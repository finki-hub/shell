import { FileUp, HardDrive } from 'lucide-react';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

import { FilePanel } from '@/components/FilePanel';
import { Header } from '@/components/Header';
import { LabTerminal } from '@/components/LabTerminal';
import { NewEnvironmentDialog } from '@/components/NewEnvironmentDialog';
import { SessionOverlay } from '@/components/SessionOverlay';
import { Banner } from '@/components/ui/banner';
import { useDropZone } from '@/hooks/useDropZone';
import { useFiles } from '@/hooks/useFiles';
import { useLabSession } from '@/hooks/useLabSession';
import { useLanguage } from '@/hooks/useLanguage';
import { useUnloadWarning } from '@/hooks/useUnloadWarning';
import { type ContentsErrorKind } from '@/lib/contents-api';
import { isStorageFull } from '@/lib/storage';

export const Lab = () => {
  const { t } = useLanguage();
  const {
    containerRef,
    created,
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
  const announcedRef = useRef(false);
  const running = status === 'running';
  const full = storage !== null && running && isStorageFull(storage);
  const files = useFiles({
    markActive,
    refreshStorage,
    session,
    setTransferring,
    storage,
  });
  const dragging = useDropZone(running, files.enqueue);
  useUnloadWarning(running || files.uploading);

  useEffect(() => {
    if (!full) setDismissedStorage(false);
  }, [full]);

  useEffect(() => {
    if (created !== false || announcedRef.current) return;
    announcedRef.current = true;
    toast.success(t.session.resumed);
  }, [created, t]);

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
          report(files.downloadAll(format));
        }}
        onNewEnvironment={() => {
          setAskingForNew(true);
        }}
        onToggleFiles={() => {
          setFilesOpen((current) => !current);
        }}
        onUpload={files.enqueue}
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
              <div className="max-h-[55vh] min-h-0 shrink-0 basis-72 lg:max-h-none lg:basis-[22rem]">
                <FilePanel
                  container={overlayHost}
                  files={files}
                  onClose={() => {
                    setFilesOpen(false);
                    focusTerminal();
                  }}
                />
              </div>
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
              report(files.downloadAll(format));
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
