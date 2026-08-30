import { MoonIcon, SunIcon } from 'lucide-react';
import { useEffect } from 'react';

import { IconButton } from '@/components/ui/icon-controls';
import { useLanguage } from '@/hooks/useLanguage';
import { applyTheme, setTheme, useTheme } from '@/hooks/useTheme';

const ThemeToggle = () => {
  const theme = useTheme();
  const { t } = useLanguage();

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  return (
    <IconButton
      aria-label={t.actions.theme}
      data-dialog-persistent=""
      onClick={() => {
        setTheme(theme === 'dark' ? 'light' : 'dark');
      }}
      title={t.actions.theme}
    >
      {theme === 'dark' ? (
        <SunIcon
          aria-hidden="true"
          className="h-4 w-4"
          data-dialog-persistent=""
        />
      ) : (
        <MoonIcon
          aria-hidden="true"
          className="h-4 w-4"
          data-dialog-persistent=""
        />
      )}
    </IconButton>
  );
};

export { ThemeToggle };
