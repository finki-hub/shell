import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { type LabSession } from '@/lib/hub-api';
import {
  resolveTerminal,
  startTerminal,
  terminalSocketUrl,
} from '@/lib/terminal-transport';

type TerminalDouble = {
  cols: number;
  data: ((value: string) => void) | null;
  resize: (() => void) | null;
  rows: number;
};

const terminalRuntime = vi.hoisted(() => ({
  captureProtocolFailure: vi.fn(),
  fit: vi.fn(),
  instances: [] as TerminalDouble[],
}));

vi.mock('@xterm/addon-fit', () => ({
  FitAddon: class {
    fit = terminalRuntime.fit;
  },
}));

vi.mock('@xterm/xterm', () => ({
  Terminal: class implements TerminalDouble {
    cols = 2_000;
    data: ((value: string) => void) | null = null;
    dispose = vi.fn();
    focus = vi.fn();

    loadAddon = vi.fn();

    open = vi.fn();
    resize: (() => void) | null = null;
    rows = 900;
    write = vi.fn();
    constructor() {
      terminalRuntime.instances.push(this);
    }

    onData = (listener: (value: string) => void) => {
      this.data = listener;
      return { dispose: vi.fn() };
    };

    onResize = (listener: () => void) => {
      this.resize = listener;
      return { dispose: vi.fn() };
    };
  },
}));

vi.mock('@/lib/analytics', () => ({
  captureProtocolFailure: terminalRuntime.captureProtocolFailure,
}));

class ResizeObserverDouble {
  disconnect = vi.fn();
  observe = vi.fn();
}

const sockets: SocketDouble[] = [];

class SocketDouble {
  static readonly OPEN = 1;

  closeCode: number | undefined;
  closeReason: string | undefined;
  readonly close = vi.fn((code?: number, reason?: string) => {
    this.closeCode = code;
    this.closeReason = reason;
  });
  readonly listeners: Partial<Record<string, EventListener[]>> = {};
  readyState = SocketDouble.OPEN;
  readonly send = vi.fn();
  readonly url: string;

  constructor(url: string | URL) {
    this.url = String(url);
    sockets.push(this);
  }

  addEventListener(
    name: string,
    listener: EventListenerOrEventListenerObject,
  ): void {
    if (typeof listener !== 'function') return;
    const current = this.listeners[name] ?? [];
    this.listeners[name] = current;
    current.push(listener);
  }

  emitClose(): void {
    const listeners = this.listeners['close'] ?? [];
    for (const listener of listeners) {
      listener(new Event('close'));
    }
  }

  emitMessage(data: unknown): void {
    const listeners = this.listeners['message'] ?? [];
    for (const listener of listeners) {
      listener({ data } as MessageEvent);
    }
  }
}

const SESSION: LabSession = {
  apiToken: 'day-token',
  maxTerminals: 2,
  tokenExpiresAt: '2099-01-01T00:00:00Z',
  username: 'user name',
};

const REMEMBERED = 'remembered';
const TERMINAL_KEY = 'lab.terminal';
const stored = new Map<string, string>();
const pageListeners = new Map<string, EventListener>();

const response = (status: number, body = ''): Response =>
  new Response(status === 204 ? null : body, { status });

const flush = async (): Promise<void> => {
  for (let turn = 0; turn < 30; turn += 1) await Promise.resolve();
};

const requestUrl = (input: RequestInfo | URL): string => {
  if (typeof input === 'string') return input;
  return input instanceof URL ? input.href : input.url;
};

const decodeFrame = (value: unknown): readonly unknown[] => {
  if (typeof value !== 'string') return [];
  const decoded: unknown = JSON.parse(value);
  return Array.isArray(decoded) ? decoded : [];
};

describe('terminal transport', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    sockets.length = 0;
    stored.clear();
    pageListeners.clear();
    terminalRuntime.instances.length = 0;
    vi.stubGlobal('WebSocket', SocketDouble);
    vi.stubGlobal('ResizeObserver', ResizeObserverDouble);
    vi.stubGlobal('location', { host: 'shell.test', protocol: 'https:' });
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => stored.get(key) ?? null,
      removeItem: (key: string) => stored.delete(key),
      setItem: (key: string, value: string) => stored.set(key, value),
    });
    vi.stubGlobal(
      'addEventListener',
      vi.fn((name: string, listener: EventListenerOrEventListenerObject) => {
        if (typeof listener === 'function') pageListeners.set(name, listener);
      }),
    );
    vi.stubGlobal('removeEventListener', vi.fn());
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('attaches to a remembered terminal without creating one', async () => {
    stored.set(TERMINAL_KEY, REMEMBERED);
    const fetchRequest = vi.fn<typeof fetch>(() =>
      Promise.resolve(Response.json([{ name: REMEMBERED }])),
    );
    vi.stubGlobal('fetch', fetchRequest);

    await expect(resolveTerminal(SESSION)).resolves.toEqual({
      name: REMEMBERED,
      ok: true,
    });
    expect(fetchRequest).toHaveBeenCalledOnce();
    expect(fetchRequest.mock.calls[0]?.[1]?.method).toBe('GET');
  });

  it('replaces a remembered name that is no longer listed', async () => {
    stored.set(TERMINAL_KEY, 'gone');
    const fetchRequest = vi
      .fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>()
      .mockResolvedValueOnce(Response.json([]))
      .mockResolvedValueOnce(
        Response.json({ name: 'created' }, { status: 201 }),
      );
    vi.stubGlobal('fetch', fetchRequest);

    await expect(resolveTerminal(SESSION)).resolves.toEqual({
      name: 'created',
      ok: true,
    });
    expect(fetchRequest.mock.calls.map((call) => call[1]?.method)).toEqual([
      'GET',
      'POST',
    ]);
    expect(stored.get(TERMINAL_KEY)).toBe('created');
  });

  it('validates frames, clamps size, chunks stdin, and releases on pagehide', async () => {
    stored.set(TERMINAL_KEY, REMEMBERED);
    const fetchRequest = vi.fn(
      (input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === 'DELETE') return Promise.resolve(response(204));
        const url = requestUrl(input);
        if (url.endsWith('/tokens')) {
          return Promise.resolve(
            response(201, JSON.stringify({ token: 'short-token' })),
          );
        }
        return Promise.resolve(Response.json([{ name: REMEMBERED }]));
      },
    );
    vi.stubGlobal('fetch', fetchRequest);
    const onEnd = vi.fn();
    const onStatus = vi.fn();
    const handle = startTerminal({
      callbacks: { onEnd, onStatus },
      container: {} as HTMLDivElement,
      session: SESSION,
    });
    await flush();
    const socket = sockets[0];
    expect(socket?.url).toBe(
      'wss://shell.test/user/user%20name/terminals/websocket/remembered?token=short-token',
    );
    expect(socket?.url).not.toContain(SESSION.apiToken);

    socket?.emitMessage(JSON.stringify(['setup', {}]));
    terminalRuntime.instances[0]?.data?.('x'.repeat(65_537));
    const sent = socket?.send.mock.calls.map((call) => decodeFrame(call[0]));
    expect(sent?.[0]).toEqual(['set_size', 512, 1_024]);
    expect(
      sent
        ?.slice(1)
        .map((frame) => (typeof frame[1] === 'string' ? frame[1].length : 0)),
    ).toEqual([65_536, 1]);

    socket?.emitMessage(JSON.stringify(['unknown', {}]));
    expect(terminalRuntime.captureProtocolFailure).toHaveBeenCalledOnce();
    expect(socket?.close).toHaveBeenCalledWith(1_002, 'invalid server message');

    pageListeners.get('pagehide')?.(new Event('pagehide'));
    await flush();
    expect(stored.has(TERMINAL_KEY)).toBe(false);
    expect(fetchRequest.mock.calls.at(-1)?.[1]).toMatchObject({
      headers: { Authorization: 'token day-token' },
      keepalive: true,
      method: 'DELETE',
    });
    handle.teardown();
  });

  it('maps disconnect to shell-exited and clears the remembered name', async () => {
    stored.set(TERMINAL_KEY, REMEMBERED);
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>((input) =>
        Promise.resolve(
          requestUrl(input).endsWith('/tokens')
            ? response(201, JSON.stringify({ token: 'short-token' }))
            : Response.json([{ name: REMEMBERED }]),
        ),
      ),
    );
    const onEnd = vi.fn();
    const handle = startTerminal({
      callbacks: { onEnd, onStatus: vi.fn() },
      container: {} as HTMLDivElement,
      session: SESSION,
    });
    await flush();

    sockets[0]?.emitMessage(JSON.stringify(['disconnect', 0]));
    sockets[0]?.emitClose();
    await flush();

    expect(stored.has(TERMINAL_KEY)).toBe(false);
    expect(onEnd).toHaveBeenCalledWith({
      message: null,
      reason: 'shell-exited',
    });
    handle.teardown();
  });

  it('reattaches instead of creating after a socket retry', async () => {
    stored.set(TERMINAL_KEY, REMEMBERED);
    const fetchRequest = vi.fn<typeof fetch>((input) =>
      Promise.resolve(
        requestUrl(input).endsWith('/tokens')
          ? response(201, JSON.stringify({ token: 'short-token' }))
          : Response.json([{ name: REMEMBERED }]),
      ),
    );
    vi.stubGlobal('fetch', fetchRequest);
    const handle = startTerminal({
      callbacks: { onEnd: vi.fn(), onStatus: vi.fn() },
      container: {} as HTMLDivElement,
      session: SESSION,
    });
    await flush();
    sockets[0]?.emitClose();
    await flush();
    await vi.advanceTimersByTimeAsync(1_000);
    await flush();

    expect(sockets).toHaveLength(2);
    expect(
      fetchRequest.mock.calls.filter((call) => call[1]?.method === 'POST'),
    ).toHaveLength(2);
    expect(
      fetchRequest.mock.calls.filter((call) =>
        requestUrl(call[0]).endsWith('/api/terminals'),
      ),
    ).toHaveLength(2);
    handle.teardown();
  });

  it('builds a socket URL with the supplied short token', () => {
    expect(terminalSocketUrl(SESSION, 'term name', 'short token')).toBe(
      'wss://shell.test/user/user%20name/terminals/websocket/term%20name?token=short%20token',
    );
  });
});
