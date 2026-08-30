import { type ReactNode, useEffect, useMemo, useState } from 'react';

import { type Language, translations } from '@/lib/i18n';

import { LanguageContext } from './LanguageContext';

const STORAGE_KEY = 'finki-hub-lang';

export const LanguageProvider = ({
  children,
}: {
  readonly children: ReactNode;
}) => {
  const [language, setLanguage] = useState<Language>(() => {
    // Storage can be unavailable during initialization; use the default.
    try {
      const saved = localStorage.getItem(STORAGE_KEY);

      if (saved === 'mk' || saved === 'en') {
        return saved;
      }
    } catch {
      return 'mk';
    }

    return 'mk';
  });

  const handleSetLanguage = (lang: Language) => {
    setLanguage(lang);

    try {
      localStorage.setItem(STORAGE_KEY, lang);
    } catch {
      // Ignore unavailable storage; keep the in-memory choice.
    }
  };

  useEffect(() => {
    const copy = translations[language];
    document.documentElement.lang = language;
    document.title = `${copy.brand} / ${copy.title}`;
  }, [language]);

  const t = translations[language];

  const contextValue = useMemo(
    () => ({ language, setLanguage: handleSetLanguage, t }),
    [language, t],
  );

  return <LanguageContext value={contextValue}>{children}</LanguageContext>;
};
