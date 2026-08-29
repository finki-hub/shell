import { describe, expect, it } from 'vitest';

import { translations } from '@/lib/i18n';

const collator = new Intl.Collator('en');
const compareKeys = (left: string, right: string): number =>
  collator.compare(left, right);

const REQUIRED_OUTCOME_KEYS = [
  'archive.errors.bad-request',
  'archive.errors.capacity',
  'archive.errors.conflict',
  'archive.errors.forbidden',
  'archive.errors.gone',
  'archive.errors.malformed',
  'archive.errors.network',
  'archive.errors.rate-limited',
  'archive.errors.unavailable',
  'archive.requested',
  'ticket.errors.bad-request',
  'ticket.errors.blocked',
  'ticket.errors.capacity',
  'ticket.errors.conflict',
  'ticket.errors.forbidden',
  'ticket.errors.gone',
  'ticket.errors.malformed',
  'ticket.errors.network',
  'ticket.errors.rate-limited',
  'ticket.errors.unanswered',
  'ticket.errors.unavailable',
  'upload.done',
  'upload.errors.aborted',
  'upload.errors.already-running',
  'upload.errors.conflict',
  'upload.errors.empty-body',
  'upload.errors.failed',
  'upload.errors.insufficient-storage',
  'upload.errors.invalid-name',
  'upload.errors.length-required',
  'upload.errors.network',
  'upload.errors.quota',
  'upload.errors.rate-limited',
  'upload.errors.session-gone',
  'upload.errors.timeout',
  'upload.errors.too-large',
] as const;

const isRecord = (value: unknown): value is Readonly<Record<string, unknown>> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const flattenStrings = (
  value: Readonly<Record<string, unknown>>,
  prefix = '',
): ReadonlyMap<string, string> => {
  const entries = new Map<string, string>();

  for (const [key, child] of Object.entries(value)) {
    const path = prefix === '' ? key : `${prefix}.${key}`;
    if (typeof child === 'string') {
      entries.set(path, child);
    } else if (isRecord(child)) {
      for (const [childPath, text] of flattenStrings(child, path)) {
        entries.set(childPath, text);
      }
    }
  }

  return entries;
};

describe('protocol outcome translations', () => {
  it('has exactly the same structural keys in English and Macedonian', () => {
    const englishKeys = flattenStrings(translations.en)
      .keys()
      .toArray()
      .sort(compareKeys);
    const macedonianKeys = flattenStrings(translations.mk)
      .keys()
      .toArray()
      .sort(compareKeys);

    expect(macedonianKeys).toEqual(englishKeys);
  });

  it('has a nonempty localized string for every typed outcome key', () => {
    const english = flattenStrings(translations.en);
    const macedonian = flattenStrings(translations.mk);

    for (const key of REQUIRED_OUTCOME_KEYS) {
      expect(english.get(key), `missing English ${key}`).toBeTruthy();
      expect(macedonian.get(key), `missing Macedonian ${key}`).toBeTruthy();
      expect(macedonian.get(key), `locale fallback at ${key}`).not.toBe(
        english.get(key),
      );
    }
  });
});
