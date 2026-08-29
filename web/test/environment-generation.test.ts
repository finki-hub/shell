import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  discardEnvironment,
  forgetEnvironmentToken,
  readEnvironmentToken,
  retireEnvironmentToken,
} from '@/lib/environment';

class TokenStorage implements Storage {
  public get length(): number {
    return this.#values.size;
  }
  readonly #failWrites = new Set<string>();
  #readsFail = false;

  readonly #values = new Map<string, string>();

  public clear(): void {
    this.#values.clear();
  }

  public failReads(): void {
    this.#readsFail = true;
  }

  public failWrite(key: string): void {
    this.#failWrites.add(key);
  }

  public getItem(key: string): null | string {
    if (this.#readsFail) {
      throw new DOMException('storage denied', 'SecurityError');
    }
    return this.#values.get(key) ?? null;
  }

  public key(index: number): null | string {
    return this.#values.keys().toArray()[index] ?? null;
  }

  public removeItem(key: string): void {
    this.#values.delete(key);
  }

  public setItem(key: string, value: string): void {
    if (this.#failWrites.has(key)) {
      throw new DOMException('storage full', 'QuotaExceededError');
    }
    this.#values.set(key, value);
  }
}

describe('environment token compare-and-swap', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('preserves the live token when retirement storage fails', () => {
    // Given
    const storage = new TokenStorage();
    storage.setItem('lab.environment', 'live');
    storage.failWrite('lab.environment.expired');
    vi.stubGlobal('localStorage', storage);

    // When
    const retired = retireEnvironmentToken('live');

    // Then
    expect({ retired, token: readEnvironmentToken() }).toEqual({
      retired: false,
      token: 'live',
    });
  });

  it('does not retire or forget a successor with a stale expected token', () => {
    // Given
    const storage = new TokenStorage();
    storage.setItem('lab.environment', 'successor');
    vi.stubGlobal('localStorage', storage);

    // When
    const retired = retireEnvironmentToken('predecessor');
    const forgotten = forgetEnvironmentToken('predecessor');

    // Then
    expect({ forgotten, retired, token: readEnvironmentToken() }).toEqual({
      forgotten: false,
      retired: false,
      token: 'successor',
    });
  });

  it('does not discard a successor with a stale expected token', async () => {
    // Given
    const storage = new TokenStorage();
    storage.setItem('lab.environment', 'successor');
    const fetch = vi.fn();
    vi.stubGlobal('localStorage', storage);
    vi.stubGlobal('fetch', fetch);

    // When
    const discarded = await discardEnvironment('predecessor');

    // Then
    expect({ discarded, requests: fetch.mock.calls.length }).toEqual({
      discarded: false,
      requests: 0,
    });
  });

  it('fails closed when storage cannot provide a token', () => {
    // Given
    const storage = new TokenStorage();
    storage.setItem('lab.environment', 'live');
    storage.failReads();
    vi.stubGlobal('localStorage', storage);

    // When
    const retired = retireEnvironmentToken('live');
    const forgotten = forgetEnvironmentToken('live');

    // Then
    expect({ forgotten, retired, token: readEnvironmentToken() }).toEqual({
      forgotten: false,
      retired: false,
      token: null,
    });
  });
});
