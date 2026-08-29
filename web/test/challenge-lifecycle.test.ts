import type { Dispatch, EffectCallback, SetStateAction } from 'react';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { clearAttention, raiseAttention } from '@/lib/attention';

type MessageListener = (event: MessageEvent<unknown>) => void;

class FakeBroadcastChannel {
  static readonly instances: FakeBroadcastChannel[] = [];

  readonly listeners = new Set<MessageListener>();

  constructor(readonly name: string) {
    FakeBroadcastChannel.instances.push(this);
  }

  addEventListener(_type: string, listener: MessageListener) {
    this.listeners.add(listener);
  }

  close() {
    this.listeners.clear();
  }

  emit(data: unknown) {
    for (const listener of this.listeners) {
      listener(new MessageEvent('message', { data }));
    }
  }

  postMessage() {
    return this.name;
  }
}

const cleanups: Array<() => void> = [];
const pagehideListeners = new Set<() => void>();
const ignoreStateUpdate = () => false;

const fakeUseState = <T>(initial: T): [T, Dispatch<SetStateAction<T>>] => [
  initial,
  ignoreStateUpdate,
];

const mountElection = async () => {
  vi.doMock('react', () => ({
    useCallback: <T extends (...arguments_: never[]) => unknown>(callback: T) =>
      callback,
    useEffect: (effect: EffectCallback) => {
      const cleanup = effect();
      if (typeof cleanup === 'function') {
        cleanups.push(cleanup);
      }
    },
    useState: fakeUseState,
  }));
  const { useChallengeElection } = await import('@/hooks/useChallengeElection');
  const clearIntervalSpy = vi.spyOn(globalThis, 'clearInterval');
  useChallengeElection('nonce-a');
  await vi.runOnlyPendingTimersAsync();
  const channel = FakeBroadcastChannel.instances.at(-1);
  if (channel === undefined) {
    expect.fail('election channel was not created');
  }

  return { channel, clearIntervalSpy };
};

describe('challenge lifecycle ownership', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel);
    vi.stubGlobal('document', {
      hasFocus: () => true,
      hidden: false,
      title: 'Baseline',
    });
    vi.stubGlobal('addEventListener', (type: string, listener: () => void) => {
      if (type === 'pagehide') {
        pagehideListeners.add(listener);
      }
    });
    vi.stubGlobal(
      'removeEventListener',
      (type: string, listener: () => void) => {
        if (type === 'pagehide') {
          pagehideListeners.delete(listener);
        }
      },
    );
  });

  afterEach(() => {
    for (const cleanup of cleanups) {
      cleanup();
    }
    cleanups.length = 0;
    pagehideListeners.clear();
    FakeBroadcastChannel.instances.length = 0;
    vi.doUnmock('react');
    vi.resetModules();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('releases holder resources when a deterministic tie demotes it', async () => {
    // Given: the hook owns a heartbeat and pagehide listener after winning.
    const { channel, clearIntervalSpy } = await mountElection();
    expect(pagehideListeners.size).toBe(1);

    // When: a lexically higher holder forces this holder to stand down.
    channel.emit({ id: '\u{FFFF}', nonce: 'nonce-a', type: 'holding' });

    // Then: demotion, not eventual effect cleanup, releases both resources.
    expect(clearIntervalSpy).toHaveBeenCalledOnce();
    expect(pagehideListeners.size).toBe(0);
  });

  it('ignores malformed channel traffic while holding', async () => {
    // Given: this tab holds the challenge lifecycle resources.
    const { channel, clearIntervalSpy } = await mountElection();

    // When: an origin peer sends a malformed BroadcastChannel payload.
    channel.emit(null);

    // Then: the holder remains active and no teardown is triggered.
    expect(clearIntervalSpy).not.toHaveBeenCalled();
    expect(pagehideListeners.size).toBe(1);
  });

  it('cleans holder resources and channel on repeated interruption', async () => {
    // Given: an elected hook owns all three browser lifecycle resources.
    const { channel, clearIntervalSpy } = await mountElection();
    const interrupt = cleanups.at(-1);
    if (interrupt === undefined) {
      expect.fail('election cleanup was not registered');
    }

    // When: its cleanup is interrupted repeatedly.
    interrupt();
    interrupt();

    // Then: teardown remains idempotent with no owned residuals.
    expect(clearIntervalSpy).toHaveBeenCalledOnce();
    expect(pagehideListeners.size).toBe(0);
    expect(channel.listeners.size).toBe(0);
  });

  it('does not let stale attention cleanup clear a successor title', () => {
    // Given: owner B supersedes owner A.
    const ownerA = raiseAttention('Alert A');
    const ownerB = raiseAttention('Alert B');
    expect(document.title).toBe('Alert B - Baseline');

    // When: stale owner A cleans up repeatedly.
    clearAttention(ownerA);
    clearAttention(ownerA);

    // Then: B remains rendered until B performs its own cleanup.
    expect(document.title).toBe('Alert B - Baseline');
    clearAttention(ownerB);
    expect(document.title).toBe('Baseline');
  });
});
