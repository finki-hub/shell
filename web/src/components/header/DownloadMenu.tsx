import { Download } from 'lucide-react';
import { useCallback, useRef, useState } from 'react';

import { IconButton } from '@/components/ui/icon-controls';
import { useDismissable } from '@/hooks/useDismissable';
import { useLanguage } from '@/hooks/useLanguage';
import { type ArchiveFormat } from '@/lib/protocol';

type DownloadMenuProps = {
  readonly disabled: boolean;
  readonly onDownload: (format: ArchiveFormat) => void;
};

const itemClass =
  'flex w-full cursor-pointer items-center rounded-sm px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent hover:text-accent-foreground';

// ZIP is the default because Windows opens it without help, but tar.gz is a
// peer rather than a fallback: it is the only one of the two that can carry
// hard links, sparse files and modes back out intact.
export const DownloadMenu = ({ disabled, onDownload }: DownloadMenuProps) => {
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
      className="relative"
      ref={rootRef}
    >
      <IconButton
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label={t.actions.download}
        disabled={disabled}
        onClick={() => {
          setOpen((current) => !current);
        }}
        title={t.actions.download}
      >
        <Download
          aria-hidden="true"
          className="h-4 w-4"
        />
      </IconButton>
      {open && (
        <div
          className="absolute right-0 z-20 mt-2 w-44 rounded-md border bg-popover p-1 text-popover-foreground shadow-md"
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
