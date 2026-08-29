import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  chrome,
  type FakeWindow,
  installFakeTurnstileWindow,
} from './support/fake-turnstile-window';

const flush = async (): Promise<void> => {
  for (let turn = 0; turn < 30; turn += 1) await Promise.resolve();
};

const isTimerCallback = (value: unknown): value is () => void =>
  typeof value === 'function';

const requireBrowserReceiver = (receiver: unknown): void => {
  if (receiver !== undefined && receiver !== globalThis) {
    throw new TypeError('Illegal invocation');
  }
};

describe('Turnstile attempt spacing', () => {
  let fake: FakeWindow;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.resetModules();
    fake = installFakeTurnstileWindow();
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          Response.json({
            challengeGraceMs: 300_000,
            challengeIntervalMs: 3_600_000,
            sitekey: 'site-key',
          }),
        ),
      ),
    );
    vi.stubGlobal('turnstile', fake.api);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('aborts during the reserved spacing delay without rendering', async () => {
    const { solveChallenge } = await import('@/lib/turnstile');
    const first = solveChallenge('session_start', { chrome, issued: 'first' });
    await flush();
    fake.renders[0]?.callback('token');
    await first;
    const controller = new AbortController();
    const second = solveChallenge('session_start', {
      chrome,
      issued: 'second',
      signal: controller.signal,
    });
    await flush();
    controller.abort();
    await expect(second).resolves.toBeNull();
    expect(fake.api.render).toHaveBeenCalledTimes(1);
  });

  it('starts an overlapping slow attempt after one gap without waiting for its predecessor', async () => {
    const { solveChallenge } = await import('@/lib/turnstile');
    const first = solveChallenge('session_start', { chrome, issued: 'first' });
    await flush();
    const second = solveChallenge('session_start', {
      chrome,
      issued: 'second',
    });
    await flush();

    await vi.advanceTimersByTimeAsync(999);
    expect(fake.api.render).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fake.api.render).toHaveBeenCalledTimes(2);

    fake.renders[0]?.callback('first-token');
    fake.renders[1]?.callback('second-token');
    await expect(Promise.all([first, second])).resolves.toEqual([
      { nonce: 'first', token: 'first-token' },
      { nonce: 'second', token: 'second-token' },
    ]);
  });

  it('compacts repeated cancellations and permits a resumed attempt', async () => {
    const { createAttemptSpacing } = await import('@/lib/turnstile-spacing');
    const spacing = createAttemptSpacing(1_000);
    const first = new AbortController();
    await expect(spacing.acquire(first.signal)).resolves.toBe('ready');
    const controllers = Array.from({ length: 10 }, () => new AbortController());
    const cancelled = controllers.map((controller) =>
      spacing.acquire(controller.signal),
    );
    const expected = controllers.map(() => 'aborted');
    for (const controller of controllers) controller.abort();

    await expect(Promise.all(cancelled)).resolves.toEqual(expected);
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(1_000);
    const resumed = new AbortController();
    await expect(spacing.acquire(resumed.signal)).resolves.toBe('ready');
    expect(vi.getTimerCount()).toBe(0);
  });

  it('uses browser timer functions without an object receiver', async () => {
    const nativeSetTimer = setTimeout;
    const nativeClearTimer = clearTimeout;
    const timerHandles = new Map<unknown, ReturnType<typeof setTimeout>>();
    const receiverSensitiveSetTimer = new Proxy(nativeSetTimer, {
      apply: (_target, receiver, argumentsList) => {
        requireBrowserReceiver(receiver);
        const callback: unknown = argumentsList[0];
        const delayMs: unknown = argumentsList[1];
        if (
          !isTimerCallback(callback) ||
          (delayMs !== undefined && typeof delayMs !== 'number')
        ) {
          throw new TypeError('Invalid timer arguments');
        }
        const timer = nativeSetTimer(callback, delayMs);
        timerHandles.set(timer, timer);
        return timer;
      },
    });
    const receiverSensitiveClearTimer = new Proxy(nativeClearTimer, {
      apply: (_target, receiver, argumentsList) => {
        requireBrowserReceiver(receiver);
        const key: unknown = argumentsList[0];
        const timer = timerHandles.get(key);
        if (timer === undefined) {
          throw new TypeError('Invalid timer handle');
        }
        nativeClearTimer(timer);
        timerHandles.delete(key);
      },
    });
    vi.stubGlobal('setTimeout', receiverSensitiveSetTimer);
    vi.stubGlobal('clearTimeout', receiverSensitiveClearTimer);

    try {
      vi.resetModules();
      const { createAttemptSpacing } = await import('@/lib/turnstile-spacing');
      const spacing = createAttemptSpacing(1_000);
      const first = new AbortController();
      await expect(spacing.acquire(first.signal)).resolves.toBe('ready');
      const delayed = new AbortController();
      const cancelled = spacing.acquire(delayed.signal);

      delayed.abort();

      await expect(cancelled).resolves.toBe('aborted');
      expect(vi.getTimerCount()).toBe(0);
      await vi.advanceTimersByTimeAsync(1_000);
      const resumed = new AbortController();
      await expect(spacing.acquire(resumed.signal)).resolves.toBe('ready');
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
