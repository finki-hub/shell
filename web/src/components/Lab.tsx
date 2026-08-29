import { FileUp, HardDrive, LoaderCircle } from 'lucide-react';
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { toast } from 'sonner';

import { FilePanel } from '@/components/FilePanel';
import { Header } from '@/components/Header';
import { LabTerminal } from '@/components/LabTerminal';
import { NewEnvironmentDialog } from '@/components/NewEnvironmentDialog';
import { SessionOverlay } from '@/components/SessionOverlay';
import { Banner } from '@/components/ui/banner';
import { UploadProgress } from '@/components/UploadProgress';
import { useDropZone } from '@/hooks/useDropZone';
import { useFiles } from '@/hooks/useFiles';
import { useLabSession } from '@/hooks/useLabSession';
import { useLanguage } from '@/hooks/useLanguage';
import { useUnloadWarning } from '@/hooks/useUnloadWarning';
import { type FileUpload } from '@/hooks/useUploadQueue';
import { type ContentsErrorKind } from '@/lib/contents-api';
import { type Translations } from '@/lib/i18n';
import { isStorageFull } from '@/lib/storage';

const updateMainUploadToast = (
  upload: FileUpload,
  uploadIds: Set<number>,
  t: Translations,
): void => {
  if (!uploadIds.has(upload.id)) return;
  const id = `main-upload-${upload.id}`;
  switch (upload.state) {
    case 'done':
      toast.success(upload.name, { description: t.files.uploaded, id });
      uploadIds.delete(upload.id);
      return;
    case 'failed':
      toast.error(upload.name, {
        description:
          upload.error === null
            ? t.files.errors.unavailable
            : t.files.errors[upload.error],
        id,
      });
      uploadIds.delete(upload.id);
      return;
    case 'running':
    case 'waiting':
      toast.loading(upload.name, {
        description: <UploadProgress value={upload.progress} />,
        id,
      });
  }
};

export const Lab = () => {
  const { t } = useLanguage();
  const {
    containerRef,
    end,
    fitTerminal,
    focusTerminal,
    markActive,
    refreshStorage,
    restart,
    session,
    setTransferring,
    startFresh,
    status,
    storage,
  } = useLabSession();
  const [overlayHost, setOverlayHost] = useState<HTMLElement | null>(null);
  const [askingForNew, setAskingForNew] = useState(false);
  const [dismissedStorage, setDismissedStorage] = useState(false);
  const [filesOpen, setFilesOpen] = useState(false);
  const mainUploadIdsRef = useRef(new Set<number>());
  const running = status === 'running';
  const loading = !running && status !== 'ended';
  const full = storage !== null && running && isStorageFull(storage);
  const files = useFiles({
    markActive,
    refreshStorage,
    session,
    setTransferring,
    storage,
  });
  const { enqueue } = files;
  const enqueueFromMain = useCallback(
    (selected: readonly File[]) => {
      for (const id of enqueue(selected)) {
        mainUploadIdsRef.current.add(id);
      }
    },
    [enqueue],
  );
  const dragging = useDropZone(running, enqueueFromMain);
  useUnloadWarning(running || files.uploading);

  useEffect(() => {
    const uploadIds = mainUploadIdsRef.current;
    const currentIds = new Set(files.uploads.map((upload) => upload.id));
    for (const id of uploadIds) {
      if (!currentIds.has(id)) {
        toast.dismiss(`main-upload-${id}`);
        uploadIds.delete(id);
      }
    }
    for (const upload of files.uploads) {
      updateMainUploadToast(upload, uploadIds, t);
    }
  }, [files.uploads, t]);

  useEffect(() => {
    if (!full) setDismissedStorage(false);
  }, [full]);

  useLayoutEffect(() => {
    fitTerminal();
  }, [filesOpen, fitTerminal]);

  const report = (task: Promise<ContentsErrorKind | null>): void => {
    void (async () => {
      const kind = await task;
      if (kind !== null) toast.error(t.files.errors[kind]);
    })();
  };

  return (
    <div className="flex h-screen flex-col bg-background">
      <Header
        filesOpen={filesOpen}
        onDownload={(format) => {
          report(files.downloadDirectory('', format));
        }}
        onNewEnvironment={() => {
          setAskingForNew(true);
        }}
        onToggleFiles={() => {
          if (!filesOpen) files.refresh();
          setFilesOpen((current) => !current);
        }}
        onUpload={enqueueFromMain}
        status={status}
        storage={storage}
      />
      <main
        className="relative flex min-h-0 flex-1 flex-col"
        ref={setOverlayHost}
      >
        <div className="container mx-auto flex min-h-0 flex-1 flex-col gap-3 py-4 sm:py-6">
          {full && !dismissedStorage && (
            <Banner
              icon={HardDrive}
              onDismiss={() => {
                setDismissedStorage(true);
              }}
              tone="urgent"
            >
              {t.storage.full}
            </Banner>
          )}
          <div className="flex min-h-0 flex-1 flex-col gap-3 lg:flex-row">
            <div className="relative min-h-0 flex-1">
              <LabTerminal containerRef={containerRef} />
              {status === 'deleting' && (
                <div className="absolute inset-px rounded-[calc(var(--radius)-1px)] bg-[#0a0a0a]" />
              )}
              {loading && (
                <output
                  aria-live="polite"
                  className="absolute inset-px z-10 flex items-center justify-center rounded-[calc(var(--radius)-1px)] bg-neutral-950/60 backdrop-blur-sm"
                >
                  <span className="flex flex-col items-center gap-3 text-sm text-neutral-100">
                    <LoaderCircle
                      aria-hidden="true"
                      className="size-12 animate-spin stroke-1 motion-reduce:animate-none"
                    />
                    {t.session.loading[status]}
                  </span>
                </output>
              )}
              {dragging && (
                <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-xl border-2 border-dashed border-primary bg-background/70">
                  <span className="flex items-center gap-2 text-sm font-medium">
                    <FileUp
                      aria-hidden="true"
                      className="h-4 w-4"
                    />
                    {t.upload.dropHere}
                  </span>
                </div>
              )}
            </div>
            {filesOpen && running && (
              <FilePanel
                container={overlayHost}
                files={files}
                onClose={() => {
                  setFilesOpen(false);
                  focusTerminal();
                }}
              />
            )}
          </div>
        </div>
        {askingForNew && running && (
          <NewEnvironmentDialog
            container={overlayHost}
            onCancel={() => {
              setAskingForNew(false);
            }}
            onClosed={focusTerminal}
            onConfirm={async () => {
              const discarded = await startFresh();
              if (discarded) setAskingForNew(false);
              return discarded;
            }}
            onDownload={(format) => {
              report(files.downloadDirectory('', format));
            }}
          />
        )}
        {status === 'ended' && end !== null && (
          <SessionOverlay
            container={overlayHost}
            end={end}
            onRestart={restart}
            onStartFresh={() => {
              void startFresh();
            }}
          />
        )}
      </main>
    </div>
  );
};
