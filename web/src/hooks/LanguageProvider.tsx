import { type ReactNode, useEffect, useMemo, useState } from 'react';

import { isLanguage, type Language, translations } from '@/lib/i18n';
import { updatePageMetadata } from '@/lib/page-metadata';

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

      if (isLanguage(saved)) {
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
    updatePageMetadata(language, document);
  }, [language]);

  const t = translations[language];

  const contextValue = useMemo(
    () => ({ language, setLanguage: handleSetLanguage, t }),
    [language, t],
  );

  return <LanguageContext value={contextValue}>{children}</LanguageContext>;
};
