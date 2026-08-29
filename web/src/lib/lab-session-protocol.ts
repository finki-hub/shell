import {
  assertNever,
  type ClientEndReason,
  type LabV2ServerMessage,
  LabV2ServerMessageSchema,
  type StorageUsage,
} from '@shell/protocol';

import type {
  DeploymentWarningAlertMessage,
  DeploymentWarningClearedMessage,
  DeploymentWarningMessage,
} from '@/lib/deployment-protocol';
import type { HttpFailureKind } from '@/lib/protocol';

import { captureProtocolFailure } from './analytics';

export type ProtocolClose = {
  readonly code: 1_002;
  readonly reason: 'invalid server message';
};

export type SessionCallbacks = {
  readonly onArchiveGrant: (token: string) => void;
  readonly onChallenge: (challenge: {
    readonly deadline: number;
    readonly nonce: string;
  }) => void;
  readonly onChallengeCleared: () => void;
  readonly onDeploymentWarning?: (warning: DeploymentWarningMessage) => void;
  readonly onDeploymentWarningAlert?: (
    alert: DeploymentWarningAlertMessage,
  ) => void;
  readonly onDeploymentWarningCleared?: (
    cleared: DeploymentWarningClearedMessage,
  ) => void;
  readonly onEnd: (reason: ClientEndReason) => void;
  readonly onEnvironment: (environment: {
    readonly expiresAt: number;
    readonly resumed: boolean;
  }) => void;
  readonly onEnvironmentExpired: () => void;
  readonly onEnvironmentToken: (token: string, resumed: boolean) => void;
  readonly onExpiresAt: (expiresAt: number) => void;
  readonly onExpiryWarning: (warning: {
    readonly expiresAt: number;
    readonly thresholdMs: number;
  }) => void;
  readonly onReady: () => void;
  readonly onStatus: (status: SessionStatus) => void;
  readonly onStorage: (usage: StorageUsage) => void;
  readonly onToken: (token: string) => void;
};

export type SessionStatus = 'connecting' | 'ended' | 'running' | 'starting';

export type TicketResult =
  | { readonly kind: 'blocked' }
  | { readonly kind: 'malformed' }
  | { readonly kind: 'network' }
  | { readonly kind: 'ticket'; readonly ticket: string }
  | { readonly kind: 'unanswered' }
  | { readonly kind: HttpFailureKind };

export const PROTOCOL_CLOSE: ProtocolClose = {
  code: 1_002,
  reason: 'invalid server message',
};

export const dispatchServerMessage = (
  message: LabV2ServerMessage,
  callbacks: SessionCallbacks,
  now: () => number,
): true => {
  switch (message.type) {
    case 'archive-grant':
      callbacks.onArchiveGrant(message.token);
      return true;
    case 'challenge':
      callbacks.onChallenge({
        deadline: now() + message.graceMs,
        nonce: message.nonce,
      });
      return true;
    case 'challenge-cleared':
      callbacks.onChallengeCleared();
      return true;
    case 'deployment-warning':
      callbacks.onDeploymentWarning?.(message);
      return true;
    case 'deployment-warning-alert':
      callbacks.onDeploymentWarningAlert?.(message);
      return true;
    case 'deployment-warning-cleared':
      callbacks.onDeploymentWarningCleared?.(message);
      return true;
    case 'end':
      if (message.reason === 'environment-expired') {
        callbacks.onEnvironmentExpired();
      }
      callbacks.onStatus('ended');
      callbacks.onEnd(message.reason);
      return true;
    case 'expiry-warning':
      callbacks.onExpiryWarning({
        expiresAt: message.expiresAt,
        thresholdMs: message.thresholdMs,
      });
      return true;
    case 'ready':
      callbacks.onEnvironmentToken(message.environmentToken, message.resumed);
      callbacks.onToken(message.token);
      callbacks.onEnvironment({
        expiresAt: message.environmentExpiresAt,
        resumed: message.resumed,
      });
      callbacks.onStatus('running');
      callbacks.onExpiresAt(message.environmentExpiresAt);
      callbacks.onReady();
      return true;
    case 'starting':
      callbacks.onStatus('starting');
      return true;
    case 'storage':
      callbacks.onStorage({
        inodesTotal: message.inodesTotal,
        inodesUsed: message.inodesUsed,
        totalBytes: message.totalBytes,
        usedBytes: message.usedBytes,
      });
      break;
    default:
      return assertNever(message);
  }
  return true;
};

export const dispatchServerText = (
  text: string,
  callbacks: SessionCallbacks,
  now: () => number,
): null | ProtocolClose => {
  let decoded: unknown;
  try {
    decoded = JSON.parse(text);
  } catch (error) {
    if (error instanceof SyntaxError) {
      captureProtocolFailure();
      return PROTOCOL_CLOSE;
    }
    throw error;
  }

  const parsed = LabV2ServerMessageSchema.safeParse(decoded);
  if (!parsed.success) {
    captureProtocolFailure();
    return PROTOCOL_CLOSE;
  }

  dispatchServerMessage(parsed.data, callbacks, now);
  return null;
};

export type BootstrapDecision =
  | {
      readonly endReason: ClientEndReason;
      readonly error: TicketError;
      readonly kind: 'end';
      readonly retireEnvironment: boolean;
    }
  | { readonly kind: 'start'; readonly ticket: string };

export type TicketError = Exclude<TicketResult['kind'], 'ticket'>;

export const ticketErrorMessage = (
  errors: Readonly<Record<TicketError, string>>,
  error: TicketError,
): string => errors[error];

export const classifyTicketResult = (
  result: TicketResult,
): BootstrapDecision => {
  let decision: BootstrapDecision;
  switch (result.kind) {
    case 'bad-request':
    case 'conflict':
    case 'forbidden':
      decision = {
        endReason: 'challenge-required',
        error: result.kind,
        kind: 'end',
        retireEnvironment: false,
      };
      break;
    case 'blocked':
      decision = {
        endReason: 'challenge-blocked',
        error: result.kind,
        kind: 'end',
        retireEnvironment: false,
      };
      break;
    case 'capacity':
      decision = {
        endReason: 'capacity',
        error: result.kind,
        kind: 'end',
        retireEnvironment: false,
      };
      break;
    case 'gone':
      decision = {
        endReason: 'environment-expired',
        error: result.kind,
        kind: 'end',
        retireEnvironment: true,
      };
      break;
    case 'malformed':
    case 'network':
    case 'rate-limited':
    case 'unavailable':
      decision = {
        endReason: 'environment-unreachable',
        error: result.kind,
        kind: 'end',
        retireEnvironment: false,
      };
      break;
    case 'ticket':
      decision = { kind: 'start', ticket: result.ticket };
      break;
    case 'unanswered':
      decision = {
        endReason: 'challenge-unanswered',
        error: result.kind,
        kind: 'end',
        retireEnvironment: false,
      };
      break;
  }
  return decision;
};
