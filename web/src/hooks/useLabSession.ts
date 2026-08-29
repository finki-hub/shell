import { useCallback, useEffect, useRef, useState } from 'react';

import { useLanguage } from '@/hooks/useLanguage';
import { readConfig } from '@/lib/config';
import {
  challengeEnd,
  classifyHubFailure,
  classifyLoginFailure,
  environmentExpired,
  type SessionEnd,
  spawnStartFailed,
} from '@/lib/end-reasons';
import {
  discardEnvironment,
  forgetEnvironmentToken,
  readOrMintEnvironmentToken,
} from '@/lib/environment';
import {
  type LabSession,
  login,
  restartServer,
  spawnServer,
} from '@/lib/hub-api';
import {
  createSessionGenerationOwner,
  type SessionGeneration,
  type SessionGenerationOwner,
} from '@/lib/session-generation';
import {
  createStoragePoller,
  readStorage,
  type StoragePoller,
  type StorageUsage,
} from '@/lib/storage-api';
import {
  startTerminal,
  type TerminalHandle,
  type TerminalStatus,
} from '@/lib/terminal-transport';
import { solveChallenge } from '@/lib/turnstile';

export type SessionStatus = 'connecting' | 'ended' | 'running' | 'starting';

const SESSION_STATUS = {
  connecting: 'connecting',
  ended: 'ended',
  reconnecting: 'connecting',
  running: 'running',
} as const satisfies Record<TerminalStatus, SessionStatus>;

// eslint-disable-next-line max-lines-per-function -- the hook owns one ordered contract bootstrap and its stable controls
export const useLabSession = () => {
  const { t } = useLanguage();
  const chromeRef = useRef({
    body: t.session.challengeStartBody,
    title: t.session.challengeTitle,
    verifying: t.session.challengeVerifying,
  });
  chromeRef.current = {
    body: t.session.challengeStartBody,
    title: t.session.challengeTitle,
    verifying: t.session.challengeVerifying,
  };
  const containerRef = useRef<HTMLDivElement | null>(null);
  const handleRef = useRef<null | TerminalHandle>(null);
  const pollerRef = useRef<null | StoragePoller>(null);
  const activeGenerationRef = useRef<null | SessionGeneration>(null);
  const generationOwnerRef = useRef<null | SessionGenerationOwner>(null);
  const sessionRef = useRef<LabSession | null>(null);
  const tokenRef = useRef<null | string>(null);
  const recoveryRef = useRef<LabSession | null>(null);
  const generationOwner =
    generationOwnerRef.current ?? createSessionGenerationOwner();
  generationOwnerRef.current = generationOwner;

  const [created, setCreated] = useState<boolean | null>(null);
  const [end, setEnd] = useState<null | SessionEnd>(null);
  const [generation, setGeneration] = useState(0);
  const [session, setSession] = useState<LabSession | null>(null);
  const [status, setStatus] = useState<SessionStatus>('connecting');
  const [storage, setStorage] = useState<null | StorageUsage>(null);

  const reset = useCallback(() => {
    activeGenerationRef.current?.abort();
    setCreated(null);
    setEnd(null);
    setSession(null);
    setStatus('connecting');
    setStorage(null);
    setGeneration((current) => current + 1);
  }, []);

  const restart = useCallback(() => {
    recoveryRef.current = sessionRef.current;
    reset();
  }, [reset]);

  const startFresh = useCallback(async (): Promise<boolean> => {
    const active = sessionRef.current;
    const token = tokenRef.current;
    if (active === null || token === null) {
      recoveryRef.current = null;
      reset();
      return true;
    }
    const discarded = await discardEnvironment(active, token);
    if (!discarded) return false;
    recoveryRef.current = null;
    // eslint-disable-next-line require-atomic-updates -- a successful discard invalidates the captured active session
    sessionRef.current = null;
    // eslint-disable-next-line require-atomic-updates -- the discarded environment's captured token is no longer usable
    tokenRef.current = null;
    setSession(null);
    reset();
    return true;
  }, [reset]);

  const refreshStorage = useCallback(() => {
    pollerRef.current?.refresh();
  }, []);
  const markActive = useCallback(() => {
    pollerRef.current?.markActive();
  }, []);
  const setTransferring = useCallback((transferring: boolean) => {
    pollerRef.current?.setTransferring(transferring);
  }, []);
  const fitTerminal = useCallback(() => {
    handleRef.current?.fit();
  }, []);
  const focusTerminal = useCallback(() => {
    handleRef.current?.focus();
  }, []);

  useEffect(() => {
    const container = containerRef.current;
    const owned = generationOwner.begin(generation);
    const recovery = recoveryRef.current;
    recoveryRef.current = null;
    activeGenerationRef.current = owned;
    let ownedHandle: null | TerminalHandle = null;
    let ownedPoller: null | StoragePoller = null;

    const finish = (next: SessionEnd): void => {
      owned.mutate(() => {
        setEnd(next);
        setStatus('ended');
      });
    };

    const openTerminal = (active: LabSession, target: HTMLDivElement): void => {
      ownedPoller = createStoragePoller({
        onResult: (result) => {
          if (result.kind === 'usage') {
            if (owned.isCurrent()) setStorage(result.usage);
            return;
          }
          finish(classifyHubFailure(result.failure));
        },
        read: () => readStorage(active, owned.signal),
      });
      ownedHandle = startTerminal({
        callbacks: {
          onEnd: finish,
          onInput: ownedPoller.markActive,
          onStatus: (next) => {
            if (owned.isCurrent()) setStatus(SESSION_STATUS[next]);
          },
        },
        container: target,
        session: active,
      });
      handleRef.current = ownedHandle;
      pollerRef.current = ownedPoller;
      ownedPoller.start();
    };

    const startServer = async (
      active: LabSession,
      recover: boolean,
    ): Promise<boolean> => {
      setStatus('starting');
      const pending = recover
        ? restartServer(active, owned.signal)
        : spawnServer(active, owned.signal);
      const result = await owned.waitFor(pending);
      if (result.kind === 'stale') return false;
      if (result.value.kind === 'start-failed') {
        finish(spawnStartFailed(result.value.message));
        return false;
      }
      if (result.value.kind === 'failed') {
        finish(classifyHubFailure(result.value.failure));
        return false;
      }
      return true;
    };

    // eslint-disable-next-line sonarjs/cognitive-complexity -- the branches mirror the contract's ordered login outcome table
    const authenticate = async (): Promise<LabSession | null> => {
      const configured = await owned.waitFor(readConfig(owned.signal));
      if (configured.kind === 'stale') return null;
      let identity = readOrMintEnvironmentToken();
      let replacedGoneEnvironment = false;
      for (;;) {
        let turnstile: null | string = null;
        if (identity.intent === 'create' && configured.value.sitekey !== null) {
          const challenge = { interactive: false };
          const solved = await owned.waitFor(
            solveChallenge('env_create', {
              chrome: chromeRef.current,
              issued: crypto.randomUUID(),
              onInteractive: () => {
                challenge.interactive = true;
              },
              signal: owned.signal,
            }),
          );
          if (solved.kind === 'stale') return null;
          if (solved.value === null) {
            finish(
              challengeEnd(challenge.interactive ? 'unanswered' : 'blocked'),
            );
            return null;
          }
          turnstile = solved.value.token;
        }
        const loggedIn = await owned.waitFor(
          login({ ...identity, signal: owned.signal, turnstile }),
        );
        if (loggedIn.kind === 'stale') return null;
        if (loggedIn.value.kind === 'gone') {
          finish(environmentExpired());
          forgetEnvironmentToken(identity.token);
          if (replacedGoneEnvironment) return null;
          replacedGoneEnvironment = true;
          identity = readOrMintEnvironmentToken();
          continue;
        }
        if (loggedIn.value.kind === 'refused') {
          finish(classifyLoginFailure(loggedIn.value.failure));
          return null;
        }
        tokenRef.current = identity.token;
        sessionRef.current = loggedIn.value.session;
        setCreated(loggedIn.value.created);
        return loggedIn.value.session;
      }
    };

    const begin = async (): Promise<void> => {
      if (container === null) return;
      const active = recovery ?? (await authenticate());
      if (active === null) return;
      if (!(await startServer(active, recovery !== null))) return;
      if (!owned.isCurrent()) return;
      setSession(active);
      openTerminal(active, container);
    };

    const start = async (): Promise<void> => {
      try {
        await begin();
      } catch (error) {
        if (error instanceof Error) {
          if (owned.isCurrent())
            finish(classifyHubFailure({ kind: 'network' }));
          return;
        }
        throw error;
      }
    };
    void start();

    return () => {
      owned.abort();
      ownedPoller?.stop();
      ownedHandle?.teardown();
      if (pollerRef.current === ownedPoller) pollerRef.current = null;
      if (handleRef.current === ownedHandle) handleRef.current = null;
      if (activeGenerationRef.current === owned) {
        activeGenerationRef.current = null;
      }
    };
  }, [generation, generationOwner]);

  return {
    containerRef,
    created,
    end,
    fitTerminal,
    focusTerminal,
    markActive,
    refreshStorage,
    restart,
    session,
    setTransferring,
    startFresh,
    status,
    storage,
  };
};
