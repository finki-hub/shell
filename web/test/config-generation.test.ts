import { afterEach, describe, expect, it, vi } from 'vitest';

describe('configuration loading', () => {
  afterEach(() => {
    vi.resetModules();
    vi.unstubAllGlobals();
  });

  it('reads /config.json, maps an empty sitekey to null, and caches it', async () => {
    const fetchRequest = vi.fn(() =>
      Promise.resolve(Response.json({ sitekey: '' })),
    );
    vi.stubGlobal('fetch', fetchRequest);
    const { readConfig } = await import('@/lib/config');

    await expect(readConfig()).resolves.toEqual({ sitekey: null });
    await expect(readConfig()).resolves.toEqual({ sitekey: null });

    expect(fetchRequest).toHaveBeenCalledTimes(1);
    expect(fetchRequest).toHaveBeenCalledWith('/config.json', {});
  });

  it('does not cache a fallback', async () => {
    const fetchRequest = vi
      .fn<() => Promise<Response>>()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(Response.json({ sitekey: 'site-key' }));
    vi.stubGlobal('fetch', fetchRequest);
    const { readConfig } = await import('@/lib/config');

    await expect(readConfig()).resolves.toEqual({ sitekey: null });
    await expect(readConfig()).resolves.toEqual({ sitekey: 'site-key' });
    await expect(readConfig()).resolves.toEqual({ sitekey: 'site-key' });

    expect(fetchRequest).toHaveBeenCalledTimes(2);
  });

  it('aborts an in-flight request without publishing its late response', async () => {
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

    controller.abort(new DOMException('cancelled', 'AbortError'));
    response.resolve(Response.json({ sitekey: 'late' }));

    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(signals).toEqual([controller.signal]);
  });
});
