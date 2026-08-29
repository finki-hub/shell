import { z } from 'zod';

import {
  decodeJson,
  httpFailure,
  type HubFailure,
  type LabSession,
  sendJson,
} from '@/lib/hub-api';

// There is no free-running timer here, and that is the whole design. The hub's
// proxy stamps `last_activity` on every request it forwards, so a poll on a
// schedule of its own would keep every container looking busy and no
// environment would ever be idle enough for the culler to reclaim — a page
// left open overnight would hold a container overnight.
//
// So the badge only reads when something has actually happened: for a minute
// after the last keystroke, for as long as a transfer is running plus one read
// when it ends, once after every file-panel mutation, and once when the tab
// comes back to the foreground. A hidden tab never reads at all.
export const ACTIVE_INTERVAL_MS = 5_000;
export const ACTIVE_WINDOW_MS = 60_000;

export type StoragePoller = {
  /** A terminal keystroke. Opens (or extends) the one-minute window. */
  readonly markActive: () => void;
  /** Read now, once. Every file-panel mutation calls this. */
  readonly refresh: () => void;
  /** A transfer is running. Polling continues until it is set back to false. */
  readonly setTransferring: (transferring: boolean) => void;
  readonly start: () => void;
  readonly stop: () => void;
};

export type StorageResult =
  | { readonly failure: HubFailure; readonly kind: 'failed' }
  | { readonly kind: 'usage'; readonly usage: StorageUsage };

// The shape `storage.ts` and `StorageBadge` already speak; only the feed
// changed, so the names did not.
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

// `statvfs` inside the container reports the XFS project quota as the
// filesystem size, so these four numbers are per environment.
const StorageSchema = z.object({
  bytesLimit: z.number(),
  bytesUsed: z.number(),
  inodesLimit: z.number(),
  inodesUsed: z.number(),
});

// `no_track_activity` keeps jupyter-server's own activity stamp off this
// route. It is kept because it costs nothing, but it is not the defence: the
// hub's proxy stamps activity a layer above it, where the query parameter has
// no say, which is why the schedule above exists at all.
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
  // `epoch` rather than a second boolean: a stop or restart that lands while a
  // read is in flight must cancel that read's result and its re-arm, and a
  // number is the one thing the compiler cannot narrow away across an await.
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

  // The one predicate that decides whether another read is owed. A hidden tab
  // is never owed one, however recently its user typed.
  const due = (): boolean =>
    poll.running &&
    visible() &&
    (poll.transferring || Date.now() < poll.activeUntil);

  // Overlapping reads would compound rather than space out, so a slow answer
  // delays the next read instead of queueing another one behind it. A read
  // re-arms only while the window it belongs to is still open, which is what
  // makes the schedule stop on its own rather than run for ever.
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

  // Both count as "the user is back and looking at the number": a tab restored
  // from the background may have skipped every read of the last hour.
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
      // "…and once after it ends": the read that lands the final size.
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
