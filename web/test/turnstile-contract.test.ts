import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  chrome,
  type FakeWindow,
  installFakeTurnstileWindow,
} from './support/fake-turnstile-window';

const configResponse = () =>
  Response.json({
    challengeGraceMs: 300_000,
    challengeIntervalMs: 3_600_000,
    sitekey: 'site-key',
  });

const flush = async (): Promise<void> => {
  for (let turn = 0; turn < 30; turn += 1) await Promise.resolve();
};

describe('custom Turnstile contract', () => {
  let fake: FakeWindow;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.resetModules();
    fake = installFakeTurnstileWindow();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('sets cdata to the issued nonce and explicitly executes', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(configResponse())),
    );
    vi.stubGlobal('turnstile', fake.api);
    const { solveChallenge } = await import('@/lib/turnstile');

    const pending = solveChallenge('session_keep', {
      chrome,
      issued: 'issued-nonce',
    });
    await flush();

    expect(fake.renders[0]).toMatchObject({
      appearance: 'interaction-only',
      cdata: 'issued-nonce',
      execution: 'execute',
    });
    expect(fake.api.execute).toHaveBeenCalledWith('widget-1');
    fake.renders[0]?.callback('token');
    await expect(pending).resolves.toEqual({
      nonce: 'issued-nonce',
      token: 'token',
    });
  });

  it('requests a nonce with the environment token and copies it to cdata', async () => {
    const fetch = vi
      .fn<(input: string, init?: RequestInit) => Promise<Response>>()
      .mockResolvedValueOnce(configResponse())
      .mockResolvedValueOnce(Response.json({ nonce: 'requested-nonce' }));
    vi.stubGlobal('fetch', fetch);
    vi.stubGlobal('turnstile', fake.api);
    const { solveChallenge } = await import('@/lib/turnstile');

    const pending = solveChallenge('env_create', {
      chrome,
      environmentToken: 'environment-token',
    });
    await flush();

    expect(fetch.mock.calls[1]?.[1]?.body).toBe(
      JSON.stringify({
        action: 'env_create',
        environmentToken: 'environment-token',
      }),
    );
    expect(fake.renders[0]?.cdata).toBe('requested-nonce');
    fake.renders[0]?.callback('token');
    await pending;
  });

  it('reveals only after interaction or the delayed reveal', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(configResponse())),
    );
    vi.stubGlobal('turnstile', fake.api);
    const interactive = vi.fn();
    const { solveChallenge } = await import('@/lib/turnstile');

    const pending = solveChallenge('session_start', {
      chrome,
      issued: 'nonce',
      onInteractive: interactive,
    });
    await flush();
    const host = fake.main.firstElementChild;
    expect(host?.className).toContain('pointer-events-none');

    fake.renders[0]?.['before-interactive-callback']();
    expect([
      interactive.mock.calls.length,
      host?.className.includes('bg-neutral'),
    ]).toEqual([1, true]);
    fake.renders[0]?.['error-callback']();
    await expect(pending).resolves.toBeNull();
    expect(fake.main.children).toHaveLength(0);
  });

  it('returns null and removes the widget on timeout', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(configResponse())),
    );
    vi.stubGlobal('turnstile', fake.api);
    const { solveChallenge } = await import('@/lib/turnstile');
    const pending = solveChallenge('session_start', {
      chrome,
      issued: 'nonce',
    });
    await flush();

    fake.renders[0]?.['timeout-callback']();

    await expect(pending).resolves.toBeNull();
    expect(fake.api.remove).toHaveBeenCalledWith('widget-1');
    expect(fake.main.children).toHaveLength(0);
  });

  it('retries after a timed-out API load', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(configResponse())),
    );
    const { solveChallenge } = await import('@/lib/turnstile');
    const first = solveChallenge('session_start', {
      chrome,
      issued: 'nonce-1',
    });
    await flush();
    await vi.advanceTimersByTimeAsync(10_000);
    await expect(first).resolves.toBeNull();
    expect(fake.scripts[0]?.parentElement).toBeNull();

    const second = solveChallenge('session_start', {
      chrome,
      issued: 'nonce-2',
    });
    await flush();
    vi.stubGlobal('turnstile', fake.api);
    fake.scripts[1]?.dispatch('load');
    await flush();
    fake.renders[0]?.callback('token');

    await expect(second).resolves.toEqual({ nonce: 'nonce-2', token: 'token' });
    expect(fake.scripts).toHaveLength(2);
  });

  it('rejects a malformed nonce response', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(configResponse())
      .mockResolvedValueOnce(Response.json({ nonce: 42 }));
    vi.stubGlobal('fetch', fetch);
    vi.stubGlobal('turnstile', fake.api);
    const { solveChallenge } = await import('@/lib/turnstile');

    const result = await solveChallenge('env_create', { chrome });

    expect(result).toBeNull();
    expect([
      fake.api.render.mock.calls.length,
      fake.api.execute.mock.calls.length,
    ]).toEqual([0, 0]);
    expect(fake.main.children).toHaveLength(0);
    expect(vi.getTimerCount()).toBe(0);
  });
});
