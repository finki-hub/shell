import type { ServerMessage } from '@shell/protocol';

import { describe, expect, it, vi } from 'vitest';

import { createArchiveTracker } from '@/lib/archive-tracker';
import {
  classifyTicketResult,
  dispatchServerText,
  type SessionCallbacks,
} from '@/lib/lab-session-protocol';

import { HistoricalLabV1ServerMessageSchema } from './lab-v1-historical-fixture';

const captureProtocolFailure = vi.hoisted(() => vi.fn());

vi.mock('@/lib/analytics', () => ({ captureProtocolFailure }));

const fixtures = [
  { type: 'starting' },
  {
    environmentExpiresAt: 2,
    environmentToken: 'environment',
    resumed: false,
    token: 'session',
    type: 'ready',
  },
  { reason: 'idle', type: 'end' },
  { expiresAt: 2, thresholdMs: 1, type: 'expiry-warning' },
  {
    inodesTotal: 4,
    inodesUsed: 3,
    totalBytes: 2,
    type: 'storage',
    usedBytes: 1,
  },
  { expiresInMs: 1, token: 'archive', type: 'archive-grant' },
  {
    action: 'session_keep',
    attempt: 1,
    graceMs: 10,
    nonce: 'nonce',
    type: 'challenge',
  },
  { type: 'challenge-cleared' },
] satisfies readonly ServerMessage[];

const record = (trace: string[], entry: string): void => {
  trace.push(entry);
};

const callbacks = (trace: string[]): SessionCallbacks => ({
  onArchiveGrant: () => {
    record(trace, 'archive-grant');
  },
  onChallenge: (challenge) => {
    record(trace, `challenge:${challenge.deadline}`);
  },
  onChallengeCleared: () => {
    record(trace, 'challenge-cleared');
  },
  onEnd: () => {
    record(trace, 'end');
  },
  onEnvironment: () => {
    record(trace, 'environment');
  },
  onEnvironmentExpired: () => {
    record(trace, 'retire');
  },
  onEnvironmentToken: () => {
    record(trace, 'environment-token');
  },
  onExpiresAt: () => {
    record(trace, 'expires-at');
  },
  onExpiryWarning: () => {
    record(trace, 'expiry-warning');
  },
  onReady: () => {
    record(trace, 'ready-side-effects');
  },
  onStatus: (status) => {
    record(trace, status);
  },
  onStorage: () => {
    record(trace, 'storage');
  },
  onToken: () => {
    record(trace, 'token');
  },
});

const runFixtures = (messages: readonly unknown[]) =>
  messages.map((message) => {
    const trace: string[] = [];
    const close = dispatchServerText(
      JSON.stringify(message),
      callbacks(trace),
      () => 1_700_000_000_000,
    );
    return { close, trace };
  });

describe('lab session protocol boundary', () => {
  it('dispatches every current and historical lab.v1 server message exactly once', () => {
    // Given
    const expected = [
      ['starting'],
      [
        'environment-token',
        'token',
        'environment',
        'running',
        'expires-at',
        'ready-side-effects',
      ],
      ['ended', 'end'],
      ['expiry-warning'],
      ['storage'],
      ['archive-grant'],
      ['challenge:1700000000010'],
      ['challenge-cleared'],
    ];

    // When
    const historical = fixtures.flatMap((fixture) => {
      const parsed = HistoricalLabV1ServerMessageSchema.safeParse(fixture);
      return parsed.success ? [parsed.data] : [];
    });
    const current = runFixtures(fixtures);
    const historicalDispatch = runFixtures(historical);

    // Then
    expect(current).toEqual(expected.map((trace) => ({ close: null, trace })));
    expect(historical).toHaveLength(fixtures.length);
    expect(historicalDispatch).toEqual(current);
  });

  it('returns a sanitized protocol close for malformed or unknown text', () => {
    // Given
    const trace: string[] = [];
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});

    // When
    const malformed = dispatchServerText('{', callbacks(trace), Date.now);
    const unknown = dispatchServerText(
      JSON.stringify({ token: 'secret', type: 'future' }),
      callbacks(trace),
      Date.now,
    );

    // Then
    expect([malformed, unknown]).toEqual([
      { code: 1_002, reason: 'invalid server message' },
      { code: 1_002, reason: 'invalid server message' },
    ]);
    expect(trace).toEqual([]);
    expect(captureProtocolFailure.mock.calls).toEqual([[], []]);
    expect([error.mock.calls, warn.mock.calls]).toEqual([[], []]);
    error.mockRestore();
    warn.mockRestore();
  });

  it('maps every typed ticket outcome exhaustively', () => {
    // Given
    const outcomes = [
      { kind: 'bad-request' },
      { kind: 'blocked' },
      { kind: 'capacity' },
      { kind: 'conflict' },
      { kind: 'forbidden' },
      { kind: 'gone' },
      { kind: 'malformed' },
      { kind: 'network' },
      { kind: 'rate-limited' },
      { kind: 'unanswered' },
      { kind: 'unavailable' },
    ] as const;

    // When
    const decisions = outcomes.map(classifyTicketResult);

    // Then
    expect(decisions.map((decision) => decision.kind)).toEqual(
      outcomes.map(() => 'end'),
    );
    expect(
      decisions.map((decision) =>
        decision.kind === 'end' ? decision.error : 'unexpected',
      ),
    ).toEqual(outcomes.map((outcome) => outcome.kind));
    expect(classifyTicketResult({ kind: 'ticket', ticket: 'ticket' })).toEqual({
      kind: 'start',
      ticket: 'ticket',
    });
  });

  it('correlates sequential archive formats and refuses overlap', () => {
    // Given
    const tracker = createArchiveTracker();

    // When
    const acceptedZip = tracker.request('zip');
    const overlap = tracker.request('tgz');
    const zip = tracker.consume('zip-token');
    const acceptedTgz = tracker.request('tgz');
    const tgz = tracker.consume('tgz-token');
    const repeated = tracker.consume('late-token');

    // Then
    expect([acceptedZip, overlap, acceptedTgz]).toEqual([true, false, true]);
    expect([zip, tgz, repeated]).toEqual([
      { format: 'zip', token: 'zip-token' },
      { format: 'tgz', token: 'tgz-token' },
      null,
    ]);
  });
});
