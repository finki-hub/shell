import { en } from './locales/en.ts';
import { mk } from './locales/mk.ts';
import { sq } from './locales/sq.ts';
import { tr } from './locales/tr.ts';

export type Language = 'en' | 'mk' | 'sq' | 'tr';

// Keep reasons and file errors in sync with their typed unions.
export type Translations = Localized<typeof en>;

type Localized<T> = T extends string
  ? string
  : { readonly [Key in keyof T]: Localized<T[Key]> };

export const translations: Record<Language, Translations> = { en, mk, sq, tr };

export const languageNames = {
  en: 'English',
  mk: 'Македонски',
  sq: 'Shqip',
  tr: 'Türkçe',
} as const satisfies Record<Language, string>;

export const languageLocales: Record<Language, string> = {
  en: 'en-GB',
  mk: 'mk-MK',
  sq: 'sq-AL',
  tr: 'tr-TR',
};

export const isLanguage = (value: unknown): value is Language =>
  typeof value === 'string' && Object.hasOwn(translations, value);

export const formatNumber = (value: number, language: Language): string =>
  new Intl.NumberFormat(languageLocales[language]).format(value);
