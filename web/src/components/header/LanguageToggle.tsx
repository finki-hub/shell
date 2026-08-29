import { type Language } from '@/lib/i18n';
import { cn } from '@/lib/utils';

type LanguageToggleProps = {
  readonly language: Language;
  readonly setLanguage: (language: Language) => void;
};

const languages: Language[] = ['mk', 'en'];

export const LanguageToggle = ({
  language,
  setLanguage,
}: LanguageToggleProps) => (
  <div className="flex items-center gap-1 rounded-lg bg-secondary/50 px-2 py-1">
    {languages.map((option) => (
      <button
        aria-pressed={language === option}
        className={cn(
          'rounded px-1.5 py-1 text-xs font-medium transition-all sm:px-2',
          language === option
            ? 'bg-primary text-primary-foreground'
            : 'text-muted-foreground hover:text-foreground',
        )}
        key={option}
        onClick={() => {
          setLanguage(option);
        }}
        type="button"
      >
        {option.toUpperCase()}
      </button>
    ))}
  </div>
);
