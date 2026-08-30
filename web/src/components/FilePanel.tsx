import { Folder, FolderX, TriangleAlert } from 'lucide-react';
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
import {
  Dialog,
  DialogContent,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
} from '@/components/ui/dialog';
import { type FilesModel } from '@/hooks/useFiles';
import { useLanguage } from '@/hooks/useLanguage';
import { type ContentsEntry, type ContentsErrorKind } from '@/lib/contents-api';

type FilePanelProps = {
  /** Portal target for overlays. */
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

const PanelBody = ({
  files,
  onDelete,
  onDownload,
  onRename,
}: PanelBodyProps) => {
  const { t } = useLanguage();

  if (files.error === 'not-found') {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-3 py-8 text-center text-muted-foreground">
        <FolderX
          aria-hidden="true"
          className="size-16 stroke-1"
        />
        <p className="text-sm">{t.files.errors['not-found']}</p>
      </div>
    );
  }

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
    if (files.loading && !files.initialized) {
      return (
        <p className="px-3 py-4 text-sm text-muted-foreground">
          {t.files.loading}
        </p>
      );
    }

    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-3 py-8 text-center text-muted-foreground">
        <Folder
          aria-hidden="true"
          className="size-16 stroke-1"
        />
        <p className="text-sm">{t.files.empty}</p>
      </div>
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
    <Dialog
      modal={false}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      open
    >
      <DialogPortal container={container}>
        <DialogOverlay />
        <DialogContent className="h-[calc(100%-2rem)] max-h-[48rem] max-w-4xl items-stretch gap-0 overflow-hidden p-0 text-left">
          <section className="flex h-full min-h-0 flex-col overflow-hidden">
            <DialogTitle className="sr-only">{t.files.title}</DialogTitle>
            <PanelToolbar
              busy={files.busy}
              onClose={onClose}
              onDownload={(format) => {
                run(files.downloadDirectory(files.path, format));
              }}
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
            {files.storage !== null && (
              <div className="border-t px-3 py-2">
                <StorageSummary usage={files.storage} />
              </div>
            )}
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
        </DialogContent>
      </DialogPortal>
    </Dialog>
  );
};
