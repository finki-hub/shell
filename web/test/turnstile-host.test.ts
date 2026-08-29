import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  chrome,
  type FakeWindow,
  installFakeTurnstileWindow,
} from './support/fake-turnstile-window';

const flush = async (): Promise<void> => {
  for (let turn = 0; turn < 30; turn += 1) await Promise.resolve();
};

describe('shared Turnstile host ownership', () => {
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

  it('keeps successor host mounted when predecessor settles after successor render', async () => {
    const { solveChallenge } = await import('@/lib/turnstile');
    const predecessor = solveChallenge('session_start', {
      chrome,
      issued: 'first',
    });
    await flush();
    const successor = solveChallenge('session_start', {
      chrome,
      issued: 'second',
    });
    await flush();
    await vi.advanceTimersByTimeAsync(1_000);
    await flush();
    expect(fake.renders).toHaveLength(2);

    fake.renders[0]?.['error-callback']();
    await predecessor;

    expect(fake.main.children).toHaveLength(1);
    fake.renders[1]?.callback('token');
    await successor;
    expect(fake.main.children).toHaveLength(0);
  });

  it('ignores repeated stale owner operations', async () => {
    const { claimSharedChallengeHost } = await import('@/lib/turnstile-host');
    const predecessor = claimSharedChallengeHost(chrome);
    const successor = claimSharedChallengeHost(chrome);
    const successorHost = fake.main.firstElementChild;
    const initialClass = successorHost?.className;

    predecessor.reveal();
    predecessor.showSlot();
    predecessor.release();
    predecessor.release();

    expect(fake.main.firstElementChild).toBe(successorHost);
    expect(successorHost?.className).toBe(initialClass);
    successor.reveal();
    expect(successorHost?.className).toContain('bg-neutral');
    successor.release();
    expect(fake.main.children).toHaveLength(0);
  });
});
