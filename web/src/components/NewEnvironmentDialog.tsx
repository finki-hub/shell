import { useState } from 'react';
import { toast } from 'sonner';

import { DownloadButton } from '@/components/DownloadButton';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
} from '@/components/ui/dialog';
import { useLanguage } from '@/hooks/useLanguage';
import { type ArchiveFormat } from '@/lib/contents-api';

type NewEnvironmentDialogProps = {
  readonly container: HTMLElement | null;
  readonly onCancel: () => void;
  readonly onClosed: () => void;
  readonly onConfirm: () => Promise<boolean>;
  readonly onDownload: (format: ArchiveFormat) => void;
};

// Asked before, not explained after. Starting a new environment deletes the
// current one outright, so the download sits in the same dialog as the warning
// rather than somewhere the user has to go and find — it is the only
// safeguard, and the only one there is meant to be.
export const NewEnvironmentDialog = ({
  container,
  onCancel,
  onClosed,
  onConfirm,
  onDownload,
}: NewEnvironmentDialogProps) => {
  const { t } = useLanguage();
  const [downloaded, setDownloaded] = useState(false);
  const [discarding, setDiscarding] = useState(false);

  return (
    // The only one of these that is a question rather than a statement, so it
    // is the only one Escape and a click outside are allowed to answer — both
    // mean "no", which is the safe answer to "shall I delete this".
    <Dialog
      modal={false}
      onOpenChange={(open) => {
        if (!open) {
          onCancel();
        }
      }}
      open
    >
      <DialogPortal container={container}>
        <DialogOverlay className="z-30" />
        <DialogContent
          className="z-30 items-stretch"
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            onClosed();
          }}
        >
          <div className="flex flex-col gap-2 text-center">
            <DialogTitle>{t.session.newEnvironmentTitle}</DialogTitle>
            <DialogDescription>
              {t.session.newEnvironmentBody}
            </DialogDescription>
          </div>
          <DownloadButton
            block
            disabled={discarding}
            label={t.actions.downloadAll}
            onDownload={(format) => {
              setDownloaded(true);
              onDownload(format);
            }}
          />
          <div className="flex gap-2">
            <button
              className="inline-flex h-10 flex-1 cursor-pointer items-center justify-center rounded-md border border-input bg-background px-4 text-sm font-medium transition-colors hover:bg-accent hover:text-accent-foreground"
              onClick={onCancel}
              type="button"
            >
              {t.actions.cancel}
            </button>
            <button
              className="inline-flex h-10 flex-1 cursor-pointer items-center justify-center rounded-md bg-destructive px-4 text-sm font-medium text-destructive-foreground transition-colors hover:bg-destructive/90"
              disabled={discarding}
              onClick={() => {
                if (!downloaded) {
                  // Said once, not enforced. It is their environment, and a
                  // dialog that refuses to proceed is a dialog people learn to
                  // dismiss without reading.
                  toast.warning(t.session.newEnvironmentNotDownloaded);
                }
                setDiscarding(true);
                void (async () => {
                  const discarded = await onConfirm();
                  if (!discarded) setDiscarding(false);
                })();
              }}
              type="button"
            >
              {t.session.newEnvironmentConfirm}
            </button>
          </div>
        </DialogContent>
      </DialogPortal>
    </Dialog>
  );
};
