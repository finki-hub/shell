export type AttemptClock = {
  readonly clearTimer: (timer: ReturnType<typeof setTimeout>) => void;
  readonly now: () => number;
  readonly setTimer: (
    callback: () => void,
    delayMs: number,
  ) => ReturnType<typeof setTimeout>;
};

export type AttemptSpacing = {
  readonly acquire: (signal: AbortSignal) => Promise<'aborted' | 'ready'>;
};

type Entry = {
  abort: (() => void) | null;
  readonly resolve: (outcome: 'aborted' | 'ready') => void;
  readonly signal: AbortSignal;
};

const systemClock: AttemptClock = {
  clearTimer: (timer) => {
    clearTimeout(timer);
  },
  now: Date.now,
  setTimer: (callback, delayMs) => setTimeout(callback, delayMs),
};

export const createAttemptSpacing = (
  gapMs: number,
  clock: AttemptClock = systemClock,
): AttemptSpacing => {
  const queue: Entry[] = [];
  let lastStartedAt: null | number = null;
  let timer: null | ReturnType<typeof setTimeout> = null;

  const pump = (): void => {
    if (timer !== null) return;
    const next = queue[0];
    if (next === undefined) return;
    const delay =
      lastStartedAt === null
        ? 0
        : Math.max(0, lastStartedAt + gapMs - clock.now());
    if (delay > 0) {
      timer = clock.setTimer(() => {
        timer = null;
        pump();
      }, delay);
      return;
    }

    queue.shift();
    if (next.abort !== null) {
      next.signal.removeEventListener('abort', next.abort);
    }
    lastStartedAt = clock.now();
    next.resolve('ready');
    pump();
  };

  const cancel = (entry: Entry): void => {
    const index = queue.indexOf(entry);
    if (index === -1) return;
    queue.splice(index, 1);
    if (entry.abort !== null) {
      entry.signal.removeEventListener('abort', entry.abort);
    }
    entry.resolve('aborted');
    if (index === 0 && timer !== null) {
      clock.clearTimer(timer);
      timer = null;
    }
    pump();
  };

  return {
    acquire: (signal) =>
      new Promise((resolve) => {
        if (signal.aborted) {
          resolve('aborted');
          return;
        }
        const entry: Entry = { abort: null, resolve, signal };
        const abort = (): void => {
          cancel(entry);
        };
        entry.abort = abort;
        signal.addEventListener('abort', abort, { once: true });
        queue.push(entry);
        pump();
      }),
  };
};
