import type { HubFailure } from '@/lib/hub-api';

// Contract §10 end reasons; i18n covers each value.
export const END_REASONS = [
  'capacity',
  'challenge-blocked',
  'challenge-failed',
  'challenge-required',
  'challenge-unanswered',
  'connection-lost',
  'environment-expired',
  'idle',
  'shell-exited',
  'start-failed',
  'terminal-limit',
  'unreachable',
] as const;

export type EndReason = (typeof END_REASONS)[number];

export type SessionEnd = {
  readonly message: null | string;
  readonly reason: EndReason;
};

const end = (reason: EndReason, message: null | string = null): SessionEnd => ({
  message,
  reason,
});

// 404, 424, and 503 all indicate idle culling and are recoverable by reconnecting.
const CULLED_STATUSES: ReadonlySet<number> = new Set([404, 424, 503]);

const CHALLENGE_ERRORS: ReadonlySet<string> = new Set([
  'challenge-failed',
  'challenge-required',
]);

const isChallengeError = (value: null | string): value is EndReason =>
  value !== null && CHALLENGE_ERRORS.has(value);

// Non-login 403 means token rejection, not a challenge verdict.
export const classifyHubFailure = (failure: HubFailure): SessionEnd => {
  if (failure.kind !== 'http') {
    return end(failure.kind === 'aborted' ? 'connection-lost' : 'unreachable');
  }
  if (failure.status === 429) {
    return end('capacity');
  }
  if (CULLED_STATUSES.has(failure.status)) {
    return end('idle');
  }
  return end('unreachable', failure.message ?? failure.reason);
};

// Login 403 carries a challenge verdict in error; other failures are unreachable.
export const classifyLoginFailure = (failure: HubFailure): SessionEnd => {
  if (failure.kind === 'http' && failure.status === 403) {
    return isChallengeError(failure.error)
      ? end(failure.error, failure.reason)
      : end('challenge-required', failure.reason);
  }
  return classifyHubFailure(failure);
};

// Blocked means widget load failure; unanswered means a displayed challenge was
// not solved.
export type ChallengeOutcome = 'blocked' | 'unanswered';

export const challengeEnd = (outcome: ChallengeOutcome): SessionEnd =>
  end(outcome === 'blocked' ? 'challenge-blocked' : 'challenge-unanswered');

export const spawnStartFailed = (message: null | string): SessionEnd =>
  end('start-failed', message);

export const environmentExpired = (): SessionEnd => end('environment-expired');

// Terminal-manager 429 means the per-container pty limit, not hub capacity.
export const classifyTerminalFailure = (failure: HubFailure): SessionEnd =>
  failure.kind === 'http' && failure.status === 429
    ? end('terminal-limit')
    : classifyHubFailure(failure);

// A terminado disconnect means shell exit; a bare socket close means connection loss.
export const classifySocketClose = (disconnected: boolean): SessionEnd =>
  end(disconnected ? 'shell-exited' : 'connection-lost');

// These reasons cannot reconnect on the same environment.
const TERMINAL_REASONS: ReadonlySet<EndReason> = new Set<EndReason>([
  'challenge-blocked',
  'challenge-failed',
  'challenge-required',
  'challenge-unanswered',
  'environment-expired',
]);

export const isRetryable = (reason: EndReason): boolean =>
  !TERMINAL_REASONS.has(reason);
