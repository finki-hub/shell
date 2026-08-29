import { TriangleAlert } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';

import { Breadcrumbs } from '@/components/file-panel/Breadcrumbs';
import { ConfirmDeleteDialog } from '@/components/file-panel/ConfirmDeleteDialog';
import { FileList } from '@/components/file-panel/FileList';
import { NameDialog } from '@/components/file-panel/NameDialog';
import { PanelToolbar } from '@/components/file-panel/PanelToolbar';
import { StorageSummary } from '@/components/file-panel/StorageSummary';
import { UploadList } from '@/components/file-panel/UploadList';
import { Banner } from '@/components/ui/banner';
import { type FilesModel } from '@/hooks/useFiles';
import { useLanguage } from '@/hooks/useLanguage';
import { type ContentsEntry, type ContentsErrorKind } from '@/lib/contents-api';

type FilePanelProps = {
  /**
   * Where the confirmation dialogs portal to — `Lab`'s `<main>`, so they dim
   *  the work area and stop at the header, exactly like the session dialogs.
   */
  readonly container: HTMLElement | null;
  readonly files: FilesModel;
  readonly onClose: () => void;
};

type PanelBodyProps = {
  readonly files: FilesModel;
  readonly onDelete: (entry: ContentsEntry) => void;
  readonly onDownload: (entry: ContentsEntry) => void;
  readonly onRename: (entry: ContentsEntry) => void;
};

type Prompt =
  | { readonly entry: ContentsEntry; readonly kind: 'rename' }
  | { readonly kind: 'create' };

const formatChipClass =
  'inline-flex h-7 cursor-pointer items-center rounded-md border border-input bg-background px-2 font-mono text-xs transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring';

const PanelBody = ({
  files,
  onDelete,
  onDownload,
  onRename,
}: PanelBodyProps) => {
  const { t } = useLanguage();

  // A refused listing is the whole panel's state, so it replaces the list
  // rather than sitting above a stale one that is no longer true.
  if (files.error !== null) {
    return (
      <div className="p-3">
        <Banner
          icon={TriangleAlert}
          tone="urgent"
        >
          {t.files.errors[files.error]}
        </Banner>
      </div>
    );
  }

  if (files.entries.length === 0) {
    return (
      <p className="px-3 py-4 text-sm text-muted-foreground">
        {files.loading ? t.files.loading : t.files.empty}
      </p>
    );
  }

  return (
    <FileList
      busy={files.busy}
      entries={files.entries}
      onDelete={onDelete}
      onDownload={onDownload}
      onOpen={files.open}
      onRename={onRename}
    />
  );
};

// The panel owns the wording and the two questions it has to ask; `useFiles`
// owns everything that talks to the container. Nothing here takes focus on its
// own: opening the panel leaves the caret in the terminal, and only a dialog
// the user opened moves it.
export const FilePanel = ({ container, files, onClose }: FilePanelProps) => {
  const { t } = useLanguage();
  const [deleting, setDeleting] = useState<ContentsEntry | null>(null);
  const [prompt, setPrompt] = useState<null | Prompt>(null);

  const run = (task: Promise<ContentsErrorKind | null>) => {
    void (async () => {
      const kind = await task;
      if (kind !== null) {
        toast.error(t.files.errors[kind]);
      }
    })();
  };

  return (
    <section
      aria-label={t.files.title}
      className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border bg-card"
    >
      <PanelToolbar
        busy={files.busy}
        onClose={onClose}
        onNewFolder={() => {
          setPrompt({ kind: 'create' });
        }}
        onRefresh={files.refresh}
        onUpload={files.enqueue}
      />
      <Breadcrumbs
        onNavigate={files.navigate}
        path={files.path}
      />
      <div
        aria-busy={files.loading}
        className="min-h-0 flex-1 overflow-y-auto"
      >
        <PanelBody
          files={files}
          onDelete={setDeleting}
          onDownload={(entry) => {
            run(files.downloadFile(entry));
          }}
          onRename={(entry) => {
            setPrompt({ entry, kind: 'rename' });
          }}
        />
      </div>
      <UploadList
        onCancel={files.cancelUpload}
        onClear={files.clearFinished}
        uploads={files.uploads}
      />
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t px-3 py-2">
        {files.storage !== null && <StorageSummary usage={files.storage} />}
        {/* Both formats as peers rather than a menu: there are two of them, a
            menu would be a click and a decision for something that is one
            click either way, and tar.gz is the only one that carries modes
            and links back out intact. */}
        <div className="ml-auto flex shrink-0 items-center gap-1">
          <button
            aria-label={`${t.files.downloadAll}: ${t.actions.downloadZip}`}
            className={formatChipClass}
            onClick={() => {
              run(files.downloadAll('zip'));
            }}
            title={t.actions.downloadZip}
            type="button"
          >
            .zip
          </button>
          <button
            aria-label={`${t.files.downloadAll}: ${t.actions.downloadTgz}`}
            className={formatChipClass}
            onClick={() => {
              run(files.downloadAll('tgz'));
            }}
            title={t.actions.downloadTgz}
            type="button"
          >
            .tar.gz
          </button>
        </div>
      </div>
      {prompt?.kind === 'create' && (
        <NameDialog
          confirmLabel={t.files.create}
          container={container}
          initialName=""
          onCancel={() => {
            setPrompt(null);
          }}
          onSubmit={(name) => {
            setPrompt(null);
            run(files.createFolder(name));
          }}
          title={t.files.newFolder}
        />
      )}
      {prompt?.kind === 'rename' && (
        <NameDialog
          confirmLabel={t.files.rename}
          container={container}
          initialName={prompt.entry.name}
          onCancel={() => {
            setPrompt(null);
          }}
          onSubmit={(name) => {
            const { entry } = prompt;
            setPrompt(null);
            run(files.rename(entry, name));
          }}
          title={t.files.rename}
        />
      )}
      {deleting !== null && (
        <ConfirmDeleteDialog
          container={container}
          entry={deleting}
          onCancel={() => {
            setDeleting(null);
          }}
          onConfirm={() => {
            const entry = deleting;
            setDeleting(null);
            run(files.remove(entry));
          }}
        />
      )}
    </section>
  );
};
