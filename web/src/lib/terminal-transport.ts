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
  readonly onInput?: () => void;
  readonly onOutput?: () => void;
  readonly onStatus: (status: TerminalStatus) => void;
};

export type TerminalHandle = {
  readonly clear: () => void;
  readonly fit: () => void;
  readonly focus: () => void;
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

// Accept only terminado's setup, stdout, and disconnect frames.
const ServerFrameSchema = z.union([
  z.tuple([z.literal('setup'), z.object({})]),
  z.tuple([z.literal('stdout'), z.string()]),
  z.tuple([z.literal('disconnect'), z.number()]),
]);

// Keep one terminal per tab; pagehide clears accepted refresh/close, while
// unclean restores may retain the remembered pty.
const TERMINAL_KEY = 'lab.terminal';

const PROTOCOL_CLOSE_CODE = 1_002;
// Bound dimensions before passing them to the pty ioctl.
const MAX_ROWS = 512;
const MAX_COLS = 1_024;
// Keep stdin frames below the 1 MiB server cap.
const STDIN_WINDOW = 64 * 1_024;
// Five retries span about 30 seconds; longer outages are terminal.
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

// terminado expects an empty object for TerminalManager.create.
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

// Recovered ptys attach over WebSocket; otherwise create one.
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
    // Forget ptys culled after 15 idle minutes or exited while hidden.
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

// pagehide requires fetch keepalive; sendJson cannot carry that option.
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
      // Browser shutdown may drop the request; server idle culling cleans up.
    }
  })();
};

// Clear remembered state and unconditionally delete on close or refresh; idle
// culling handles a dropped keepalive request.
const releaseOnPagehide =
  (session: LabSession): (() => void) =>
  (): void => {
    const name = recallTerminal();
    if (name === null) return;
    forgetTerminal();
    releaseTerminal(session, name);
  };

// WebSocket handshakes cannot carry Authorization; use the 60-second,
// servers-only token and never the SPA token in the URL.
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

  // Do not send heartbeats: frames update activity and heartbeats prevent
  // culling; the server's ws_ping_interval keeps the socket alive.
  const live = {
    activityAt: Date.now(),
    disposed: false,
    // Teardown increments the epoch to invalidate late async work.
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

  // Resolve whether close followed pty exit or connection loss.
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
            // An exited pty cannot be recovered.
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

  // Reconnect on a connection close; the pty remains and is not deleted.
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
    clear: () => {
      terminal.reset();
    },
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
      container.replaceChildren();
    },
  };
};
