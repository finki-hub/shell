import { createContext } from 'react';

import { type Language, type Translations } from '@/lib/i18n';

export type LanguageContextValue = {
  language: Language;
  setLanguage: (language: Language) => void;
  t: Translations;
};

export const LanguageContext = createContext<LanguageContextValue | undefined>(
  undefined,
);
LanguageContext.displayName = 'LanguageContext';
