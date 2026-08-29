import { useCallback, useEffect, useRef, useState } from 'react';

import type {
  ArchiveFormat,
  ClientEndReason,
  StorageUsage,
} from '@/lib/protocol';

import { useAudioPriming } from '@/hooks/useAudioPriming';
import { useDeploymentWarning } from '@/hooks/useDeploymentWarning';
import { useLanguage } from '@/hooks/useLanguage';
import {
  discardStoredEnvironment,
  readEnvironmentToken,
  readExpiredEnvironmentToken,
  retireEnvironmentToken,
} from '@/lib/environment';
import {
  classifyTicketResult,
  type SessionStatus as LabSessionStatus,
  type TicketError,
} from '@/lib/lab-session-protocol';
import {
  type SessionHandle,
  startLabSession,
} from '@/lib/lab-session-transport';
import {
  createSessionCallbacks,
  createSessionGenerationOwner,
  type SessionGeneration,
  type SessionGenerationOwner,
} from '@/lib/session-generation';
import { requestTicket } from '@/lib/ticket';
import { startArchiveDownload } from '@/lib/transfer';

export type SessionStatus = LabSessionStatus;

const hasOnlyExpiredEnvironment = (expectedToken: null | string): boolean =>
  expectedToken === null && readExpiredEnvironmentToken() !== null;

export const useLabSession = () => {
  const { language, t } = useLanguage();
  useAudioPriming();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chrome = {
    body: t.session.challengeStartBody,
    title: t.session.challengeTitle,
    verifying: t.session.challengeVerifying,
  };
  const chromeRef = useRef(chrome);
  chromeRef.current = chrome;
  const {
    clearDeploymentWarning,
    receiveDeploymentWarning,
    receiveDeploymentWarningAlert,
    redeploymentWarning,
    resetDeploymentWarning,
  } = useDeploymentWarning(language);
  const handleRef = useRef<null | SessionHandle>(null);
  const activeGenerationRef = useRef<null | SessionGeneration>(null);
  const generationOwnerRef = useRef<null | SessionGenerationOwner>(null);
  const generationOwner =
    generationOwnerRef.current ?? createSessionGenerationOwner();
  generationOwnerRef.current = generationOwner;
  const [status, setStatus] = useState<SessionStatus>('connecting');
  const [endReason, setEndReason] = useState<ClientEndReason | null>(null);
  const [ticketError, setTicketError] = useState<null | TicketError>(null);
  const [expiresAt, setExpiresAt] = useState<null | number>(null);
  const [storage, setStorage] = useState<null | StorageUsage>(null);
  const [environment, setEnvironment] = useState<null | {
    expiresAt: number;
    resumed: boolean;
  }>(null);
  const [expiryWarning, setExpiryWarning] = useState<null | {
    expiresAt: number;
    thresholdMs: number;
  }>(null);
  const [expiredToken, setExpiredToken] = useState<null | string>(
    readExpiredEnvironmentToken,
  );
  const [challenge, setChallenge] = useState<null | {
    deadline: number;
    nonce: string;
  }>(null);
  const [token, setToken] = useState<null | string>(null);
  const [generation, setGeneration] = useState(0);
  const restart = useCallback(() => {
    activeGenerationRef.current?.abort();
    setChallenge(null);
    setExpiredToken(readExpiredEnvironmentToken());
    setStatus('connecting');
    setEndReason(null);
    setTicketError(null);
    setExpiresAt(null);
    setStorage(null);
    setToken(null);
    setEnvironment(null);
    setExpiryWarning(null);
    resetDeploymentWarning();
    setGeneration((current) => current + 1);
  }, [resetDeploymentWarning]);
  const downloadArchive = useCallback((format: ArchiveFormat) => {
    handleRef.current?.requestArchive(format);
  }, []);
  const refreshStorage = useCallback(() => {
    handleRef.current?.refreshStorage();
  }, []);
  const answerChallenge = useCallback((solution: string) => {
    handleRef.current?.answerChallenge(solution);
  }, []);
  const focusTerminal = useCallback(() => {
    handleRef.current?.focus();
  }, []);
  const startFresh = useCallback(() => {
    discardStoredEnvironment();
    setExpiredToken(null);
    restart();
  }, [restart]);
  useEffect(() => {
    const container = containerRef.current;
    const ownedGeneration = generationOwner.begin(generation);
    const environmentToken = readEnvironmentToken();
    activeGenerationRef.current = ownedGeneration;
    let ownedHandle: null | SessionHandle = null;
    const callbacks = createSessionCallbacks(
      ownedGeneration,
      environmentToken,
      {
        onArchiveGrant: startArchiveDownload,
        onDeploymentWarning: receiveDeploymentWarning,
        onDeploymentWarningAlert: receiveDeploymentWarningAlert,
        onDeploymentWarningCleared: clearDeploymentWarning,
        setChallenge,
        setEndReason,
        setEnvironment,
        setExpiredToken,
        setExpiresAt,
        setExpiryWarning,
        setStatus,
        setStorage,
        setToken,
      },
    );

    const begin = async () => {
      if (container === null) {
        return;
      }
      if (hasOnlyExpiredEnvironment(environmentToken)) {
        if (!ownedGeneration.isCurrent()) return;
        setStatus('ended');
        setEndReason('environment-expired');
        return;
      }

      const ticket = await ownedGeneration.waitFor(
        requestTicket({
          chrome: chromeRef.current,
          environmentToken,
          signal: ownedGeneration.signal,
        }),
      );
      if (ticket.kind === 'stale') {
        return;
      }
      const decision = classifyTicketResult(ticket.value);
      switch (decision.kind) {
        case 'end':
          if (!ownedGeneration.isCurrent()) return;
          if (decision.retireEnvironment && environmentToken !== null) {
            retireEnvironmentToken(environmentToken);
            setExpiredToken(readExpiredEnvironmentToken());
          }
          setStatus('ended');
          setTicketError(decision.error);
          setEndReason(decision.endReason);
          return;
        case 'start': {
          if (!ownedGeneration.isCurrent()) {
            return;
          }
          const handle = startLabSession({
            callbacks,
            container,
            environmentToken,
            ticket: decision.ticket,
          });
          if (!ownedGeneration.isCurrent()) {
            handle.teardown();
            return;
          }
          ownedHandle = handle;
          handleRef.current = handle;
        }
      }
    };

    const start = async () => {
      try {
        await begin();
      } catch (error) {
        if (error instanceof Error) {
          if (!ownedGeneration.isCurrent()) return;
          setStatus('ended');
          setEndReason('environment-unreachable');
          return;
        }
        throw error;
      }
    };
    void start();

    return () => {
      ownedGeneration.abort();
      ownedHandle?.teardown();
      if (handleRef.current === ownedHandle) {
        handleRef.current = null;
      }
      if (activeGenerationRef.current === ownedGeneration) {
        activeGenerationRef.current = null;
      }
    };
  }, [
    clearDeploymentWarning,
    generation,
    generationOwner,
    receiveDeploymentWarning,
    receiveDeploymentWarningAlert,
  ]);

  return {
    answerChallenge,
    challenge,
    containerRef,
    downloadArchive,
    endReason,
    environment,
    expiredToken,
    expiresAt,
    expiryWarning,
    focusTerminal,
    generation,
    redeploymentWarning,
    refreshStorage,
    restart,
    startFresh,
    status,
    storage,
    ticketError,
    token,
  };
};
