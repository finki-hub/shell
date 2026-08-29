import { ChevronDown, Download } from 'lucide-react';
import { useCallback, useRef, useState } from 'react';

import { useDismissable } from '@/hooks/useDismissable';
import { useLanguage } from '@/hooks/useLanguage';
import { type ArchiveFormat } from '@/lib/protocol';
import { cn } from '@/lib/utils';

type DownloadButtonProps = {
  readonly block?: boolean;
  readonly disabled?: boolean;
  readonly label: string;
  readonly onDownload: (format: ArchiveFormat) => void;
};

const itemClass =
  'flex w-full cursor-pointer items-center rounded-sm px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent hover:text-accent-foreground';

// The header's icon-only menu is right for a toolbar and wrong everywhere the
// download is the point of the screen. Same two formats, same behaviour, but
// labelled — tar.gz was otherwise unreachable in exactly the places where
// carrying modes, hard links and sparse files back out intact matters most.
export const DownloadButton = ({
  block = false,
  disabled = false,
  label,
  onDownload,
}: DownloadButtonProps) => {
  const { t } = useLanguage();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const close = useCallback(() => {
    setOpen(false);
  }, []);
  useDismissable(open, rootRef, close);

  const choose = (format: ArchiveFormat) => {
    setOpen(false);
    onDownload(format);
  };

  return (
    <div
      className={cn('relative', block && 'w-full')}
      ref={rootRef}
    >
      <button
        aria-expanded={open}
        aria-haspopup="menu"
        className={cn(
          'inline-flex h-10 cursor-pointer items-center justify-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-60',
          block && 'w-full',
        )}
        disabled={disabled}
        onClick={() => {
          setOpen((current) => !current);
        }}
        type="button"
      >
        <Download
          aria-hidden="true"
          className="h-4 w-4"
        />
        {label}
        <ChevronDown
          aria-hidden="true"
          className="h-4 w-4 opacity-70"
        />
      </button>
      {open && (
        <div
          className={cn(
            'absolute right-0 z-30 mt-2 rounded-md border bg-popover p-1 text-popover-foreground shadow-md',
            block ? 'w-full' : 'w-44',
          )}
          role="menu"
        >
          <button
            className={itemClass}
            onClick={() => {
              choose('zip');
            }}
            role="menuitem"
            type="button"
          >
            {t.actions.downloadZip}
          </button>
          <button
            className={itemClass}
            onClick={() => {
              choose('tgz');
            }}
            role="menuitem"
            type="button"
          >
            {t.actions.downloadTgz}
          </button>
        </div>
      )}
    </div>
  );
};
