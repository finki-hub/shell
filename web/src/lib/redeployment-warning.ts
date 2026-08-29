import type { LabV2ServerMessage } from '@shell/protocol';

import type { Language } from '@/lib/i18n';

export type DeploymentWarning = Omit<DeploymentWarningMessage, 'type'>;
export type DeploymentWarningPresentation = {
  readonly copy: string;
  readonly deploymentId: string;
  readonly titlePrefix: string;
};

export type DeploymentWarningState = {
  readonly active: DeploymentWarning | null;
  readonly clearedDeploymentIds: readonly string[];
  readonly newestStartedAt: number;
};

type DeploymentWarningClearedMessage = Extract<
  LabV2ServerMessage,
  { readonly type: 'deployment-warning-cleared' }
>;

type DeploymentWarningEvent =
  DeploymentWarningClearedMessage | DeploymentWarningMessage;

type DeploymentWarningMessage = Extract<
  LabV2ServerMessage,
  { readonly type: 'deployment-warning' }
>;

const rememberedClearLimit = 8;

const rememberClear = (
  clearedDeploymentIds: readonly string[],
  deploymentId: string,
): readonly string[] =>
  [...clearedDeploymentIds, deploymentId].slice(-rememberedClearLimit);

const formatTime = (deadlineAt: number, language: Language): string =>
  new Intl.DateTimeFormat(language === 'mk' ? 'mk-MK' : 'en-GB', {
    hour: '2-digit',
    hour12: false,
    minute: '2-digit',
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  }).format(deadlineAt);

export const createDeploymentWarningPresentation = ({
  deadlineAt,
  deploymentId,
  language,
}: DeploymentWarning & {
  readonly language: Language;
}): DeploymentWarningPresentation => {
  const time = formatTime(deadlineAt, language);

  if (language === 'mk') {
    return {
      copy: `Ажурирањето на серверот е закажано за ${time}. Зачувајте ја работата сега.`,
      deploymentId,
      titlePrefix: 'Ажурирање на серверот',
    };
  }

  return {
    copy: `Server update scheduled for ${time}. Save your work now.`,
    deploymentId,
    titlePrefix: 'Server update',
  };
};

export const reduceDeploymentWarning = (
  current: DeploymentWarningState | null,
  event: DeploymentWarningEvent,
): DeploymentWarningState => {
  const state = current ?? {
    active: null,
    clearedDeploymentIds: [],
    newestStartedAt: -Infinity,
  };

  if (event.type === 'deployment-warning-cleared') {
    if (state.active?.deploymentId !== event.deploymentId) {
      return state;
    }

    return {
      active: null,
      clearedDeploymentIds: rememberClear(
        state.clearedDeploymentIds,
        event.deploymentId,
      ),
      newestStartedAt: state.newestStartedAt,
    };
  }

  if (
    state.clearedDeploymentIds.includes(event.deploymentId) ||
    event.startedAt < state.newestStartedAt
  ) {
    return state;
  }

  if (state.active?.deploymentId === event.deploymentId) {
    return state;
  }

  if (event.startedAt === state.newestStartedAt) {
    return state;
  }

  return {
    active: {
      deadlineAt: event.deadlineAt,
      deploymentId: event.deploymentId,
      startedAt: event.startedAt,
    },
    clearedDeploymentIds: state.clearedDeploymentIds,
    newestStartedAt: event.startedAt,
  };
};
