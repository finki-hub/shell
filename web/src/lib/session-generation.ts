export type GenerationAwait<T> =
  { readonly kind: 'current'; readonly value: T } | { readonly kind: 'stale' };

export type SessionGeneration = {
  readonly abort: () => void;
  readonly id: number;
  readonly isCurrent: () => boolean;
  readonly mutate: (mutation: () => void) => boolean;
  readonly signal: AbortSignal;
  readonly waitFor: <T>(pending: Promise<T>) => Promise<GenerationAwait<T>>;
};

export type SessionGenerationOwner = {
  readonly begin: (id: number) => SessionGeneration;
};

const abortRejection =
  (signal: AbortSignal, reject: (reason: Error) => void): (() => void) =>
  (): void => {
    reject(
      signal.reason instanceof Error
        ? signal.reason
        : new DOMException('cancelled', 'AbortError'),
    );
  };

export const waitForAbortSignal = async <T>(
  pending: Promise<T>,
  signal?: AbortSignal,
): Promise<T> => {
  if (signal === undefined) {
    return pending;
  }
  signal.throwIfAborted();
  const interrupted = Promise.withResolvers<T>();
  const abort = abortRejection(signal, interrupted.reject);
  signal.addEventListener('abort', abort, { once: true });
  try {
    return await Promise.race([pending, interrupted.promise]);
  } finally {
    signal.removeEventListener('abort', abort);
  }
};

export const createSessionGenerationOwner = (): SessionGenerationOwner => {
  let current: null | SessionGeneration = null;

  return {
    begin: (id) => {
      current?.abort();
      const controller = new AbortController();
      const generation: SessionGeneration = {
        abort: () => {
          controller.abort();
          if (current === generation) {
            current = null;
          }
        },
        id,
        isCurrent: () => current === generation && !controller.signal.aborted,
        mutate: (mutation) => {
          if (!generation.isCurrent()) {
            return false;
          }
          mutation();
          return true;
        },
        signal: controller.signal,
        waitFor: async <T>(
          pending: Promise<T>,
        ): Promise<GenerationAwait<T>> => {
          if (!generation.isCurrent()) {
            return { kind: 'stale' };
          }
          try {
            const value = await waitForAbortSignal(pending, controller.signal);
            return generation.isCurrent()
              ? { kind: 'current', value }
              : { kind: 'stale' };
          } catch (error) {
            if (!generation.isCurrent()) {
              return { kind: 'stale' };
            }
            throw error;
          }
        },
      };
      current = generation;
      return generation;
    },
  };
};
