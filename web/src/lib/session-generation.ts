import type { DeploymentAlert } from './deployment-alert';
import type { LabSessionCallbacks } from './lab-session-transport';
import type { ClientEndReason, StorageUsage } from './protocol';
import type { DeploymentWarning } from './redeployment-warning';

import {
  clearExpiredEnvironmentToken,
  readExpiredEnvironmentToken,
  retireEnvironmentToken,
  writeEnvironmentToken,
} from './environment';

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

type SessionCallbackState = {
  readonly onArchiveGrant: LabSessionCallbacks['onArchiveGrant'];
  readonly onDeploymentWarning?: (warning: DeploymentWarning) => void;
  readonly onDeploymentWarningAlert?: (alert: DeploymentAlert) => void;
  readonly onDeploymentWarningCleared?: (deploymentId: string) => void;
  readonly setChallenge: StateSetter<
    null | Parameters<LabSessionCallbacks['onChallenge']>[0]
  >;
  readonly setEndReason: StateSetter<ClientEndReason | null>;
  readonly setEnvironment: StateSetter<
    null | Parameters<LabSessionCallbacks['onEnvironment']>[0]
  >;
  readonly setExpiredToken: StateSetter<null | string>;
  readonly setExpiresAt: StateSetter<null | number>;
  readonly setExpiryWarning: StateSetter<
    null | Parameters<LabSessionCallbacks['onExpiryWarning']>[0]
  >;
  readonly setStatus: StateSetter<
    Parameters<LabSessionCallbacks['onStatus']>[0]
  >;
  readonly setStorage: StateSetter<null | StorageUsage>;
  readonly setToken: StateSetter<null | string>;
};

type StateSetter<T> = (value: ((current: T) => T) | T) => void;

export const createSessionCallbacks = (
  generation: SessionGeneration,
  environmentToken: null | string,
  state: SessionCallbackState,
): LabSessionCallbacks => ({
  onArchiveGrant: (token, format) => {
    generation.mutate(() => {
      state.onArchiveGrant(token, format);
    });
  },
  onChallenge: (pending) => {
    generation.mutate(() => {
      state.setChallenge(pending);
    });
  },
  onChallengeCleared: () => {
    generation.mutate(() => {
      state.setChallenge(null);
    });
  },
  onDeploymentWarning: (warning) => {
    generation.mutate(() => {
      state.onDeploymentWarning?.({
        deadlineAt: warning.deadlineAt,
        deploymentId: warning.deploymentId,
        startedAt: warning.startedAt,
      });
    });
  },
  onDeploymentWarningAlert: (alert) => {
    generation.mutate(() => {
      state.onDeploymentWarningAlert?.({
        deploymentId: alert.deploymentId,
        phase: alert.phase,
      });
    });
  },
  onDeploymentWarningCleared: (cleared) => {
    generation.mutate(() => {
      state.onDeploymentWarningCleared?.(cleared.deploymentId);
    });
  },
  onEnd: (reason) => {
    generation.mutate(() => {
      state.setEndReason((current) => current ?? reason);
      state.setChallenge(null);
      if (reason === 'environment-expired') {
        state.setExpiredToken(readExpiredEnvironmentToken());
      }
    });
  },
  onEnvironment: (environment) => {
    generation.mutate(() => {
      state.setEnvironment(environment);
    });
  },
  onEnvironmentExpired: () => {
    generation.mutate(() => {
      if (environmentToken !== null) retireEnvironmentToken(environmentToken);
    });
  },
  onEnvironmentToken: (token, resumed) => {
    generation.mutate(() => {
      writeEnvironmentToken(token);
      if (resumed) clearExpiredEnvironmentToken();
    });
  },
  onExpiresAt: (expiresAt) => {
    generation.mutate(() => {
      state.setExpiresAt(expiresAt);
    });
  },
  onExpiryWarning: (warning) => {
    generation.mutate(() => {
      state.setExpiryWarning(warning);
    });
  },
  onStatus: (status) => {
    generation.mutate(() => {
      state.setStatus((current) =>
        current === 'ended' && status !== 'ended' ? current : status,
      );
    });
  },
  onStorage: (storage) => {
    generation.mutate(() => {
      state.setStorage(storage);
    });
  },
  onToken: (token) => {
    generation.mutate(() => {
      state.setToken(token);
    });
  },
});
