import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useLabSession } from '@/hooks/useLabSession';
import {
  createSessionGenerationOwner,
  waitForAbortSignal,
} from '@/lib/session-generation';

type Cleanup = () => void;
type Effect = () => Cleanup | undefined;

const runtime = vi.hoisted(() => {
  const effects: Effect[] = [];
  const refs: Array<{ current: unknown }> = [];
  const states: unknown[] = [];
  const cursor = 0;
  return { cursor, effects, refs, states };
});

vi.mock('react', () => ({
  useCallback: (callback: unknown): unknown => callback,
  useEffect: (effect: Effect): void => {
    runtime.effects.push(effect);
  },
  useRef: (initial: unknown): { current: unknown } => {
    const index = runtime.cursor;
    runtime.cursor += 1;
    const existing = runtime.refs[index];
    if (existing !== undefined) {
      return existing;
    }
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
    const setter = (value: unknown): void => {
      runtime.states[index] =
        typeof value === 'function'
          ? Reflect.apply(value, undefined, [runtime.states[index]])
          : value;
    };
    return [runtime.states[index], setter];
  },
}));

const tickets = vi.hoisted(() => {
  const pending: Array<(ticket: { kind: 'ticket'; ticket: string }) => void> =
    [];
  return { pending };
});
const startLabSession = vi.hoisted(() => vi.fn());

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
vi.mock('@/lib/attention', () => ({
  clearAttention: vi.fn(),
  playFocusedChime: vi.fn(),
  primeAudio: vi.fn(),
}));
vi.mock('@/lib/environment', () => ({
  clearExpiredEnvironmentToken: vi.fn(),
  discardEnvironment: vi.fn(),
  forgetEnvironmentToken: vi.fn(),
  readEnvironmentToken: vi.fn(() => null),
  readExpiredEnvironmentToken: vi.fn(() => null),
  retireEnvironmentToken: vi.fn(),
  writeEnvironmentToken: vi.fn(),
}));
vi.mock('@/lib/lab-session-transport', () => ({ startLabSession }));
vi.mock('@/lib/ticket', () => ({
  requestTicket: () =>
    new Promise((resolve) => {
      tickets.pending.push(resolve);
    }),
}));
vi.mock('@/lib/transfer', () => ({ startArchiveDownload: vi.fn() }));

const renderHook = () => {
  runtime.cursor = 0;
  runtime.effects.length = 0;
  return useLabSession();
};

const settlePromises = async (): Promise<void> => {
  for (let index = 0; index < 6; index += 1) {
    await Promise.resolve();
  }
};

describe('session generation ownership', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    runtime.cursor = 0;
    runtime.effects.length = 0;
    runtime.refs.length = 0;
    runtime.states.length = 0;
    tickets.pending.length = 0;
    startLabSession.mockClear();
    vi.stubGlobal('addEventListener', vi.fn());
    vi.stubGlobal('removeEventListener', vi.fn());
    vi.stubGlobal('document', {
      createElement: vi.fn(() => ({})),
      fonts: { ready: Promise.resolve() },
    });
  });

  it('does not open a socket when generation A resolves after B', async () => {
    // Given
    const first = renderHook();
    first.containerRef.current = document.createElement('div');
    const firstCleanups = runtime.effects
      .map((effect) => effect())
      .filter((value): value is Cleanup => typeof value === 'function');
    await settlePromises();
    first.restart();
    for (const cleanup of firstCleanups) cleanup();
    const second = renderHook();
    second.containerRef.current = document.createElement('div');
    for (const effect of runtime.effects) effect();
    await settlePromises();

    // When
    tickets.pending[1]?.({ kind: 'ticket', ticket: 'B' });
    await settlePromises();
    tickets.pending[0]?.({ kind: 'ticket', ticket: 'A' });
    await settlePromises();

    // Then
    expect(startLabSession).toHaveBeenCalledTimes(1);
    expect(startLabSession).toHaveBeenCalledWith(
      expect.objectContaining({ environmentToken: null, ticket: 'B' }),
    );
  });

  it('requests a ticket while fonts remain unresolved', async () => {
    // Given
    vi.stubGlobal('document', {
      createElement: vi.fn(() => ({})),
      fonts: { ready: new Promise<FontFaceSet>(() => {}) },
    });
    const session = renderHook();
    session.containerRef.current = document.createElement('div');

    // When
    for (const effect of runtime.effects) effect();
    await settlePromises();

    // Then
    expect(tickets.pending).toHaveLength(1);
  });

  it('signals abort before cleanup and keeps repeated cleanup idempotent', () => {
    // Given
    const generation = createSessionGenerationOwner().begin(4);
    const observations: boolean[] = [];

    // When
    generation.abort();
    observations.push(generation.signal.aborted);
    generation.abort();
    observations.push(generation.signal.aborted);

    // Then
    expect(observations).toEqual([true, true]);
  });

  it('abandons a hung await when the generation is replaced', async () => {
    // Given
    const owner = createSessionGenerationOwner();
    const first = owner.begin(1);
    const pending = first.waitFor(new Promise<string>(() => {}));

    // When
    owner.begin(2);

    // Then
    await expect(pending).resolves.toEqual({ kind: 'stale' });
  });

  it('rejects a hung config-style await on abort', async () => {
    // Given
    const controller = new AbortController();
    const pending = waitForAbortSignal(
      new Promise<string>(() => {}),
      controller.signal,
    );

    // When
    controller.abort(new DOMException('cancelled', 'AbortError'));

    // Then
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  });
});
