import { ChevronRight, CornerLeftUp } from 'lucide-react';
import { Fragment } from 'react';

import { parentPath } from '@/hooks/useFiles';
import { useLanguage } from '@/hooks/useLanguage';
import { cn } from '@/lib/utils';

type BreadcrumbsProps = {
  readonly onNavigate: (path: string) => void;
  readonly path: string;
};

const crumbClass =
  'shrink-0 cursor-pointer rounded-sm px-1.5 py-0.5 transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring';

export const Breadcrumbs = ({ onNavigate, path }: BreadcrumbsProps) => {
  const { t } = useLanguage();
  const segments = path.split('/').filter((segment) => segment !== '');

  return (
    <div className="flex items-center gap-1 border-b px-2 py-1.5">
      <button
        aria-label={t.files.parent}
        className={cn(
          crumbClass,
          'inline-flex size-6 items-center justify-center p-0 text-muted-foreground',
          'disabled:pointer-events-none disabled:opacity-40',
        )}
        disabled={path === ''}
        onClick={() => {
          onNavigate(parentPath(path));
        }}
        title={t.files.parent}
        type="button"
      >
        <CornerLeftUp
          aria-hidden="true"
          className="size-3.5"
        />
      </button>
      <nav
        aria-label={t.files.title}
        className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto text-xs"
      >
        <button
          aria-current={segments.length === 0 ? 'page' : undefined}
          className={cn(
            crumbClass,
            segments.length === 0
              ? 'font-medium text-foreground'
              : 'text-muted-foreground',
          )}
          onClick={() => {
            onNavigate('');
          }}
          type="button"
        >
          {t.files.home}
        </button>
        {segments.map((segment, index) => {
          const target = segments.slice(0, index + 1).join('/');
          const current = index === segments.length - 1;

          return (
            <Fragment key={target}>
              <ChevronRight
                aria-hidden="true"
                className="size-3 shrink-0 text-muted-foreground"
              />
              <button
                aria-current={current ? 'page' : undefined}
                className={cn(
                  crumbClass,
                  current
                    ? 'font-medium text-foreground'
                    : 'text-muted-foreground',
                )}
                onClick={() => {
                  onNavigate(target);
                }}
                title={segment}
                type="button"
              >
                {segment}
              </button>
            </Fragment>
          );
        })}
      </nav>
    </div>
  );
};
