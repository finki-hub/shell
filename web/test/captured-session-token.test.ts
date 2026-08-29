import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  readEnvironmentToken,
  readExpiredEnvironmentToken,
} from '@/lib/environment';
import {
  createSessionCallbacks,
  createSessionGenerationOwner,
} from '@/lib/session-generation';
import { requestTicket } from '@/lib/ticket';

const CAPTURED_TOKEN = 'E0';
const STORAGE_KEY = 'lab.environment';

vi.mock('@/lib/turnstile', () => ({
  isChallengeConfigured: () => Promise.resolve(false),
  solveChallenge: () => Promise.resolve(null),
}));

class MemoryStorage implements Storage {
  public get length(): number {
    return this.#values.size;
  }

  readonly #values = new Map<string, string>();

  public clear(): void {
    this.#values.clear();
  }

  public getItem(key: string): null | string {
    return this.#values.get(key) ?? null;
  }

  public key(index: number): null | string {
    return this.#values.keys().toArray()[index] ?? null;
  }

  public removeItem(key: string): void {
    this.#values.delete(key);
  }

  public setItem(key: string, value: string): void {
    this.#values.set(key, value);
  }
}

const stateTargets = () => ({
  onArchiveGrant: vi.fn(),
  setChallenge: vi.fn(),
  setEndReason: vi.fn(),
  setEnvironment: vi.fn(),
  setExpiredToken: vi.fn(),
  setExpiresAt: vi.fn(),
  setExpiryWarning: vi.fn(),
  setStatus: vi.fn(),
  setStorage: vi.fn(),
  setToken: vi.fn(),
});

describe('captured environment token ownership', () => {
  let storage: MemoryStorage;

  beforeEach(() => {
    storage = new MemoryStorage();
    vi.stubGlobal('localStorage', storage);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('does not retire a successor from an expired predecessor callback', () => {
    // Given
    storage.setItem(STORAGE_KEY, CAPTURED_TOKEN);
    const generation = createSessionGenerationOwner().begin(0);
    const callbacks = createSessionCallbacks(
      generation,
      CAPTURED_TOKEN,
      stateTargets(),
    );
    storage.setItem(STORAGE_KEY, 'E1');

    // When
    callbacks.onEnvironmentExpired();

    // Then
    expect({
      expired: readExpiredEnvironmentToken(),
      live: readEnvironmentToken(),
    }).toEqual({ expired: null, live: 'E1' });
  });

  it('retires the matching captured environment callback token', () => {
    // Given
    storage.setItem(STORAGE_KEY, CAPTURED_TOKEN);
    const generation = createSessionGenerationOwner().begin(0);
    const callbacks = createSessionCallbacks(
      generation,
      CAPTURED_TOKEN,
      stateTargets(),
    );

    // When
    callbacks.onEnvironmentExpired();

    // Then
    expect({
      expired: readExpiredEnvironmentToken(),
      live: readEnvironmentToken(),
    }).toEqual({ expired: CAPTURED_TOKEN, live: null });
  });

  it('builds the ticket from the generation token after storage changes', async () => {
    // Given
    storage.setItem(STORAGE_KEY, CAPTURED_TOKEN);
    const generationToken = readEnvironmentToken();
    storage.setItem(STORAGE_KEY, 'E1');
    const bodies: unknown[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
        if (typeof init?.body === 'string') bodies.push(JSON.parse(init.body));
        return Promise.resolve(Response.json({ ticket: 'ticket' }));
      }),
    );

    // When
    const result = await requestTicket({
      chrome: {
        body: 'body',
        title: 'title',
        verifying: 'verifying',
      },
      environmentToken: generationToken,
    });

    // Then
    expect(generationToken).toBe(CAPTURED_TOKEN);
    expect(result).toEqual({ kind: 'ticket', ticket: 'ticket' });
    expect(bodies).toEqual([
      { action: 'session_start', environmentToken: CAPTURED_TOKEN },
    ]);
  });
});
