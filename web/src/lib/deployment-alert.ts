import { z } from 'zod';

import { isChimeMuted } from '@/hooks/useChime';
import { playFocusedChime } from '@/lib/attention';
import { ELECTION_WINDOW_MS } from '@/lib/constants';
import { wins } from '@/lib/election';

const deploymentAlertChannel = 'lab.deployment-warning-alert';

const ClaimSchema = z
  .object({
    alertId: z.string(),
    id: z.string(),
    lastActive: z.number(),
    score: z.literal(2),
    type: z.literal('claim'),
  })
  .strict();

export type DeploymentAlert = {
  readonly deploymentId: string;
  readonly phase: 'one-minute' | 'start';
};

export type DeploymentAlertCoordinator = {
  readonly alert: (alert: DeploymentAlert) => void;
  readonly close: () => void;
};

type AlertClaim = z.infer<typeof ClaimSchema>;

type CoordinatorOptions = {
  readonly channel?: DeploymentBroadcastChannel;
  readonly isEligible?: () => boolean;
  readonly play?: () => void;
};

type DeploymentBroadcastChannel = {
  readonly addEventListener: (
    type: 'message',
    listener: (event: MessageEvent<unknown>) => void,
  ) => void;
  readonly close: () => void;
  readonly postMessage: (message: unknown) => void;
  readonly removeEventListener: (
    type: 'message',
    listener: (event: MessageEvent<unknown>) => void,
  ) => void;
};

type ObservedClaims = {
  readonly claims: Map<string, AlertClaim>;
  readonly expiresAt: number;
};

type PendingElection = {
  readonly claims: Map<string, AlertClaim>;
  readonly mine: AlertClaim;
  readonly timer: ReturnType<typeof setTimeout>;
};

const isFocusedAndUnmuted = (): boolean =>
  !isChimeMuted() && !document.hidden && document.hasFocus();

const alertKey = ({ deploymentId, phase }: DeploymentAlert): string =>
  `${deploymentId}:${phase}`;

export const createDeploymentAlertCoordinator = (
  options: CoordinatorOptions = {},
): DeploymentAlertCoordinator => {
  const channel =
    options.channel ?? new BroadcastChannel(deploymentAlertChannel);
  const isEligible = options.isEligible ?? isFocusedAndUnmuted;
  const play = options.play ?? playFocusedChime;
  const identity = crypto.randomUUID();
  const pending = new Map<string, PendingElection>();
  const observed = new Map<string, ObservedClaims>();
  const handled = new Set<string>();

  const receive = (event: MessageEvent<unknown>): void => {
    const parsed = ClaimSchema.safeParse(event.data);
    if (!parsed.success || parsed.data.id === identity) {
      return;
    }
    const remembered = observed.get(parsed.data.alertId);
    if (remembered === undefined || remembered.expiresAt < Date.now()) {
      observed.set(parsed.data.alertId, {
        claims: new Map([[parsed.data.id, parsed.data]]),
        expiresAt: Date.now() + ELECTION_WINDOW_MS,
      });
    } else {
      remembered.claims.set(parsed.data.id, parsed.data);
    }
    pending.get(parsed.data.alertId)?.claims.set(parsed.data.id, parsed.data);
  };

  channel.addEventListener('message', receive);

  return {
    alert: (alert) => {
      const key = alertKey(alert);
      if (handled.has(key)) {
        return;
      }
      handled.add(key);
      if (!isEligible()) {
        return;
      }

      const mine: AlertClaim = {
        alertId: key,
        id: identity,
        lastActive: Date.now(),
        score: 2,
        type: 'claim',
      };
      const remembered = observed.get(key);
      const claims = new Map<string, AlertClaim>(
        remembered === undefined || remembered.expiresAt < Date.now()
          ? []
          : remembered.claims,
      );
      claims.set(identity, mine);
      const timer = setTimeout(() => {
        const election = pending.get(key);
        if (election === undefined) {
          return;
        }
        pending.delete(key);
        if (isEligible() && wins(mine, election.claims.values().toArray())) {
          play();
        }
      }, ELECTION_WINDOW_MS);
      pending.set(key, { claims, mine, timer });
      channel.postMessage(mine);
    },
    close: () => {
      for (const election of pending.values()) {
        clearTimeout(election.timer);
      }
      pending.clear();
      observed.clear();
      channel.removeEventListener('message', receive);
      channel.close();
    },
  };
};
