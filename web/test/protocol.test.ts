import { afterEach, describe, expect, it, vi } from 'vitest';

import { requestTicket } from '@/lib/ticket';
import {
  classifyUploadHttpResponse,
  requestExpiredEnvironmentArchive,
  uploadFile,
} from '@/lib/transfer';

const posthog = vi.hoisted(() => ({ capture: vi.fn(), init: vi.fn() }));

vi.mock('posthog-js', () => ({ default: posthog }));

class CancelledRequest {
  public readonly open = vi.fn();
  public readonly send = vi.fn();
  public readonly setRequestHeader = vi.fn();
  public readonly upload = { addEventListener: vi.fn() };
  private readonly listeners: Partial<Record<string, () => void>> = {};

  public abort(): void {
    this.listeners['abort']?.();
  }

  public addEventListener(type: string, listener: () => void): void {
    this.listeners[type] = listener;
  }
}

const challengeState = vi.hoisted(() => ({ configured: false }));

vi.mock('@/lib/environment', () => ({
  readEnvironmentToken: () => null,
  readExpiredEnvironmentToken: () => null,
}));
vi.mock('@/lib/turnstile', () => ({
  isChallengeConfigured: () => Promise.resolve(challengeState.configured),
  solveChallenge: () => Promise.resolve(null),
}));

const chrome = { body: 'body', title: 'title', verifying: 'verifying' };
const statuses = [
  [400, 'bad-request'],
  [403, 'forbidden'],
  [409, 'conflict'],
  [410, 'gone'],
  [429, 'rate-limited'],
  [503, 'unavailable'],
  [507, 'capacity'],
] as const;

describe('sanitized protocol telemetry', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('captures only a stable boundary marker after initialization', async () => {
    // Given
    vi.stubEnv('VITE_POSTHOG_KEY', 'configured');
    vi.resetModules();
    const { captureProtocolFailure, initializeAnalytics } =
      await import('@/lib/analytics');
    captureProtocolFailure();

    // When
    await initializeAnalytics();
    captureProtocolFailure();

    // Then
    expect(posthog.capture.mock.calls).toEqual([
      ['lab_protocol_failure', { boundary: 'server_message' }],
    ]);
  });
});

describe('typed HTTP protocol outcomes', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    challengeState.configured = false;
  });

  it.each(statuses)(
    'classifies ticket status %i as %s',
    async (status, kind) => {
      // Given
      vi.stubGlobal(
        'fetch',
        vi.fn(() => Promise.resolve(new Response(null, { status }))),
      );

      // When
      const result = await requestTicket({ chrome, environmentToken: null });

      // Then
      expect(result).toEqual({ kind });
    },
  );

  it('classifies valid and malformed successful ticket responses', async () => {
    // Given
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({ ticket: 'ticket-1' }, { status: 200 }),
      )
      .mockResolvedValueOnce(new Response('{', { status: 200 }));
    vi.stubGlobal('fetch', fetch);

    // When
    const valid = await requestTicket({ chrome, environmentToken: null });
    const malformed = await requestTicket({ chrome, environmentToken: null });

    // Then
    expect([valid, malformed]).toEqual([
      { kind: 'ticket', ticket: 'ticket-1' },
      { kind: 'malformed' },
    ]);
  });

  it('classifies a ticket network failure without throwing', async () => {
    // Given
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('offline'))),
    );

    // When
    const result = await requestTicket({ chrome, environmentToken: null });

    // Then
    expect(result).toEqual({ kind: 'network' });
  });

  it('preserves blocked preflight behavior', async () => {
    // Given
    challengeState.configured = true;

    // When
    const result = await requestTicket({ chrome, environmentToken: null });

    // Then
    expect(result).toEqual({ kind: 'blocked' });
  });

  it.each(statuses)(
    'classifies archive-ticket status %i as %s',
    async (status, kind) => {
      // Given
      vi.stubGlobal(
        'fetch',
        vi.fn(() => Promise.resolve(new Response(null, { status }))),
      );

      // When
      const result = await requestExpiredEnvironmentArchive(
        'environment',
        'zip',
      );

      // Then
      expect(result).toEqual({ kind });
    },
  );

  it('classifies malformed and network archive-ticket responses', async () => {
    // Given
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response('{', { status: 200 }))
      .mockRejectedValueOnce(new TypeError('offline'));
    vi.stubGlobal('fetch', fetch);

    // When
    const malformed = await requestExpiredEnvironmentArchive(
      'environment',
      'zip',
    );
    const network = await requestExpiredEnvironmentArchive(
      'environment',
      'tgz',
    );

    // Then
    expect([malformed, network]).toEqual([
      { kind: 'malformed' },
      { kind: 'network' },
    ]);
  });

  it('launches a native anchor and reports only download requested', async () => {
    // Given
    const anchor = {
      click: vi.fn(),
      download: 'initial',
      href: '',
      rel: '',
      remove: vi.fn(),
    };
    const append = vi.fn();
    vi.stubGlobal('document', {
      body: { append },
      createElement: vi.fn(() => anchor),
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          Response.json(
            { token: 'archive token' },
            {
              status: 200,
            },
          ),
        ),
      ),
    );

    // When
    const result = await requestExpiredEnvironmentArchive('environment', 'zip');

    // Then
    expect(result).toEqual({ kind: 'requested' });
    expect(anchor.href).toBe(
      '/api/session/archive?token=archive%20token&format=zip',
    );
    expect([
      append.mock.calls.length,
      anchor.click.mock.calls.length,
      anchor.remove.mock.calls.length,
    ]).toEqual([1, 1, 1]);
  });
});

describe('upload HTTP protocol outcomes', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('classifies upload statuses and malformed success bodies precisely', () => {
    // Given
    const body = JSON.stringify({ error: 'failed' });

    // When
    const outcomes = statuses.map(([status]) =>
      classifyUploadHttpResponse(status, body),
    );
    const malformed = classifyUploadHttpResponse(201, '{');
    const success = classifyUploadHttpResponse(
      201,
      JSON.stringify({ bytes: 3, name: 'file.txt' }),
    );

    // Then
    expect(
      outcomes.map((outcome) => (outcome.ok ? 'success' : outcome.outcome)),
    ).toEqual([
      'bad-request',
      'forbidden',
      'conflict',
      'gone',
      'rate-limited',
      'unavailable',
      'capacity',
    ]);
    expect([malformed, success]).toEqual([
      { limit: null, ok: false, outcome: 'malformed', reason: 'failed' },
      { ok: true },
    ]);
  });

  it('preserves XHR upload cancellation semantics', async () => {
    // Given
    vi.stubGlobal('XMLHttpRequest', CancelledRequest);
    const handle = uploadFile(
      'session-token',
      new File(['data'], 'file.txt'),
      vi.fn(),
    );

    // When
    handle.cancel();
    const result = await handle.done;

    // Then
    expect(result).toEqual({
      limit: null,
      ok: false,
      outcome: 'network',
      reason: 'aborted',
    });
  });
});
