import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { TurnstileApi } from '@/lib/turnstile-api';

import {
  chrome,
  type FakeWindow,
  installFakeTurnstileWindow,
} from './support/fake-turnstile-window';

const flush = async (): Promise<void> => {
  for (let turn = 0; turn < 30; turn += 1) await Promise.resolve();
};

const config = {
  challengeGraceMs: 300_000,
  challengeIntervalMs: 3_600_000,
  sitekey: 'site-key',
} as const;

describe('rendered Turnstile attempt settlement', () => {
  let fake: FakeWindow;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.resetModules();
    fake = installFakeTurnstileWindow();
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(Response.json(config))),
    );
    vi.stubGlobal('turnstile', fake.api);
  });

  afterEach(() => {
    vi.doUnmock('@/lib/config');
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('removes a rendered widget exactly once when interruptions race', async () => {
    const controller = new AbortController();
    const { solveChallenge } = await import('@/lib/turnstile');
    const pending = solveChallenge('session_start', {
      chrome,
      issued: 'nonce',
      signal: controller.signal,
    });
    await flush();
    const rendered = fake.renders[0];

    controller.abort();
    rendered?.['error-callback']();
    rendered?.['timeout-callback']();
    rendered?.callback('late-token');

    await expect(pending).resolves.toBeNull();
    expect(fake.api.remove).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('returns null when aborted while config is deferred', async () => {
    const deferred = Promise.withResolvers<typeof config>();
    vi.doMock('@/lib/config', () => ({ readConfig: () => deferred.promise }));
    const controller = new AbortController();
    const { solveChallenge } = await import('@/lib/turnstile');
    const pending = solveChallenge('session_start', {
      chrome,
      issued: 'nonce',
      signal: controller.signal,
    });
    controller.abort();
    await expect(pending).resolves.toBeNull();

    deferred.resolve(config);
    await flush();
    expect([
      fake.scripts.length,
      fake.renders.length,
      fake.main.children.length,
    ]).toEqual([0, 0, 0]);
  });

  it('returns null when aborted while the API is deferred', async () => {
    vi.stubGlobal('turnstile', undefined);
    const controller = new AbortController();
    const { solveChallenge } = await import('@/lib/turnstile');
    const pending = solveChallenge('session_start', {
      chrome,
      issued: 'nonce',
      signal: controller.signal,
    });
    await flush();
    expect(fake.scripts).toHaveLength(1);

    controller.abort();
    await expect(pending).resolves.toBeNull();
    vi.stubGlobal('turnstile', fake.api);
    fake.scripts[0]?.dispatch('load');
    await flush();
    expect(fake.renders).toHaveLength(0);
    expect(fake.main.children).toHaveLength(0);
  });

  it('returns null when aborted while the nonce is deferred', async () => {
    const nonce = Promise.withResolvers<Response>();
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(Response.json(config))
      .mockReturnValueOnce(nonce.promise);
    vi.stubGlobal('fetch', fetch);
    const controller = new AbortController();
    const { solveChallenge } = await import('@/lib/turnstile');
    const pending = solveChallenge('env_create', {
      chrome,
      signal: controller.signal,
    });
    await flush();

    controller.abort();
    await expect(pending).resolves.toBeNull();
    nonce.resolve(Response.json({ nonce: 'late' }));
    await flush();
    expect(fake.renders).toHaveLength(0);
    expect(fake.main.children).toHaveLength(0);
  });

  it('aborts the underlying nonce request with the caller signal', async () => {
    const nonce = Promise.withResolvers<Response>();
    const nonceSignals: AbortSignal[] = [];
    const fetch = vi
      .fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>()
      .mockResolvedValueOnce(Response.json(config))
      .mockImplementationOnce((_input, init) => {
        if (init?.signal instanceof AbortSignal) nonceSignals.push(init.signal);
        return nonce.promise;
      });
    vi.stubGlobal('fetch', fetch);
    const controller = new AbortController();
    const { solveChallenge } = await import('@/lib/turnstile');
    const pending = solveChallenge('env_create', {
      chrome,
      signal: controller.signal,
    });
    await flush();

    expect(nonceSignals[0]).toBe(controller.signal);
    controller.abort();
    expect(nonceSignals[0]?.aborted).toBe(true);
    await expect(pending).resolves.toBeNull();
    nonce.resolve(Response.json({ nonce: 'late' }));
    await flush();
    expect(fake.renders).toHaveLength(0);
  });

  it('settles and releases the host when widget removal throws', async () => {
    fake.api.remove.mockImplementation(() => {
      throw new Error('third-party removal failure');
    });
    const controller = new AbortController();
    const { solveChallenge } = await import('@/lib/turnstile');
    const pending = solveChallenge('session_start', {
      chrome,
      issued: 'nonce',
      signal: controller.signal,
    });
    await flush();

    expect(() => fake.renders[0]?.callback('token')).not.toThrow();
    await expect(pending).resolves.toEqual({ nonce: 'nonce', token: 'token' });
    controller.abort();
    expect(fake.api.remove).toHaveBeenCalledTimes(1);
    expect(fake.main.children).toHaveLength(0);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('does not render when aborted immediately before a spacing permit', async () => {
    const { createTurnstileAttempt } = await import('@/lib/turnstile-attempt');
    const permit = Promise.withResolvers<'ready'>();
    const spacing = { acquire: () => permit.promise };
    const api: TurnstileApi = {
      execute: vi.fn(),
      remove: vi.fn(),
      render: vi.fn(() => 'widget'),
    };
    const controller = new AbortController();
    const attempt = createTurnstileAttempt(controller.signal);
    const pending = attempt.render({
      action: 'session_start',
      api,
      nonce: 'nonce',
      sitekey: 'site-key',
      spacing,
      target: document.createElement('div'),
    });

    controller.abort();
    permit.resolve('ready');

    await expect(pending).resolves.toBeNull();
    expect(api.render).not.toHaveBeenCalled();
  });

  it('settles the solve timeout with one removal and no pending timers', async () => {
    const { solveChallenge } = await import('@/lib/turnstile');
    const pending = solveChallenge('session_start', {
      chrome,
      issued: 'nonce',
    });
    await flush();

    await vi.advanceTimersByTimeAsync(60_000);

    await expect(pending).resolves.toBeNull();
    expect(fake.api.remove).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });
});
