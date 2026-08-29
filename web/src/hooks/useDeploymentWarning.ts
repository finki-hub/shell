import { useCallback, useEffect, useRef, useState } from 'react';

import type { Language } from '@/lib/i18n';

import {
  createDeploymentAlertCoordinator,
  type DeploymentAlertCoordinator,
} from '@/lib/deployment-alert';
import {
  createDeploymentWarningPresentation,
  type DeploymentWarning,
  type DeploymentWarningPresentation,
  type DeploymentWarningState,
  reduceDeploymentWarning,
} from '@/lib/redeployment-warning';

export type DeploymentWarningController = {
  readonly clearDeploymentWarning: (deploymentId: string) => void;
  readonly receiveDeploymentWarning: (warning: DeploymentWarning) => void;
  readonly receiveDeploymentWarningAlert: (alert: DeploymentAlert) => void;
  readonly redeploymentWarning: DeploymentWarningPresentation | null;
  readonly resetDeploymentWarning: () => void;
};

type DeploymentAlert = {
  readonly deploymentId: string;
  readonly phase: 'one-minute' | 'start';
};

export const useDeploymentWarning = (
  language: Language,
): DeploymentWarningController => {
  const languageRef = useRef(language);
  languageRef.current = language;
  const alertsRef = useRef<DeploymentAlertCoordinator | null>(null);
  const warningRef = useRef<DeploymentWarningState | null>(null);
  const presentationRef = useRef<DeploymentWarningPresentation | null>(null);
  const [warning, setWarning] = useState<DeploymentWarningState | null>(null);

  useEffect(() => {
    const coordinator = createDeploymentAlertCoordinator();
    alertsRef.current = coordinator;

    return () => {
      coordinator.close();
      if (alertsRef.current === coordinator) {
        alertsRef.current = null;
      }
    };
  }, []);

  const receiveDeploymentWarning = useCallback(
    (incoming: DeploymentWarning) => {
      const next = reduceDeploymentWarning(warningRef.current, {
        ...incoming,
        type: 'deployment-warning',
      });
      const previousId = warningRef.current?.active?.deploymentId;
      warningRef.current = next;
      if (next.active?.deploymentId !== previousId && next.active !== null) {
        presentationRef.current = createDeploymentWarningPresentation({
          ...next.active,
          language: languageRef.current,
        });
      }
      setWarning(next);
    },
    [],
  );

  const clearDeploymentWarning = useCallback((deploymentId: string) => {
    const next = reduceDeploymentWarning(warningRef.current, {
      deploymentId,
      type: 'deployment-warning-cleared',
    });
    warningRef.current = next;
    if (next.active === null) {
      presentationRef.current = null;
    }
    setWarning(next);
  }, []);

  const receiveDeploymentWarningAlert = useCallback(
    (alert: DeploymentAlert) => {
      if (warningRef.current?.active?.deploymentId === alert.deploymentId) {
        alertsRef.current?.alert(alert);
      }
    },
    [],
  );

  const resetDeploymentWarning = useCallback(() => {
    warningRef.current = null;
    presentationRef.current = null;
    setWarning(null);
  }, []);

  return {
    clearDeploymentWarning,
    receiveDeploymentWarning,
    receiveDeploymentWarningAlert,
    redeploymentWarning:
      warning?.active === null || warning === null
        ? null
        : presentationRef.current,
    resetDeploymentWarning,
  };
};
