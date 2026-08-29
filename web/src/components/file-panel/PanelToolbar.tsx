import { FolderPlus, RefreshCw, Upload, X } from 'lucide-react';
import { useRef } from 'react';

import { DownloadMenu } from '@/components/header/DownloadMenu';
import { IconButton } from '@/components/ui/icon-controls';
import { useLanguage } from '@/hooks/useLanguage';
import { type ArchiveFormat } from '@/lib/contents-api';

type PanelToolbarProps = {
  readonly busy: boolean;
  readonly onClose: () => void;
  readonly onDownload: (format: ArchiveFormat) => void;
  readonly onNewFolder: () => void;
  readonly onRefresh: () => void;
  readonly onUpload: (files: readonly File[]) => void;
};

// The same `IconButton` the header uses, in the same order the header uses it,
// because the panel's toolbar and the header's controls are one vocabulary
// seen twice — a second button shape here would read as a second product.
export const PanelToolbar = ({
  busy,
  onClose,
  onDownload,
  onNewFolder,
  onRefresh,
  onUpload,
}: PanelToolbarProps) => {
  const { t } = useLanguage();
  const inputRef = useRef<HTMLInputElement | null>(null);

  return (
    <div className="flex items-center gap-1.5 border-b px-2 py-2">
      <h2 className="mr-auto min-w-0 truncate pl-1 text-sm font-semibold tracking-tight">
        {t.files.title}
      </h2>
      <input
        aria-label={t.actions.upload}
        className="hidden"
        multiple
        onChange={(event) => {
          onUpload([...(event.target.files ?? [])]);
          event.target.value = '';
        }}
        ref={inputRef}
        type="file"
      />
      <IconButton
        aria-label={t.actions.upload}
        onClick={() => {
          inputRef.current?.click();
        }}
        title={t.actions.upload}
      >
        <Upload
          aria-hidden="true"
          className="size-4"
        />
      </IconButton>
      <DownloadMenu
        label={t.files.downloadCurrent}
        onDownload={onDownload}
      />
      <IconButton
        aria-label={t.files.newFolder}
        disabled={busy}
        onClick={onNewFolder}
        title={t.files.newFolder}
      >
        <FolderPlus
          aria-hidden="true"
          className="size-4"
        />
      </IconButton>
      <IconButton
        aria-label={t.files.refresh}
        onClick={onRefresh}
        title={t.files.refresh}
      >
        <RefreshCw
          aria-hidden="true"
          className="size-4"
        />
      </IconButton>
      <IconButton
        aria-label={t.files.close}
        onClick={onClose}
        title={t.files.close}
      >
        <X
          aria-hidden="true"
          className="size-4"
        />
      </IconButton>
    </div>
  );
};
