import { describe, expect, it } from 'vitest';

import { type ContentsErrorKind } from '@/lib/contents-api';
import { END_REASONS } from '@/lib/end-reasons';
import { translations } from '@/lib/i18n';

const CONTENTS_ERRORS = [
  'aborted',
  'bad-request',
  'conflict',
  'forbidden',
  'gone',
  'insufficient-storage',
  'malformed',
  'network',
  'not-found',
  'rate-limited',
  'too-large',
  'unavailable',
] as const satisfies readonly ContentsErrorKind[];

const collator = new Intl.Collator('en');
const sorted = (values: readonly string[]): readonly string[] =>
  values.toSorted((left, right) => collator.compare(left, right));

describe('outcome translations', () => {
  it('has one nonempty string per end reason in both languages', () => {
    expect(sorted(Object.keys(translations.en.reasons))).toEqual(
      sorted(END_REASONS),
    );
    expect(sorted(Object.keys(translations.mk.reasons))).toEqual(
      sorted(END_REASONS),
    );
    for (const reason of END_REASONS) {
      expect(translations.en.reasons[reason]).not.toBe('');
      expect(translations.mk.reasons[reason]).not.toBe('');
    }
  });

  it('has one nonempty string per contents error in both languages', () => {
    expect(sorted(Object.keys(translations.en.files.errors))).toEqual(
      sorted(CONTENTS_ERRORS),
    );
    expect(sorted(Object.keys(translations.mk.files.errors))).toEqual(
      sorted(CONTENTS_ERRORS),
    );
    for (const kind of CONTENTS_ERRORS) {
      expect(translations.en.files.errors[kind]).not.toBe('');
      expect(translations.mk.files.errors[kind]).not.toBe('');
    }
  });
});
