import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  type LabSessionCallbacks,
  startLabSession,
} from '@/lib/lab-session-transport';

const state = vi.hoisted(() => ({
  fit: vi.fn(),
  focus: vi.fn(),
  terminalDispose: vi.fn(),
  terminalWrite: vi.fn(),
}));
vi.mock('@xterm/addon-fit', () => ({
  FitAddon: class {
    public fit = state.fit;
  },
}));
vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    public cols = 80;
    public dispose = state.terminalDispose;
    public focus = state.focus;
    public loadAddon = vi.fn();
    public onData = vi.fn(() => ({ dispose: vi.fn() }));
    public onResize = vi.fn(() => ({ dispose: vi.fn() }));
    public open = vi.fn();
    public rows = 24;
    public write = state.terminalWrite;
  },
}));
type SocketEvent = { readonly data?: ArrayBuffer | string };
type SocketListener = (event: SocketEvent) => void;

const observerInstances: FakeObserver[] = [];
const socketInstances: FakeSocket[] = [];

const currentObserver = (): FakeObserver => {
  const observer = observerInstances.at(-1);
  if (observer === undefined) {
    throw new Error('Observer was not constructed');
  }
  return observer;
};

const currentSocket = (): FakeSocket => {
  const socket = socketInstances.at(-1);
  if (socket === undefined) {
    throw new Error('Socket was not constructed');
  }
  return socket;
};

class FakeObserver {
  public readonly disconnect = vi.fn();
  public readonly observe = vi.fn();

  public constructor() {
    observerInstances.push(this);
  }
}

class FakeSocket {
  public static readonly OPEN = 1;
  public binaryType = '';
  public readonly close = vi.fn();
  public readyState = FakeSocket.OPEN;
  public readonly sent: Array<ArrayBufferView | string> = [];
  private readonly listeners: Partial<Record<string, SocketListener[]>> = {};

  public constructor(
    public readonly url: string,
    public readonly protocols: readonly string[],
  ) {
    socketInstances.push(this);
  }

  public addEventListener(type: string, listener: SocketListener): void {
    let listeners = this.listeners[type];
    if (listeners === undefined) {
      listeners = [];
      this.listeners[type] = listeners;
    }
    listeners.push(listener);
  }

  public emit(type: string, event: SocketEvent = {}): void {
    const listeners = this.listeners[type] ?? [];
    for (const listener of listeners) {
      listener(event);
    }
  }

  public send(data: ArrayBufferView | string): void {
    this.sent.push(data);
  }
}

const record = (trace: string[], entry: string): void => {
  trace.push(entry);
};

const callbacks = (trace: string[]): LabSessionCallbacks => ({
  onArchiveGrant: (token, format) => {
    record(trace, `grant:${token}:${format}`);
  },
  onChallenge: () => {
    record(trace, 'challenge');
  },
  onChallengeCleared: () => {
    record(trace, 'challenge-cleared');
  },
  onEnd: (reason) => {
    record(trace, `end:${reason}`);
  },
  onEnvironment: () => {
    record(trace, 'environment');
  },
  onEnvironmentExpired: () => {
    record(trace, 'retire');
  },
  onEnvironmentToken: () => {
    record(trace, 'environment-token');
  },
  onExpiresAt: () => {
    record(trace, 'expires-at');
  },
  onExpiryWarning: () => {
    record(trace, 'expiry-warning');
  },
  onStatus: (status) => {
    record(trace, `status:${status}`);
  },
  onStorage: () => {
    record(trace, 'storage');
  },
  onToken: () => {
    record(trace, 'token');
  },
});

describe('lab session transport', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal('WebSocket', FakeSocket);
    vi.stubGlobal('ResizeObserver', FakeObserver);
    vi.stubGlobal(
      'HTMLDivElement',
      class {
        public readonly nodeType = 1;
      },
    );
    vi.stubGlobal('location', { host: 'labs.test', protocol: 'https:' });
    vi.stubGlobal('document', { hidden: false });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('preserves socket and terminal lifecycle ordering', () => {
    // Given
    const trace: string[] = [];
    const handle = startLabSession({
      callbacks: callbacks(trace),
      container: new HTMLDivElement(),
      environmentToken: 'E0',
      ticket: 'ticket',
    });
    const socket = currentSocket();
    vi.stubGlobal('localStorage', { getItem: () => 'E1' });

    // When
    handle.refreshStorage();
    socket.emit('open');
    socket.emit('message', {
      data: JSON.stringify({
        environmentExpiresAt: 2,
        environmentToken: 'environment',
        resumed: false,
        token: 'session',
        type: 'ready',
      }),
    });
    handle.refreshStorage();
    handle.teardown();
    handle.teardown();

    // Then
    expect(socket.url).toBe('wss://labs.test/ws');
    expect(socket.protocols).toEqual(['lab.v2', 'lab.v1', 'lab.ticket.ticket']);
    expect(socket.sent.slice(0, 3)).toEqual([
      JSON.stringify({ environmentToken: 'E0', type: 'hello' }),
      JSON.stringify({ cols: 80, rows: 24, type: 'resize' }),
      JSON.stringify({ type: 'storage-refresh' }),
    ]);
    expect(trace).toEqual([
      'environment-token',
      'token',
      'environment',
      'status:running',
      'expires-at',
    ]);
    expect([
      currentObserver().disconnect.mock.calls.length,
      socket.close.mock.calls.length,
      state.terminalDispose.mock.calls.length,
    ]).toEqual([1, 1, 1]);
  });

  it('closes malformed text without breaking binary output', () => {
    // Given
    const trace: string[] = [];
    startLabSession({
      callbacks: callbacks(trace),
      container: new HTMLDivElement(),
      environmentToken: 'environment',
      ticket: 'ticket',
    });
    const socket = currentSocket();
    const bytes = new Uint8Array([1, 2, 3]).buffer;

    // When
    socket.emit('message', { data: bytes });
    socket.emit('message', { data: '{"type":"future","token":"secret"}' });

    // Then
    expect(state.terminalWrite).toHaveBeenCalledWith(new Uint8Array(bytes));
    expect(socket.close).toHaveBeenCalledWith(1_002, 'invalid server message');
    expect(trace).toEqual([]);
  });

  it('correlates sequential archive grants and rejects overlap', () => {
    // Given
    const trace: string[] = [];
    const handle = startLabSession({
      callbacks: callbacks(trace),
      container: new HTMLDivElement(),
      environmentToken: 'environment',
      ticket: 'ticket',
    });
    const socket = currentSocket();
    socket.emit('message', {
      data: JSON.stringify({
        environmentExpiresAt: 2,
        environmentToken: 'environment',
        resumed: false,
        token: 'session',
        type: 'ready',
      }),
    });

    // When
    const zip = handle.requestArchive('zip');
    const overlap = handle.requestArchive('tgz');
    socket.emit('message', {
      data: JSON.stringify({
        expiresInMs: 1,
        token: 'zip-token',
        type: 'archive-grant',
      }),
    });
    const tgz = handle.requestArchive('tgz');
    socket.emit('message', {
      data: JSON.stringify({
        expiresInMs: 1,
        token: 'tgz-token',
        type: 'archive-grant',
      }),
    });

    // Then
    expect([zip, overlap, tgz]).toEqual([true, false, true]);
    expect(trace.filter((entry) => entry.startsWith('grant:'))).toEqual([
      'grant:zip-token:zip',
      'grant:tgz-token:tgz',
    ]);
  });
});
