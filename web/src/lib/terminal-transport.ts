import { FitAddon } from '@xterm/addon-fit';
import { Terminal } from '@xterm/xterm';
import { z } from 'zod';

import { captureProtocolFailure } from '@/lib/analytics';
import {
  classifyHubFailure,
  classifySocketClose,
  classifyTerminalFailure,
  type SessionEnd,
} from '@/lib/end-reasons';
import {
  decodeJson,
  httpFailure,
  type HubFailure,
  type LabSession,
  mintUrlToken,
  sendJson,
} from '@/lib/hub-api';

export type StartTerminalInput = {
  readonly callbacks: TerminalCallbacks;
  readonly container: HTMLDivElement;
  readonly session: LabSession;
};

export type TerminalCallbacks = {
  readonly onEnd: (end: SessionEnd) => void;
  /** A keystroke. The storage poller uses it as its only activity signal. */
  readonly onInput?: () => void;
  readonly onOutput?: () => void;
  readonly onStatus: (status: TerminalStatus) => void;
};

export type TerminalHandle = {
  readonly fit: () => void;
  readonly focus: () => void;
  /** When the last frame arrived. No ping is ever sent to refresh it. */
  readonly lastActivityAt: () => number;
  readonly teardown: () => void;
};

export type TerminalStatus =
  'connecting' | 'ended' | 'reconnecting' | 'running';

type CreateResult =
  | { readonly error: HubFailure; readonly ok: false }
  | { readonly name: string; readonly ok: true };

type ListResult =
  | { readonly error: HubFailure; readonly ok: false }
  | { readonly names: readonly string[]; readonly ok: true };

const TerminalSchema = z.object({ name: z.string() });

const TerminalListSchema = z.array(TerminalSchema);

// terminado's entire server vocabulary: `["setup", {}]` once on open,
// `["stdout", s]` for pty output, `["disconnect", code]` when the pty dies.
// Anything else arriving on this socket is not terminado, and is treated so.
const ServerFrameSchema = z.union([
  z.tuple([z.literal('setup'), z.object({})]),
  z.tuple([z.literal('stdout'), z.string()]),
  z.tuple([z.literal('disconnect'), z.number()]),
]);

// One tab, one shell. `sessionStorage` is the exact lifetime wanted: it
// survives a reload and is discarded with the tab, so two tabs never fight
// over one pty and a refresh does not orphan the one it had.
const TERMINAL_KEY = 'lab.terminal';

const PROTOCOL_CLOSE_CODE = 1_002;
// A pty is not a canvas: nothing sane asks for a terminal this large, and
// `set_size` reaches an ioctl, so the numbers are bounded here rather than
// trusted from a resize observer that happened to see a transient layout.
const MAX_ROWS = 512;
const MAX_COLS = 1_024;
// Well under the container's 1 MiB frame cap, so a large paste never arrives
// as one message the server refuses.
const STDIN_WINDOW = 64 * 1_024;
// Five attempts over half a minute. A hub restart or a proxy reload is back
// inside that; anything longer is not a blip and the page should say so.
const RECONNECT_DELAYS_MS: readonly number[] = [
  1_000, 2_000, 4_000, 8_000, 15_000,
];

const TERMINAL_THEME = {
  background: '#0a0a0a',
  cursor: '#22c55e',
  foreground: '#f2f2f2',
  selectionBackground: '#22c55e44',
};

const userRoot = (session: LabSession): string =>
  `/user/${encodeURIComponent(session.username)}`;

// Storage can be unavailable (private browsing, a blocked third-party frame).
// Losing the name costs a shell, never the page, so every access degrades to
// "this tab does not remember one".
const recallTerminal = (): null | string => {
  try {
    return sessionStorage.getItem(TERMINAL_KEY);
  } catch (error) {
    if (error instanceof Error) {
      return null;
    }
    throw error;
  }
};

const rememberTerminal = (name: string): void => {
  try {
    sessionStorage.setItem(TERMINAL_KEY, name);
  } catch (error) {
    if (!(error instanceof Error)) {
      throw error;
    }
  }
};

const forgetTerminal = (): void => {
  try {
    sessionStorage.removeItem(TERMINAL_KEY);
  } catch (error) {
    if (!(error instanceof Error)) {
      throw error;
    }
  }
};

export const clampDimension = (value: number, limit: number): number =>
  Math.min(limit, Math.max(1, Math.trunc(value)));

export const listTerminals = async (
  session: LabSession,
  signal?: AbortSignal,
): Promise<ListResult> => {
  const result = await sendJson({
    method: 'GET',
    session,
    signal,
    url: `${userRoot(session)}/api/terminals`,
  });
  if (result.kind !== 'response') {
    return { error: { kind: result.kind }, ok: false };
  }
  if (result.status !== 200) {
    return { error: httpFailure(result.status, result.text), ok: false };
  }
  const model = decodeJson(TerminalListSchema, result.text);
  return model === null
    ? { error: { kind: 'malformed' }, ok: false }
    : { names: model.map((entry) => entry.name), ok: true };
};

// The body must be an empty JSON object: terminado's root handler reads it as
// the keyword arguments for `TerminalManager.create`.
export const createTerminal = async (
  session: LabSession,
  signal?: AbortSignal,
): Promise<CreateResult> => {
  const result = await sendJson({
    body: {},
    method: 'POST',
    session,
    signal,
    url: `${userRoot(session)}/api/terminals`,
  });
  if (result.kind !== 'response') {
    return { error: { kind: result.kind }, ok: false };
  }
  if (result.status !== 200 && result.status !== 201) {
    return { error: httpFailure(result.status, result.text), ok: false };
  }
  const model = decodeJson(TerminalSchema, result.text);
  return model === null
    ? { error: { kind: 'malformed' }, ok: false }
    : { name: model.name, ok: true };
};

// Attach when this tab's own shell is still there, create otherwise. An attach
// is a WebSocket and nothing else — no POST — because terminado replays a
// pty's scrollback to every client that joins it, so a reload lands back in
// the same shell with the same history rather than on a blank prompt.
export const resolveTerminal = async (
  session: LabSession,
  signal?: AbortSignal,
): Promise<CreateResult> => {
  const remembered = recallTerminal();
  if (remembered !== null) {
    const listed = await listTerminals(session, signal);
    if (!listed.ok) {
      return { error: listed.error, ok: false };
    }
    if (listed.names.includes(remembered)) {
      return { name: remembered, ok: true };
    }
    // Culled after 15 idle minutes, or exited while the tab was away.
    forgetTerminal();
  }
  const created = await createTerminal(session, signal);
  if (created.ok) {
    rememberTerminal(created.name);
  }
  return created;
};

export const deleteTerminal = async (
  session: LabSession,
  name: string,
): Promise<void> => {
  await sendJson({
    method: 'DELETE',
    session,
    url: `${userRoot(session)}/api/terminals/${encodeURIComponent(name)}`,
  });
};

// `keepalive` because this runs from `pagehide`, after the page has stopped
// being able to wait for anything. `sendJson` is bypassed for that one flag.
export const releaseTerminal = (session: LabSession, name: string): void => {
  const url = `${userRoot(session)}/api/terminals/${encodeURIComponent(name)}`;
  void (async () => {
    try {
      await fetch(url, {
        headers: { Authorization: `token ${session.apiToken}` },
        keepalive: true,
        method: 'DELETE',
      });
    } catch {
      // Fire-and-forget: the document that asked is already going away, and
      // the server's 15-minute idle cull is the backstop for whatever the
      // browser refused to send.
    }
  })();
};

// Closing a tab must not leave its pty behind: `LAB_MAX_TERMINALS` is a
// per-container budget, and an abandoned shell holds a slot until the server's
// 15-minute idle cull notices. `pagehide` is the last event a document
// reliably gets and it cannot tell a close from a reload, so the delete is
// unconditional — and the reattach in `resolveTerminal` is what makes that
// safe rather than wasteful. Whenever the delete does not land (a crash, a
// force-quit, an offline tab, a browser that drops the keepalive request) the
// next load finds the terminal still listed and joins it, scrollback and all;
// when it does land there is nothing to join and a fresh shell is created,
// which is what a reload would have got in any case.
const releaseOnPagehide =
  (session: LabSession): (() => void) =>
  (): void => {
    const name = recallTerminal();
    if (name === null) return;
    forgetTerminal();
    releaseTerminal(session, name);
  };

// The credential rides in the query string because a WebSocket handshake
// cannot carry an Authorization header. It is never the SPA's own day-long
// token: `urlToken` is a 60-second, `access:servers!user=<u>`-only token minted
// for this handshake alone, so a URL that leaks into a log or a history entry
// is worthless a minute later.
export const terminalSocketUrl = (
  session: LabSession,
  name: string,
  urlToken: string,
): string => {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  return (
    `${scheme}://${location.host}${userRoot(session)}` +
    `/terminals/websocket/${encodeURIComponent(name)}` +
    `?token=${encodeURIComponent(urlToken)}`
  );
};

export const startTerminal = ({
  callbacks,
  container,
  session,
}: StartTerminalInput): TerminalHandle => {
  const terminal = new Terminal({
    cursorBlink: true,
    fontFamily:
      'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
    fontSize: 14,
    scrollback: 5_000,
    theme: TERMINAL_THEME,
  });
  const fitAddon = new FitAddon();
  terminal.loadAddon(fitAddon);
  terminal.open(container);
  fitAddon.fit();

  // Liveness is observed, never provoked: the page sends no heartbeat, because
  // terminado stamps `last_activity` on every frame it writes and a keepalive
  // from here would keep an abandoned container alive forever. Tornado's own
  // `ws_ping_interval` on the server side holds the socket open.
  const live = {
    activityAt: Date.now(),
    disposed: false,
    // Bumped by teardown. Compared rather than reading `disposed` after an
    // await, because a number is the one thing the compiler cannot narrow away
    // across one.
    epoch: 0,
    ready: false,
    socket: null as null | WebSocket,
    timer: null as null | ReturnType<typeof setTimeout>,
  };

  const send = (frame: readonly unknown[]): void => {
    if (!live.ready || live.socket?.readyState !== WebSocket.OPEN) return;
    live.socket.send(JSON.stringify(frame));
  };

  const sendSize = (): void => {
    send([
      'set_size',
      clampDimension(terminal.rows, MAX_ROWS),
      clampDimension(terminal.cols, MAX_COLS),
    ]);
  };

  const sendStdin = (data: string): void => {
    callbacks.onInput?.();
    for (let offset = 0; offset < data.length; offset += STDIN_WINDOW) {
      send(['stdin', data.slice(offset, offset + STDIN_WINDOW)]);
    }
  };

  const finish = (end: SessionEnd): void => {
    if (live.disposed) return;
    live.ready = false;
    callbacks.onStatus('ended');
    callbacks.onEnd(end);
  };

  const sleep = (ms: number): Promise<void> =>
    new Promise((resolve) => {
      live.timer = setTimeout(resolve, ms);
    });

  // Resolves when the socket closes, saying which kind of close it was: a pty
  // that exited, or a connection that dropped.
  const runSocket = (
    name: string,
    urlToken: string,
  ): Promise<'closed' | 'exited'> =>
    new Promise((resolve) => {
      const opened = new WebSocket(terminalSocketUrl(session, name, urlToken));
      live.socket = opened;
      const closing = { exited: false };
      opened.addEventListener('message', (event: MessageEvent<unknown>) => {
        if (live.disposed) return;
        live.activityAt = Date.now();
        const frame =
          typeof event.data === 'string'
            ? decodeJson(ServerFrameSchema, event.data)
            : null;
        if (frame === null) {
          captureProtocolFailure();
          opened.close(PROTOCOL_CLOSE_CODE, 'invalid server message');
          return;
        }
        switch (frame[0]) {
          case 'disconnect':
            // The pty is gone, so the name this tab remembers names nothing.
            forgetTerminal();
            closing.exited = true;
            opened.close();
            break;
          case 'setup':
            live.ready = true;
            fitAddon.fit();
            sendSize();
            terminal.focus();
            callbacks.onStatus('running');
            break;
          case 'stdout':
            terminal.write(frame[1]);
            callbacks.onOutput?.();
            break;
        }
      });
      opened.addEventListener('close', () => {
        live.ready = false;
        if (live.socket === opened) live.socket = null;
        resolve(closing.exited ? 'exited' : 'closed');
      });
    });

  // One attempt at a live shell: find or make one, mint the handshake token,
  // run its socket, and say whether the page should try again. `finish` is a
  // no-op once torn down, so a late answer to an abandoned attempt reports
  // nothing.
  const attemptOnce = async (epoch: number): Promise<'ended' | 'retry'> => {
    const resolved = await resolveTerminal(session);
    if (!resolved.ok) {
      if (live.epoch === epoch) finish(classifyTerminalFailure(resolved.error));
      return 'ended';
    }
    const minted = await mintUrlToken(session);
    if (minted.kind !== 'token') {
      if (live.epoch === epoch) finish(classifyHubFailure(minted.failure));
      return 'ended';
    }
    const outcome = await runSocket(resolved.name, minted.token);
    if (outcome === 'exited') {
      finish(classifySocketClose(true));
      return 'ended';
    }
    return live.epoch === epoch ? 'retry' : 'ended';
  };

  // A bare close is a lost connection, not a finished shell, and the container
  // is still there: the next attempt reattaches to the very same pty, which is
  // why nothing deletes the terminal between retries.
  const run = async (): Promise<void> => {
    const epoch = live.epoch;
    callbacks.onStatus('connecting');
    if ((await attemptOnce(epoch)) === 'ended') return;
    for (const wait of RECONNECT_DELAYS_MS) {
      callbacks.onStatus('reconnecting');
      await sleep(wait);
      if (live.epoch !== epoch) return;
      if ((await attemptOnce(epoch)) === 'ended') return;
    }
    finish(classifySocketClose(false));
  };

  const release = releaseOnPagehide(session);
  const dataListener = terminal.onData(sendStdin);
  const resizeListener = terminal.onResize(sendSize);
  const observer = new ResizeObserver(() => {
    fitAddon.fit();
  });
  observer.observe(container);
  addEventListener('pagehide', release);
  void run();

  return {
    fit: () => {
      fitAddon.fit();
    },
    focus: () => {
      terminal.focus();
    },
    lastActivityAt: () => live.activityAt,
    teardown: () => {
      if (live.disposed) return;
      live.disposed = true;
      live.epoch += 1;
      if (live.timer !== null) clearTimeout(live.timer);
      removeEventListener('pagehide', release);
      observer.disconnect();
      dataListener.dispose();
      resizeListener.dispose();
      live.socket?.close();
      terminal.dispose();
    },
  };
};
