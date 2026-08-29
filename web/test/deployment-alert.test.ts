import { afterEach, describe, expect, it, vi } from 'vitest';

import { createDeploymentAlertCoordinator } from '@/lib/deployment-alert';

const deploymentId = 'deployment-1';

type BroadcastListener = (event: MessageEvent<unknown>) => void;

class TestBroadcastChannel {
  private readonly listeners = new Set<BroadcastListener>();

  public constructor(private readonly peers: Set<TestBroadcastChannel>) {
    peers.add(this);
  }

  public addEventListener(_type: 'message', listener: BroadcastListener): void {
    this.listeners.add(listener);
  }

  public close(): void {
    this.peers.delete(this);
    this.listeners.clear();
  }

  public postMessage(message: unknown): void {
    for (const peer of this.peers) {
      if (peer !== this) {
        peer.receive(message);
      }
    }
  }

  public receive(data: unknown): void {
    for (const listener of this.listeners) {
      listener(new MessageEvent('message', { data }));
    }
  }

  public removeEventListener(
    _type: 'message',
    listener: BroadcastListener,
  ): void {
    this.listeners.delete(listener);
  }
}

describe('deployment alert election', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('elects one focused tab for each live phase and never queues an ineligible alert', async () => {
    vi.useFakeTimers();
    const peers = new Set<TestBroadcastChannel>();
    const firstPlay = vi.fn();
    const secondPlay = vi.fn();
    const first = createDeploymentAlertCoordinator({
      channel: new TestBroadcastChannel(peers),
      isEligible: () => true,
      play: firstPlay,
    });
    const second = createDeploymentAlertCoordinator({
      channel: new TestBroadcastChannel(peers),
      isEligible: () => true,
      play: secondPlay,
    });
    const skippedPlay = vi.fn();
    const skipped = createDeploymentAlertCoordinator({
      channel: new TestBroadcastChannel(peers),
      isEligible: () => false,
      play: skippedPlay,
    });

    first.alert({ deploymentId, phase: 'start' });
    second.alert({ deploymentId, phase: 'start' });
    skipped.alert({ deploymentId, phase: 'one-minute' });
    await vi.advanceTimersByTimeAsync(300);

    expect(firstPlay.mock.calls.length + secondPlay.mock.calls.length).toBe(1);
    expect(skippedPlay).not.toHaveBeenCalled();
    first.alert({ deploymentId, phase: 'one-minute' });
    second.alert({ deploymentId, phase: 'one-minute' });
    await vi.advanceTimersByTimeAsync(300);
    expect(firstPlay.mock.calls.length + secondPlay.mock.calls.length).toBe(2);

    first.close();
    second.close();
    skipped.close();
  });
});
