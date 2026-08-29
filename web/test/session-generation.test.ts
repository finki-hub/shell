import { describe, expect, it } from 'vitest';

import {
  createSessionGenerationOwner,
  waitForAbortSignal,
} from '@/lib/session-generation';

describe('session generation ownership', () => {
  it('signals abort before cleanup and keeps repeated cleanup idempotent', () => {
    const generation = createSessionGenerationOwner().begin(4);

    generation.abort();
    const first = generation.signal.aborted;
    generation.abort();

    expect([first, generation.signal.aborted]).toEqual([true, true]);
  });

  it('abandons a hung await when the generation is replaced', async () => {
    const owner = createSessionGenerationOwner();
    const first = owner.begin(1);
    const pending = first.waitFor(new Promise<string>(() => {}));

    owner.begin(2);

    await expect(pending).resolves.toEqual({ kind: 'stale' });
  });

  it('rejects a hung await when its signal is aborted', async () => {
    const controller = new AbortController();
    const pending = waitForAbortSignal(
      new Promise<string>(() => {}),
      controller.signal,
    );

    controller.abort(new DOMException('cancelled', 'AbortError'));

    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  });
});
