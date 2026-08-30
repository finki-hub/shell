import { z } from 'zod';

import {
  decodeJson,
  httpFailure,
  type HubFailure,
  type LabSession,
  sendJson,
} from '@/lib/hub-api';

// Avoid scheduled reads: the proxy treats each request as activity, preventing
// idle culling. Read during activity, transfers, mutations, and foregrounding;
// hidden tabs remain idle.
export const ACTIVE_INTERVAL_MS = 5_000;
export const ACTIVE_WINDOW_MS = 60_000;

export type StoragePoller = {
  readonly markActive: () => void;
  readonly refresh: () => void;
  readonly setTransferring: (transferring: boolean) => void;
  readonly start: () => void;
  readonly stop: () => void;
};

export type StorageResult =
  | { readonly failure: HubFailure; readonly kind: 'failed' }
  | { readonly kind: 'usage'; readonly usage: StorageUsage };

export type StorageUsage = {
  readonly inodesTotal: number;
  readonly inodesUsed: number;
  readonly totalBytes: number;
  readonly usedBytes: number;
};

type StoragePollerInput = {
  readonly clearTimer?: (handle: ReturnType<typeof setTimeout>) => void;
  readonly onResult: (result: StorageResult) => void;
  readonly read: () => Promise<StorageResult>;
  readonly setTimer?: (
    callback: () => void,
    ms: number,
  ) => ReturnType<typeof setTimeout>;
};

const StorageSchema = z.object({
  bytesLimit: z.number(),
  bytesUsed: z.number(),
  inodesLimit: z.number(),
  inodesUsed: z.number(),
});

// Keep this route out of jupyter-server activity tracking; the hub proxy still
// tracks the request.
export const readStorage = async (
  session: LabSession,
  signal?: AbortSignal,
): Promise<StorageResult> => {
  const result = await sendJson({
    method: 'GET',
    session,
    signal,
    url: `/user/${encodeURIComponent(session.username)}/lab/storage?no_track_activity=1`,
  });
  if (result.kind !== 'response') {
    return { failure: { kind: result.kind }, kind: 'failed' };
  }
  if (result.status !== 200) {
    return { failure: httpFailure(result.status, result.text), kind: 'failed' };
  }
  const model = decodeJson(StorageSchema, result.text);
  if (model === null) {
    return { failure: { kind: 'malformed' }, kind: 'failed' };
  }
  return {
    kind: 'usage',
    usage: {
      inodesTotal: model.inodesLimit,
      inodesUsed: model.inodesUsed,
      totalBytes: model.bytesLimit,
      usedBytes: model.bytesUsed,
    },
  };
};

const visible = (): boolean => document.visibilityState === 'visible';

export const createStoragePoller = ({
  clearTimer = clearTimeout,
  onResult,
  read,
  setTimer = setTimeout,
}: StoragePollerInput): StoragePoller => {
  // Epoch invalidates in-flight reads and re-arming after stop or restart.
  const poll = {
    activeUntil: 0,
    busy: false,
    epoch: 0,
    handle: null as null | ReturnType<typeof setTimeout>,
    running: false,
    transferring: false,
  };

  const cancel = (): void => {
    if (poll.handle === null) return;
    clearTimer(poll.handle);
    poll.handle = null;
  };

  const due = (): boolean =>
    poll.running &&
    visible() &&
    (poll.transferring || Date.now() < poll.activeUntil);

  const run = async (): Promise<void> => {
    cancel();
    if (!poll.running || poll.busy || !visible()) return;
    const epoch = poll.epoch;
    poll.busy = true;
    try {
      const result = await read();
      if (poll.epoch === epoch) onResult(result);
    } finally {
      poll.busy = false;
      if (poll.epoch === epoch && due()) {
        poll.handle = setTimer(() => {
          void run();
        }, ACTIVE_INTERVAL_MS);
      }
    }
  };

  const arm = (): void => {
    if (poll.handle !== null || poll.busy || !due()) return;
    poll.handle = setTimer(() => {
      void run();
    }, ACTIVE_INTERVAL_MS);
  };

  const onVisible = (): void => {
    if (!visible()) {
      cancel();
      return;
    }
    void run();
  };

  return {
    markActive: () => {
      poll.activeUntil = Date.now() + ACTIVE_WINDOW_MS;
      arm();
    },
    refresh: () => {
      void run();
    },
    setTransferring: (transferring) => {
      poll.transferring = transferring;
      if (transferring) {
        arm();
        return;
      }
      void run();
    },
    start: () => {
      if (poll.running) return;
      poll.epoch += 1;
      poll.running = true;
      poll.activeUntil = Date.now() + ACTIVE_WINDOW_MS;
      document.addEventListener('visibilitychange', onVisible);
      addEventListener('focus', onVisible);
      void run();
    },
    stop: () => {
      poll.epoch += 1;
      poll.running = false;
      poll.transferring = false;
      poll.activeUntil = 0;
      document.removeEventListener('visibilitychange', onVisible);
      removeEventListener('focus', onVisible);
      cancel();
    },
  };
};
