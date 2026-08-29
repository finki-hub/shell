import {
  type ArchiveFormat,
  type ClientMessage,
  WS_PROTOCOL,
  WS_PROTOCOL_V2,
  WS_TICKET_PREFIX,
} from '@shell/protocol';
import { FitAddon } from '@xterm/addon-fit';
import { Terminal } from '@xterm/xterm';

import { createArchiveTracker } from '@/lib/archive-tracker';
import { PRESENCE_INTERVAL_MS } from '@/lib/constants';
import {
  dispatchServerText,
  type SessionCallbacks,
} from '@/lib/lab-session-protocol';

const TERMINAL_THEME = {
  background: '#0a0a0a',
  cursor: '#22c55e',
  foreground: '#f2f2f2',
  selectionBackground: '#22c55e44',
};

export type LabSessionCallbacks = Omit<
  SessionCallbacks,
  'onArchiveGrant' | 'onReady'
> & {
  readonly onArchiveGrant: (token: string, format: ArchiveFormat) => void;
};

export type SessionHandle = {
  readonly answerChallenge: (solution: string) => void;
  readonly focus: () => void;
  readonly refreshStorage: () => void;
  readonly requestArchive: (format: ArchiveFormat) => boolean;
  readonly teardown: () => void;
};

type ControlMessage = Exclude<ClientMessage, { readonly type: 'hello' }>;

export const createSessionHello = (
  environmentToken: null | string,
): ClientMessage => ({ environmentToken, type: 'hello' });

type StartLabSessionInput = {
  readonly callbacks: LabSessionCallbacks;
  readonly container: HTMLDivElement;
  readonly environmentToken: null | string;
  readonly ticket: string;
};

export const startLabSession = ({
  callbacks,
  container,
  environmentToken,
  ticket,
}: StartLabSessionInput): SessionHandle => {
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

  const encoder = new TextEncoder();
  const wsProtocol = location.protocol === 'https:' ? 'wss' : 'ws';
  // The ticket is carried outside the URL/history/default request line. A
  // trusted WebSocket peer still receives the credential-bearing subprotocol.
  const socket = new WebSocket(`${wsProtocol}://${location.host}/ws`, [
    WS_PROTOCOL_V2,
    WS_PROTOCOL,
    `${WS_TICKET_PREFIX}${ticket}`,
  ]);
  socket.binaryType = 'arraybuffer';
  let ready = false;
  let disposed = false;
  const archive = createArchiveTracker();

  const sendControl = (message: ControlMessage): boolean => {
    if (!ready || socket.readyState !== WebSocket.OPEN) {
      return false;
    }
    socket.send(JSON.stringify(message));
    return true;
  };

  const sendResize = () =>
    sendControl({
      cols: terminal.cols,
      rows: terminal.rows,
      type: 'resize',
    });

  socket.addEventListener('open', () => {
    socket.send(JSON.stringify(createSessionHello(environmentToken)));
  });

  const protocolCallbacks: SessionCallbacks = {
    ...callbacks,
    onArchiveGrant: (token) => {
      const grant = archive.consume(token);
      if (grant !== null) {
        callbacks.onArchiveGrant(grant.token, grant.format);
      }
    },
    onReady: () => {
      ready = true;
      fitAddon.fit();
      sendResize();
      terminal.focus();
    },
  };

  socket.addEventListener(
    'message',
    (event: MessageEvent<ArrayBuffer | string>) => {
      if (disposed) {
        return;
      }
      if (typeof event.data !== 'string') {
        terminal.write(new Uint8Array(event.data));
        return;
      }

      const close = dispatchServerText(event.data, protocolCallbacks, Date.now);
      if (close !== null) {
        socket.close(close.code, close.reason);
      }
    },
  );
  socket.addEventListener('close', () => {
    if (disposed) {
      return;
    }
    ready = false;
    callbacks.onStatus('ended');
    callbacks.onEnd('connection-closed');
  });

  const dataListener = terminal.onData((data) => {
    if (ready && socket.readyState === WebSocket.OPEN) {
      socket.send(encoder.encode(data));
    }
  });
  const resizeListener = terminal.onResize(() => {
    sendResize();
  });
  const observer = new ResizeObserver(() => {
    fitAddon.fit();
  });
  observer.observe(container);

  const presence = setInterval(() => {
    if (!document.hidden) {
      sendControl({ type: 'present' });
    }
  }, PRESENCE_INTERVAL_MS);

  return {
    answerChallenge: (solution) => {
      sendControl({ token: solution, type: 'challenge-response' });
    },
    focus: () => {
      terminal.focus();
    },
    refreshStorage: () => {
      sendControl({ type: 'storage-refresh' });
    },
    requestArchive: (format) => {
      if (!ready || socket.readyState !== WebSocket.OPEN) {
        return false;
      }
      if (!archive.request(format)) {
        return false;
      }
      socket.send(
        JSON.stringify({ type: 'archive-request' } satisfies ClientMessage),
      );
      return true;
    },
    teardown: () => {
      if (disposed) {
        return;
      }
      disposed = true;
      clearInterval(presence);
      observer.disconnect();
      dataListener.dispose();
      resizeListener.dispose();
      socket.close();
      terminal.dispose();
    },
  };
};
