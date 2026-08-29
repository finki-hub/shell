import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useLabSession } from '@/hooks/useLabSession';
import { type LabSession } from '@/lib/hub-api';

type Cleanup = () => void;
type Effect = () => Cleanup | undefined;

const runtime = vi.hoisted(() => ({
  cursor: 0,
  effects: [] as Effect[],
  refs: [] as Array<{ current: unknown }>,
  states: [] as unknown[],
}));

const services = vi.hoisted(() => ({
  createStoragePoller: vi.fn(),
  discardEnvironment: vi.fn(),
  forgetEnvironmentToken: vi.fn(),
  login: vi.fn(),
  readConfig: vi.fn(),
  readOrMintEnvironmentToken: vi.fn(),
  readStorage: vi.fn(),
  restartServer: vi.fn(),
  solveChallenge:
    vi.fn<
      (
        action: 'env_create',
        input: { readonly issued: string },
      ) => Promise<null | { readonly nonce: string; readonly token: string }>
    >(),
  spawnServer: vi.fn(),
  startTerminal: vi.fn(),
}));

const poller = vi.hoisted(() => ({
  markActive: vi.fn(),
  refresh: vi.fn(),
  setTransferring: vi.fn(),
  start: vi.fn(),
  stop: vi.fn(),
}));

const terminal = vi.hoisted(() => ({
  fit: vi.fn(),
  focus: vi.fn(),
  lastActivityAt: vi.fn(() => 0),
  teardown: vi.fn(),
}));

vi.mock('react', () => ({
  useCallback: (callback: unknown): unknown => callback,
  useEffect: (effect: Effect): void => {
    runtime.effects.push(effect);
  },
  useRef: (initial: unknown): { current: unknown } => {
    const index = runtime.cursor;
    runtime.cursor += 1;
    const existing = runtime.refs[index];
    if (existing !== undefined) return existing;
    const created = { current: initial };
    runtime.refs[index] = created;
    return created;
  },
  useState: (
    initial: unknown,
  ): readonly [unknown, (value: unknown) => void] => {
    const index = runtime.cursor;
    runtime.cursor += 1;
    if (runtime.states[index] === undefined) {
      runtime.states[index] =
        typeof initial === 'function'
          ? Reflect.apply(initial, undefined, [])
          : initial;
    }
    const setValue = (value: unknown): void => {
      runtime.states[index] =
        typeof value === 'function'
          ? Reflect.apply(value, undefined, [runtime.states[index]])
          : value;
    };
    return [runtime.states[index], setValue];
  },
}));

vi.mock('@/hooks/useLanguage', () => ({
  useLanguage: () => ({
    t: {
      session: {
        challengeStartBody: 'body',
        challengeTitle: 'title',
        challengeVerifying: 'verifying',
      },
    },
  }),
}));
vi.mock('@/lib/config', () => ({ readConfig: services.readConfig }));
vi.mock('@/lib/environment', () => ({
  discardEnvironment: services.discardEnvironment,
  forgetEnvironmentToken: services.forgetEnvironmentToken,
  readOrMintEnvironmentToken: services.readOrMintEnvironmentToken,
}));
vi.mock('@/lib/hub-api', () => ({
  login: services.login,
  restartServer: services.restartServer,
  spawnServer: services.spawnServer,
}));
vi.mock('@/lib/storage-api', () => ({
  createStoragePoller: services.createStoragePoller,
  readStorage: services.readStorage,
}));
vi.mock('@/lib/terminal-transport', () => ({
  startTerminal: services.startTerminal,
}));
vi.mock('@/lib/turnstile', () => ({
  solveChallenge: services.solveChallenge,
}));

const SESSION: LabSession = {
  apiToken: 'api-token',
  maxTerminals: 2,
  tokenExpiresAt: '2099-01-01T00:00:00Z',
  username: 'user-name',
};

const renderHook = (): ReturnType<typeof useLabSession> => {
  runtime.cursor = 0;
  return useLabSession();
};

const flush = async (): Promise<void> => {
  for (let turn = 0; turn < 30; turn += 1) await Promise.resolve();
};

const startHook = async (): Promise<Cleanup | undefined> => {
  const hook = renderHook();
  hook.containerRef.current = {} as HTMLDivElement;
  const cleanup = runtime.effects.at(-1)?.();
  await flush();
  return cleanup;
};

describe('useLabSession bootstrap', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    runtime.cursor = 0;
    runtime.effects.length = 0;
    runtime.refs.length = 0;
    runtime.states.length = 0;
    services.createStoragePoller.mockReturnValue(poller);
    services.readConfig.mockResolvedValue({ sitekey: null });
    services.readOrMintEnvironmentToken.mockReturnValue({
      intent: 'resume',
      token: 'stored-token',
    });
    services.login.mockResolvedValue({
      created: false,
      kind: 'session',
      session: SESSION,
    });
    services.spawnServer.mockResolvedValue({ kind: 'ready' });
    services.restartServer.mockResolvedValue({ kind: 'ready' });
    services.solveChallenge.mockResolvedValue({
      nonce: 'nonce',
      token: 'turnstile-token',
    });
    services.startTerminal.mockReturnValue(terminal);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('resumes without solving a creation challenge', async () => {
    const cleanup = await startHook();

    expect(services.solveChallenge).not.toHaveBeenCalled();
    expect(services.login).toHaveBeenCalledWith(
      expect.objectContaining({
        intent: 'resume',
        token: 'stored-token',
        turnstile: null,
      }),
    );
    expect(services.spawnServer).toHaveBeenCalledWith(
      SESSION,
      expect.any(AbortSignal),
    );
    expect(services.startTerminal).toHaveBeenCalledOnce();
    cleanup?.();
  });

  it('creates without a challenge when no sitekey is configured', async () => {
    services.readOrMintEnvironmentToken.mockReturnValue({
      intent: 'create',
      token: 'new-token',
    });

    await startHook();

    expect(services.solveChallenge).not.toHaveBeenCalled();
    expect(services.login).toHaveBeenCalledWith(
      expect.objectContaining({ intent: 'create', turnstile: null }),
    );
  });

  it('passes an issued value and the solution for a challenged create', async () => {
    services.readConfig.mockResolvedValue({ sitekey: 'site-key' });
    services.readOrMintEnvironmentToken.mockReturnValue({
      intent: 'create',
      token: 'new-token',
    });

    await startHook();

    expect(services.solveChallenge.mock.calls[0]?.[0]).toBe('env_create');
    expect(typeof services.solveChallenge.mock.calls[0]?.[1].issued).toBe(
      'string',
    );
    expect(services.login).toHaveBeenCalledWith(
      expect.objectContaining({ turnstile: 'turnstile-token' }),
    );
  });

  it('forgets a gone environment, mints once, and creates', async () => {
    services.readOrMintEnvironmentToken
      .mockReturnValueOnce({ intent: 'resume', token: 'gone-token' })
      .mockReturnValueOnce({ intent: 'create', token: 'replacement-token' });
    services.login
      .mockResolvedValueOnce({ kind: 'gone' })
      .mockResolvedValueOnce({
        created: true,
        kind: 'session',
        session: SESSION,
      });

    await startHook();
    const current = renderHook();

    expect(services.forgetEnvironmentToken).toHaveBeenCalledWith('gone-token');
    expect(services.login).toHaveBeenCalledTimes(2);
    expect(services.login).toHaveBeenLastCalledWith(
      expect.objectContaining({
        intent: 'create',
        token: 'replacement-token',
      }),
    );
    expect(current.end).toEqual({
      message: null,
      reason: 'environment-expired',
    });
  });

  it('classifies spawn capacity and synchronous start failure', async () => {
    services.spawnServer.mockResolvedValueOnce({
      failure: {
        error: null,
        kind: 'http',
        message: null,
        reason: null,
        status: 429,
      },
      kind: 'failed',
    });
    await startHook();
    expect(renderHook().end?.reason).toBe('capacity');

    runtime.cursor = 0;
    runtime.effects.length = 0;
    runtime.refs.length = 0;
    runtime.states.length = 0;
    services.spawnServer.mockResolvedValueOnce({
      kind: 'start-failed',
      message: 'hub message',
    });
    await startHook();
    expect(renderHook().end).toEqual({
      message: 'hub message',
      reason: 'start-failed',
    });
  });

  it('starts again locally when no environment was created', async () => {
    services.readConfig.mockResolvedValue({ sitekey: 'site-key' });
    services.readOrMintEnvironmentToken.mockReturnValue({
      intent: 'create',
      token: 'new-token',
    });
    services.solveChallenge.mockResolvedValue(null);
    await startHook();
    const current = renderHook();
    expect(current.end?.reason).toBe('challenge-blocked');

    await expect(current.startFresh()).resolves.toBe(true);

    expect(renderHook().end).toBeNull();
    expect(services.discardEnvironment).not.toHaveBeenCalled();
  });
});
