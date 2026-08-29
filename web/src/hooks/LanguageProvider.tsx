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
    // Guarded because localStorage throws in a browser that blocks site data,
    // and a thrown initialiser here takes the whole page down rather than one
    // unremembered preference.
    try {
      const saved = localStorage.getItem(STORAGE_KEY);

      if (saved === 'mk' || saved === 'en') {
        return saved;
      }
    } catch {
      // Falls through to the default.
    }

    return 'mk';
  });

  const handleSetLanguage = (lang: Language) => {
    setLanguage(lang);

    try {
      localStorage.setItem(STORAGE_KEY, lang);
    } catch {
      // The choice holds for this page and is simply not remembered.
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
