import { createElement } from 'react';
import { renderToString } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

import { useLabSession } from '@/hooks/useLabSession';
import { createArchiveTracker } from '@/lib/archive-tracker';
import { translations } from '@/lib/i18n';
import {
  classifyTicketResult,
  ticketErrorMessage,
} from '@/lib/lab-session-protocol';
import { archiveTicketNotice } from '@/lib/transfer';

const keyCollator = new Intl.Collator('en');

vi.mock('@/hooks/useLanguage', () => ({
  useLanguage: () => ({
    t: {
      session: {
        challengeStartBody: 'body',
        challengeTitle: 'title',
        challengeVerifying: 'verifying',
      },
    },
  }),
}));
vi.mock('@/lib/environment', () => ({
  clearExpiredEnvironmentToken: vi.fn(),
  discardEnvironment: vi.fn(() => Promise.resolve()),
  forgetEnvironmentToken: vi.fn(),
  readEnvironmentToken: vi.fn(() => null),
  readExpiredEnvironmentToken: vi.fn(() => null),
  retireEnvironmentToken: vi.fn(),
  writeEnvironmentToken: vi.fn(),
}));
vi.mock('@/lib/lab-session-transport', () => ({
  startLabSession: vi.fn(),
}));

describe('useLabSession public orchestration', () => {
  it('preserves the public hook return shape', () => {
    // Given
    let keys: string[] = [];
    const Harness = () => {
      keys = Object.keys(useLabSession()).sort((left, right) =>
        keyCollator.compare(left, right),
      );
      return null;
    };

    // When
    renderToString(createElement(Harness));

    // Then
    expect(keys).toEqual([
      'answerChallenge',
      'challenge',
      'containerRef',
      'downloadArchive',
      'endReason',
      'environment',
      'expiredToken',
      'expiresAt',
      'expiryWarning',
      'focusTerminal',
      'generation',
      'redeploymentWarning',
      'refreshStorage',
      'restart',
      'startFresh',
      'status',
      'storage',
      'ticketError',
      'token',
    ]);
  });

  it('maps every typed ticket outcome exhaustively', () => {
    // Given
    const inputs = [
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
    const decisions = inputs.map(classifyTicketResult);

    // Then
    expect(decisions).toEqual([
      {
        endReason: 'challenge-required',
        error: 'bad-request',
        kind: 'end',
        retireEnvironment: false,
      },
      {
        endReason: 'challenge-blocked',
        error: 'blocked',
        kind: 'end',
        retireEnvironment: false,
      },
      {
        endReason: 'capacity',
        error: 'capacity',
        kind: 'end',
        retireEnvironment: false,
      },
      {
        endReason: 'challenge-required',
        error: 'conflict',
        kind: 'end',
        retireEnvironment: false,
      },
      {
        endReason: 'challenge-required',
        error: 'forbidden',
        kind: 'end',
        retireEnvironment: false,
      },
      {
        endReason: 'environment-expired',
        error: 'gone',
        kind: 'end',
        retireEnvironment: true,
      },
      {
        endReason: 'environment-unreachable',
        error: 'malformed',
        kind: 'end',
        retireEnvironment: false,
      },
      {
        endReason: 'environment-unreachable',
        error: 'network',
        kind: 'end',
        retireEnvironment: false,
      },
      {
        endReason: 'environment-unreachable',
        error: 'rate-limited',
        kind: 'end',
        retireEnvironment: false,
      },
      {
        endReason: 'challenge-unanswered',
        error: 'unanswered',
        kind: 'end',
        retireEnvironment: false,
      },
      {
        endReason: 'environment-unreachable',
        error: 'unavailable',
        kind: 'end',
        retireEnvironment: false,
      },
    ]);
  });

  it('renders each precise ticket and archive outcome', () => {
    // Given
    const ticketErrors = [
      'bad-request',
      'blocked',
      'capacity',
      'conflict',
      'forbidden',
      'gone',
      'malformed',
      'network',
      'rate-limited',
      'unanswered',
      'unavailable',
    ] as const;
    const archiveOutcomes = [
      'bad-request',
      'capacity',
      'conflict',
      'forbidden',
      'gone',
      'malformed',
      'network',
      'rate-limited',
      'unavailable',
    ] as const;

    // When
    const tickets = ticketErrors.map((error) =>
      ticketErrorMessage(translations.en.ticket.errors, error),
    );
    const archives = archiveOutcomes.map((kind) =>
      archiveTicketNotice(translations.en.archive, { kind }),
    );
    const requested = archiveTicketNotice(translations.en.archive, {
      kind: 'requested',
    });

    // Then
    expect(tickets).toEqual(
      ticketErrors.map((error) => translations.en.ticket.errors[error]),
    );
    expect(archives).toEqual(
      archiveOutcomes.map((kind) => ({
        kind: 'error',
        message: translations.en.archive.errors[kind],
      })),
    );
    expect(requested).toEqual({
      kind: 'success',
      message: translations.en.archive.requested,
    });
  });

  it('correlates two sequential archive formats and rejects overlap', () => {
    // Given
    const tracker = createArchiveTracker();

    // When
    const first = tracker.request('tgz');
    const overlap = tracker.request('zip');
    const tgz = tracker.consume('tgz-token');
    const second = tracker.request('zip');
    const zip = tracker.consume('zip-token');

    // Then
    expect([first, overlap, second]).toEqual([true, false, true]);
    expect([tgz, zip]).toEqual([
      { format: 'tgz', token: 'tgz-token' },
      { format: 'zip', token: 'zip-token' },
    ]);
  });
});
