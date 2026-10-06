import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  discardEnvironmentServerSide,
  type LabSession,
  login,
  mintUrlToken,
  restartServer,
  spawnServer,
  stopServer,
} from '@/lib/hub-api';

const SESSION: LabSession = {
  apiToken: 'api-token',
  maxTerminals: 2,
  tokenExpiresAt: '2099-01-01T00:00:00Z',
  username: 'user name',
};

const response = (status: number, body = ''): Response =>
  new Response(status === 204 ? null : body, { status });

const flush = async (): Promise<void> => {
  for (let turn = 0; turn < 10; turn += 1) await Promise.resolve();
};

describe('hub API', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('sends the API token on authenticated calls and scopes URL tokens', async () => {
    const fetchRequest = vi
      .fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>()
      .mockResolvedValueOnce(response(201))
      .mockResolvedValueOnce(response(204))
      .mockResolvedValueOnce(response(204))
      .mockResolvedValueOnce(response(201, JSON.stringify({ token: 'short' })));
    vi.stubGlobal('fetch', fetchRequest);

    await spawnServer(SESSION);
    await stopServer(SESSION);
    await discardEnvironmentServerSide(SESSION);
    await expect(mintUrlToken(SESSION)).resolves.toEqual({
      kind: 'token',
      token: 'short',
    });

    for (const call of fetchRequest.mock.calls) {
      expect(call[1]?.headers).toMatchObject({
        Authorization: 'token api-token',
      });
    }
    expect(fetchRequest.mock.calls[3]?.[1]?.body).toBe(
      JSON.stringify({
        // eslint-disable-next-line camelcase -- the hub token API wire field is snake case
        expires_in: 60,
        note: 'url',
        scopes: ['access:servers!user=user name'],
      }),
    );
  });

  it('separates a gone resume from other login refusals', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(() => Promise.resolve(response(410, '{}'))),
    );

    await expect(
      login({ intent: 'resume', token: 'gone-token' }),
    ).resolves.toEqual({ kind: 'gone' });
  });

  it('accepts an immediate spawn and polls an accepted spawn to ready', async () => {
    vi.useFakeTimers();
    const fetchRequest = vi
      .fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>()
      .mockResolvedValueOnce(response(201))
      .mockResolvedValueOnce(response(202))
      .mockResolvedValueOnce(
        response(
          200,
          JSON.stringify({
            name: SESSION.username,
            pending: 'spawn',
            servers: { '': { ready: true } },
          }),
        ),
      );
    vi.stubGlobal('fetch', fetchRequest);

    await expect(spawnServer(SESSION)).resolves.toEqual({ kind: 'ready' });
    const pending = spawnServer(SESSION);
    await flush();
    await vi.advanceTimersByTimeAsync(1_000);

    await expect(pending).resolves.toEqual({ kind: 'ready' });
  });

  it('accepts additive user and server metadata while polling a bodyless spawn', async () => {
    vi.useFakeTimers();
    const fetchRequest = vi
      .fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>()
      .mockResolvedValueOnce(response(202))
      .mockResolvedValueOnce(
        response(
          200,
          '{"name":"user name","pending":null,"servers":{"":{"display_name":"Default server","ready":true}},"user_info":{"admin":false,"groups":[],"name":"user name"}}',
        ),
      );
    vi.stubGlobal('fetch', fetchRequest);

    const pending = spawnServer(SESSION);
    await flush();
    await vi.advanceTimersByTimeAsync(1_000);

    await expect(pending).resolves.toEqual({ kind: 'ready' });
    expect(fetchRequest).toHaveBeenCalledTimes(2);
    const [spawnUrl, spawnInit] = fetchRequest.mock.calls[0] ?? [];
    expect(spawnUrl).toBe('/hub/api/users/user%20name/server');
    expect(spawnInit?.method).toBe('POST');
    expect(spawnInit?.body).toBeUndefined();
    expect(new Headers(spawnInit?.headers).has('Content-Type')).toBe(false);
    expect(spawnInit?.headers).toMatchObject({
      Authorization: 'token api-token',
    });
    expect(fetchRequest.mock.calls[1]?.[1]?.method).toBe('GET');
  });

  it('reports a completed-but-unready spawn as start-failed', async () => {
    vi.useFakeTimers();
    const fetchRequest = vi
      .fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>()
      .mockResolvedValueOnce(response(202))
      .mockResolvedValueOnce(
        response(
          200,
          JSON.stringify({
            name: SESSION.username,
            pending: null,
            servers: {},
          }),
        ),
      );
    vi.stubGlobal('fetch', fetchRequest);

    const pending = spawnServer(SESSION);
    await flush();
    await vi.advanceTimersByTimeAsync(1_000);

    await expect(pending).resolves.toEqual({
      kind: 'start-failed',
      message: null,
    });
  });

  it('waits for a stopped server before spawning again', async () => {
    vi.useFakeTimers();
    const fetchRequest = vi
      .fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>()
      .mockResolvedValueOnce(response(202))
      .mockResolvedValueOnce(
        response(
          200,
          JSON.stringify({
            name: SESSION.username,
            pending: null,
            servers: {},
          }),
        ),
      )
      .mockResolvedValueOnce(response(201));
    vi.stubGlobal('fetch', fetchRequest);

    const pending = restartServer(SESSION);
    await flush();
    await vi.advanceTimersByTimeAsync(1_000);

    await expect(pending).resolves.toEqual({ kind: 'ready' });
    expect(fetchRequest.mock.calls.map((call) => call[1]?.method)).toEqual([
      'DELETE',
      'GET',
      'POST',
    ]);
  });

  it.each([400, 404])(
    'tolerates stop status %i during restart',
    async (status) => {
      const fetchRequest = vi
        .fn<
          (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
        >()
        .mockResolvedValueOnce(response(status, '{}'))
        .mockResolvedValueOnce(response(201));
      vi.stubGlobal('fetch', fetchRequest);

      await expect(restartServer(SESSION)).resolves.toEqual({ kind: 'ready' });
    },
  );
});
