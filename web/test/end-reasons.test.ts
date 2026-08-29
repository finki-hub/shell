import { describe, expect, it } from 'vitest';

import {
  challengeEnd,
  classifyHubFailure,
  classifyLoginFailure,
  classifySocketClose,
  classifyTerminalFailure,
  environmentExpired,
  isRetryable,
  spawnStartFailed,
} from '@/lib/end-reasons';

describe('session end classification', () => {
  it.each([
    [404, 'idle'],
    [424, 'idle'],
    [503, 'idle'],
    [429, 'capacity'],
    [500, 'unreachable'],
  ] as const)('maps hub status %i to %s', (status, reason) => {
    expect(
      classifyHubFailure({
        error: null,
        kind: 'http',
        message: null,
        reason: null,
        status,
      }),
    ).toEqual({ message: null, reason });
  });

  it('maps transport, login, terminal, and socket outcomes', () => {
    expect(classifyHubFailure({ kind: 'aborted' }).reason).toBe(
      'connection-lost',
    );
    expect(classifyHubFailure({ kind: 'network' }).reason).toBe('unreachable');
    expect(
      classifyLoginFailure({
        error: 'challenge-failed',
        kind: 'http',
        message: null,
        reason: 'verdict',
        status: 403,
      }),
    ).toEqual({ message: 'verdict', reason: 'challenge-failed' });
    expect(
      classifyTerminalFailure({
        error: null,
        kind: 'http',
        message: null,
        reason: null,
        status: 429,
      }).reason,
    ).toBe('terminal-limit');
    expect(classifySocketClose(true).reason).toBe('shell-exited');
    expect(classifySocketClose(false).reason).toBe('connection-lost');
  });

  it('builds the remaining explicit outcomes and retry policy', () => {
    expect(challengeEnd('blocked').reason).toBe('challenge-blocked');
    expect(challengeEnd('unanswered').reason).toBe('challenge-unanswered');
    expect(environmentExpired().reason).toBe('environment-expired');
    expect(spawnStartFailed('hub detail')).toEqual({
      message: 'hub detail',
      reason: 'start-failed',
    });
    expect(isRetryable('idle')).toBe(true);
    expect(isRetryable('environment-expired')).toBe(false);
  });
});
