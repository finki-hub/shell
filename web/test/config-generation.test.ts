import { afterEach, describe, expect, it, vi } from 'vitest';

const config = {
  challengeGraceMs: 1,
  challengeIntervalMs: 2,
  sitekey: 'sitekey',
};

describe('generation-owned config fetch', () => {
  afterEach(() => {
    vi.resetModules();
    vi.unstubAllGlobals();
  });

  it('aborts the underlying request and rejects its late result', async () => {
    // Given
    const response = Promise.withResolvers<Response>();
    const signals: Array<AbortSignal | null | undefined> = [];
    vi.stubGlobal(
      'fetch',
      vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
        signals.push(init?.signal);
        return response.promise;
      }),
    );
    const { readConfig } = await import('@/lib/config');
    const controller = new AbortController();
    const pending = readConfig(controller.signal);

    // When
    controller.abort(new DOMException('cancelled', 'AbortError'));
    response.resolve(Response.json(config));

    // Then
    expect(signals).toEqual([controller.signal]);
    expect(signals[0]?.aborted).toBe(true);
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  });

  it('does not let caller A abort or cache-poison caller B', async () => {
    // Given
    const first = Promise.withResolvers<Response>();
    const second = Promise.withResolvers<Response>();
    const responses = [first.promise, second.promise];
    const signals: Array<AbortSignal | null | undefined> = [];
    const fetchRequest = vi.fn(
      (_input: RequestInfo | URL, init?: RequestInit) => {
        signals.push(init?.signal);
        return responses.shift() ?? Promise.reject(new Error('extra request'));
      },
    );
    vi.stubGlobal('fetch', fetchRequest);
    const { readConfig } = await import('@/lib/config');
    const controllerA = new AbortController();
    const controllerB = new AbortController();
    const pendingA = readConfig(controllerA.signal);
    const pendingB = readConfig(controllerB.signal);

    // When
    controllerA.abort(new DOMException('cancelled', 'AbortError'));
    first.resolve(Response.json({ ...config, sitekey: 'stale' }));
    second.resolve(Response.json(config));

    // Then
    await expect(pendingA).rejects.toMatchObject({ name: 'AbortError' });
    await expect(pendingB).resolves.toEqual(config);
    await expect(readConfig()).resolves.toEqual(config);
    expect(fetchRequest).toHaveBeenCalledTimes(2);
    expect(signals).toEqual([controllerA.signal, controllerB.signal]);
    expect(signals[1]?.aborted).toBe(false);
  });
});
