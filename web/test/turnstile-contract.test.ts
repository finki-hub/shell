import { afterEach, describe, expect, it, vi } from 'vitest';

import { login } from '@/lib/hub-api';

const loginResponse = () =>
  Response.json({
    apiToken: 'api-token',
    created: true,
    maxTerminals: 2,
    tokenExpiresAt: '2099-01-01T00:00:00Z',
    username: 'user-name',
  });

describe('creation challenge login contract', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('includes the solution for a challenged create', async () => {
    const fetchRequest = vi.fn<typeof fetch>(() =>
      Promise.resolve(loginResponse()),
    );
    vi.stubGlobal('fetch', fetchRequest);

    await login({
      intent: 'create',
      token: 'environment-token',
      turnstile: 'turnstile-token',
    });

    expect(fetchRequest.mock.calls[0]?.[1]?.body).toBe(
      JSON.stringify({
        intent: 'create',
        token: 'environment-token',
        turnstile: 'turnstile-token',
      }),
    );
  });

  it.each(['create', 'resume'] as const)(
    'omits the solution for an unchallenged %s',
    async (intent) => {
      const fetchRequest = vi.fn<typeof fetch>(() =>
        Promise.resolve(loginResponse()),
      );
      vi.stubGlobal('fetch', fetchRequest);

      await login({ intent, token: 'environment-token', turnstile: null });

      expect(fetchRequest.mock.calls[0]?.[1]?.body).toBe(
        JSON.stringify({ intent, token: 'environment-token' }),
      );
    },
  );
});
