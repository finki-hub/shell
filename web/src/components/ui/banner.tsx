import { type LucideIcon, X } from 'lucide-react';
import { type ReactNode } from 'react';

import { useLanguage } from '@/hooks/useLanguage';
import { cn } from '@/lib/utils';

type BannerProps = {
  readonly action?: ReactNode;
  readonly children: ReactNode;
  readonly icon: LucideIcon;
  readonly onDismiss?: () => void;
  readonly tone: 'urgent' | 'warning';
};

const TONES = {
  urgent: {
    icon: 'text-destructive',
    surface: 'border-destructive bg-destructive/10 text-destructive',
  },
  warning: {
    icon: 'text-amber-600 dark:text-amber-400',
    surface: 'border-amber-500/40 bg-amber-500/10',
  },
} as const;

export const Banner = ({
  action,
  children,
  icon: Icon,
  onDismiss,
  tone,
}: BannerProps) => {
  const { t } = useLanguage();
  const style = TONES[tone];

  return (
    <div
      className={cn(
        'flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border px-3 py-2.5 text-sm',
        style.surface,
      )}
    >
      <Icon
        aria-hidden="true"
        className={cn('size-4 shrink-0', style.icon)}
      />
      <span className="min-w-0 flex-1">{children}</span>
      {action}
      {onDismiss !== undefined && (
        <button
          aria-label={t.actions.dismiss}
          className="inline-flex size-7 shrink-0 cursor-pointer items-center justify-center rounded-md transition-colors hover:bg-black/10 dark:hover:bg-white/10"
          onClick={onDismiss}
          title={t.actions.dismiss}
          type="button"
        >
          <X
            aria-hidden="true"
            className="size-3.5"
          />
        </button>
      )}
    </div>
  );
};
