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

describe.each(Object.entries(translations))(
  '%s outcome translations',
  (_language, t) => {
    it('has one nonempty string per end reason', () => {
      expect(sorted(Object.keys(t.reasons))).toEqual(sorted(END_REASONS));
      for (const reason of END_REASONS) {
        expect(t.reasons[reason]).not.toBe('');
      }
    });

    it('has one nonempty string per contents error', () => {
      expect(sorted(Object.keys(t.files.errors))).toEqual(
        sorted(CONTENTS_ERRORS),
      );
      for (const kind of CONTENTS_ERRORS) {
        expect(t.files.errors[kind]).not.toBe('');
      }
    });
  },
);
