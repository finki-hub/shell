import type { HubFailure } from '@/lib/hub-api';

// The classification table of contract §10, in one place, so the hook does not
// re-derive it and the i18n catalogue has exactly one list to cover.
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

// A culled container answers on the very next call to `/user/<u>/...`, and
// which status it answers with depends on how far the request got: 424 is
// JupyterHub 5's own answer for "this single-user server is not running", 404
// means the hub has forgotten the server entirely, and 503 means the proxy
// route is gone. All three are the same event — idleness reclaimed the
// container — and all three are recoverable: the page offers a reconnect,
// which re-spawns onto the same home directory.
const CULLED_STATUSES: ReadonlySet<number> = new Set([404, 424, 503]);

const CHALLENGE_ERRORS: ReadonlySet<string> = new Set([
  'challenge-failed',
  'challenge-required',
]);

const isChallengeError = (value: null | string): value is EndReason =>
  value !== null && CHALLENGE_ERRORS.has(value);

// Shared by every call that is not a login: nothing here can be a challenge
// verdict, so 403 means the token stopped being accepted rather than that
// Cloudflare refused.
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

// 403 is the only status the login route uses for a challenge verdict, and it
// names which one in `error`; anything else there is a refusal the user cannot
// act on, reported as unreachable rather than as a puzzle they failed.
export const classifyLoginFailure = (failure: HubFailure): SessionEnd => {
  if (failure.kind === 'http' && failure.status === 403) {
    return isChallengeError(failure.error)
      ? end(failure.error, failure.reason)
      : end('challenge-required', failure.reason);
  }
  return classifyHubFailure(failure);
};

// Distinguished from a hub refusal on purpose: a challenge that is configured
// and could not be completed locally is its own answer. "Blocked" means the
// widget never loaded — something on this network is in the way; "unanswered"
// means a puzzle was shown and nobody solved it.
export type ChallengeOutcome = 'blocked' | 'unanswered';

export const challengeEnd = (outcome: ChallengeOutcome): SessionEnd =>
  end(outcome === 'blocked' ? 'challenge-blocked' : 'challenge-unanswered');

export const spawnStartFailed = (message: null | string): SessionEnd =>
  end('start-failed', message);

export const environmentExpired = (): SessionEnd => end('environment-expired');

// `CappedTerminalManager` answers 429 when the container already holds
// `LAB_MAX_TERMINALS` ptys, which is a different thing from the hub being at
// its session ceiling.
export const classifyTerminalFailure = (failure: HubFailure): SessionEnd =>
  failure.kind === 'http' && failure.status === 429
    ? end('terminal-limit')
    : classifyHubFailure(failure);

// terminado sends `["disconnect", code]` when the pty dies, which is the shell
// exiting rather than anything going wrong. A socket that just closes is a
// connection lost, and the transport reconnects onto a new terminal.
export const classifySocketClose = (disconnected: boolean): SessionEnd =>
  end(disconnected ? 'shell-exited' : 'connection-lost');

// Whether the page should offer "try again" on the same environment. A gone
// environment and a failed challenge both need a different starting point.
const TERMINAL_REASONS: ReadonlySet<EndReason> = new Set<EndReason>([
  'challenge-blocked',
  'challenge-failed',
  'challenge-required',
  'challenge-unanswered',
  'environment-expired',
]);

export const isRetryable = (reason: EndReason): boolean =>
  !TERMINAL_REASONS.has(reason);
