import { FileUp, HardDrive, ShieldAlert } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

import { ChallengeModal } from '@/components/ChallengeModal';
import { ExpiredOverlay } from '@/components/ExpiredOverlay';
import { ExpiryBanner } from '@/components/ExpiryBanner';
import { Header } from '@/components/Header';
import { LabTerminal } from '@/components/LabTerminal';
import { NewEnvironmentDialog } from '@/components/NewEnvironmentDialog';
import { RedeploymentWarningBanner } from '@/components/RedeploymentWarningBanner';
import { SessionOverlay } from '@/components/SessionOverlay';
import { Banner } from '@/components/ui/banner';
import { useDropZone } from '@/hooks/useDropZone';
import { useLabSession } from '@/hooks/useLabSession';
import { useLanguage } from '@/hooks/useLanguage';
import { useUnloadWarning } from '@/hooks/useUnloadWarning';
import { useUploads } from '@/hooks/useUploads';
import { useVisibilityRefresh } from '@/hooks/useVisibilityRefresh';
import { isStorageFull } from '@/lib/storage';

export const Lab = () => {
  const { t } = useLanguage();
  const {
    answerChallenge,
    challenge,
    containerRef,
    downloadArchive,
    endReason,
    environment,
    expiredToken,
    expiresAt,
    expiryWarning,
    focusTerminal,
    generation,
    redeploymentWarning,
    refreshStorage,
    restart,
    startFresh,
    status,
    storage,
    ticketError,
    token,
  } = useLabSession();
  // Held in state rather than a ref, because it is passed to a portal: a ref is
  // still null on the render that first mounts a dialog, and the overlay would
  // land on `document.body` — over the header — for exactly the frame somebody
  // is looking at.
  const [overlayHost, setOverlayHost] = useState<HTMLElement | null>(null);
  const running = status === 'running';
  const announcedRef = useRef(false);
  const [askingForNew, setAskingForNew] = useState(false);
  // Deliberately not remembered. Waving the warning away in the first hour
  // must not silence the last one, and a refresh is a new page.
  const [dismissedWarning, setDismissedWarning] = useState(0);
  const [dismissedStorage, setDismissedStorage] = useState(false);
  // Raised by the challenge below when a check the user never saw has failed
  // anyway. Not dismissable: it is up for as long as the thing it warns about
  // is true, and it goes on its own the moment the check is over.
  const [challengeTrouble, setChallengeTrouble] = useState(false);
  const full = storage !== null && running && isStorageFull(storage);

  // Dismissing says "I know", not "never tell me again". Freeing space and
  // filling it a second time is a new thing to know, so the dismissal only
  // lasts as long as the condition that caused it.
  useEffect(() => {
    if (!full) {
      setDismissedStorage(false);
    }
  }, [full]);

  useEffect(() => {
    if (challenge === null) {
      setChallengeTrouble(false);
    }
  }, [challenge]);

  useEffect(() => {
    // Said once per connection, and only when there was something to keep:
    // telling a first-time user their files were restored would be a lie.
    if (!(environment?.resumed === true && !announcedRef.current)) {
      return;
    }

    announcedRef.current = true;
    toast.success(t.session.resumed);
  }, [environment, t]);
  const { enqueue, uploading } = useUploads(token, refreshStorage, generation);
  const dragging = useDropZone(running, enqueue);
  useVisibilityRefresh(running, refreshStorage);
  // Files survive a refresh; the running program and the scrollback do not,
  // and an upload lives in this tab and dies with it.
  useUnloadWarning(running || uploading);

  return (
    <div className="flex h-screen flex-col bg-background">
      <Header
        expiresAt={expiresAt}
        onDownload={downloadArchive}
        onNewEnvironment={() => {
          setAskingForNew(true);
        }}
        onUpload={enqueue}
        status={status}
        storage={storage}
      />
      {/* Full-bleed, with the content centred inside it. The overlays position
          against this, so they dim everything below the header rather than
          stopping at the content column's edges and leaving a lit margin down
          both sides. */}
      <main
        className="relative flex min-h-0 flex-1 flex-col"
        ref={setOverlayHost}
      >
        <div className="container mx-auto flex min-h-0 flex-1 flex-col gap-3 py-4 sm:py-6">
          {/* A line above the terminal rather than an overlay: at 100% the
            user needs the terminal in front of them to delete something,
            and the error they hit differs from tool to tool. */}
          {expiryWarning !== null &&
            running &&
            dismissedWarning !== expiryWarning.thresholdMs && (
              <ExpiryBanner
                onDismiss={() => {
                  setDismissedWarning(expiryWarning.thresholdMs);
                }}
                onDownload={downloadArchive}
                thresholdMs={expiryWarning.thresholdMs}
              />
            )}
          {redeploymentWarning !== null && running && (
            <RedeploymentWarningBanner
              key={redeploymentWarning.deploymentId}
              presentation={redeploymentWarning}
            />
          )}
          {/* A line rather than a dialog, and deliberately so: the check
            that failed is being retried without them, there is nothing here to
            click, and the only thing they can usefully do about it is save
            whatever they would mind losing. */}
          {challengeTrouble && running && (
            <Banner
              icon={ShieldAlert}
              tone="urgent"
            >
              {t.session.challengeTrouble}
            </Banner>
          )}
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
        </div>
        {askingForNew && running && (
          <NewEnvironmentDialog
            container={overlayHost}
            onCancel={() => {
              setAskingForNew(false);
            }}
            onClosed={focusTerminal}
            onConfirm={() => {
              setAskingForNew(false);
              startFresh();
            }}
            onDownload={downloadArchive}
          />
        )}
        {challenge !== null && running && (
          <ChallengeModal
            container={overlayHost}
            deadline={challenge.deadline}
            nonce={challenge.nonce}
            onClosed={focusTerminal}
            onSolved={answerChallenge}
            onTrouble={setChallengeTrouble}
          />
        )}
        {status === 'ended' && endReason === 'environment-expired' && (
          <ExpiredOverlay
            container={overlayHost}
            environmentToken={expiredToken ?? ''}
            onStartNew={restart}
          />
        )}
        {status === 'ended' && endReason !== 'environment-expired' && (
          <SessionOverlay
            container={overlayHost}
            onRestart={restart}
            reason={endReason}
            ticketError={ticketError}
          />
        )}
      </main>
    </div>
  );
};
