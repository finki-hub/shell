import {
  Download,
  File as FileIcon,
  Folder,
  Pencil,
  Trash2,
} from 'lucide-react';

import { useLanguage } from '@/hooks/useLanguage';
import { type ContentsEntry } from '@/lib/contents-api';
import { formatBytes } from '@/lib/storage';

type EntryRowProps = RowActions & {
  readonly busy: boolean;
  readonly entry: ContentsEntry;
};

type FileListProps = RowActions & {
  readonly busy: boolean;
  readonly entries: readonly ContentsEntry[];
};

type RowActions = {
  readonly onDelete: (entry: ContentsEntry) => void;
  readonly onDownload: (entry: ContentsEntry) => void;
  readonly onOpen: (entry: ContentsEntry) => void;
  readonly onRename: (entry: ContentsEntry) => void;
};

const rowButtonClass =
  'inline-flex size-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-40';

const EntryRow = ({
  busy,
  entry,
  onDelete,
  onDownload,
  onOpen,
  onRename,
}: EntryRowProps) => {
  const { language, t } = useLanguage();
  const isDirectory = entry.type === 'directory';
  // eslint-disable-next-line unicorn/prefer-temporal -- Temporal is not available in every browser this ships to
  const modified = new Date(entry.modifiedAt);
  const when = modified.toLocaleString(language === 'mk' ? 'mk-MK' : 'en-GB', {
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    month: 'short',
  });

  return (
    <li className="flex items-center gap-2 px-3 py-1 transition-colors hover:bg-accent/40">
      {isDirectory ? (
        <Folder
          aria-hidden="true"
          className="size-4 shrink-0 text-primary"
        />
      ) : (
        <FileIcon
          aria-hidden="true"
          className="size-4 shrink-0 text-muted-foreground"
        />
      )}
      {isDirectory ? (
        <button
          className="min-w-0 flex-1 cursor-pointer truncate rounded-sm text-left text-sm hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onClick={() => {
            onOpen(entry);
          }}
          title={t.files.open}
          type="button"
        >
          {entry.name}
        </button>
      ) : (
        <span
          className="min-w-0 flex-1 truncate text-sm"
          title={entry.name}
        >
          {entry.name}
        </span>
      )}
      <span className="w-14 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
        {isDirectory || entry.size === null ? '—' : formatBytes(entry.size)}
      </span>
      <span
        className="hidden w-28 shrink-0 text-right text-xs tabular-nums text-muted-foreground sm:block"
        title={modified.toLocaleString()}
      >
        {when}
      </span>
      <span className="flex w-23 shrink-0 items-center justify-end gap-1">
        {!isDirectory && (
          <button
            aria-label={`${t.files.download}: ${entry.name}`}
            className={rowButtonClass}
            onClick={() => {
              onDownload(entry);
            }}
            title={t.files.download}
            type="button"
          >
            <Download
              aria-hidden="true"
              className="size-3.5"
            />
          </button>
        )}
        <button
          aria-label={`${t.files.rename}: ${entry.name}`}
          className={rowButtonClass}
          disabled={busy}
          onClick={() => {
            onRename(entry);
          }}
          title={t.files.rename}
          type="button"
        >
          <Pencil
            aria-hidden="true"
            className="size-3.5"
          />
        </button>
        <button
          aria-label={`${t.files.delete}: ${entry.name}`}
          className={rowButtonClass}
          disabled={busy}
          onClick={() => {
            onDelete(entry);
          }}
          title={t.files.delete}
          type="button"
        >
          <Trash2
            aria-hidden="true"
            className="size-3.5"
          />
        </button>
      </span>
    </li>
  );
};

export const FileList = ({
  busy,
  entries,
  onDelete,
  onDownload,
  onOpen,
  onRename,
}: FileListProps) => {
  const { t } = useLanguage();

  return (
    <>
      <div className="flex items-center gap-2 border-b px-3 py-1 text-xs text-muted-foreground">
        <span className="min-w-0 flex-1 text-left">{t.files.columnName}</span>
        <span className="w-14 shrink-0 text-right">{t.files.columnSize}</span>
        <span className="hidden w-28 shrink-0 text-right sm:block">
          {t.files.columnModified}
        </span>
        <span
          aria-hidden="true"
          className="w-23 shrink-0"
        />
      </div>
      <ul aria-label={t.files.title}>
        {entries.map((entry) => (
          <EntryRow
            busy={busy}
            entry={entry}
            key={entry.path}
            onDelete={onDelete}
            onDownload={onDownload}
            onOpen={onOpen}
            onRename={onRename}
          />
        ))}
      </ul>
    </>
  );
};
