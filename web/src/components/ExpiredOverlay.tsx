import { Plus } from 'lucide-react';
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
import { clearExpiredEnvironmentToken } from '@/lib/environment';
import { type ArchiveFormat } from '@/lib/protocol';
import {
  archiveTicketNotice,
  requestExpiredEnvironmentArchive,
} from '@/lib/transfer';

type ExpiredOverlayProps = {
  readonly container: HTMLElement | null;
  readonly environmentToken: string;
  readonly onStartNew: () => void;
};

// Two actions, never one. Rescuing the old work and getting back to work are
// different needs, and a user is not made to wait out the grace window
// before starting again — the expired environment finishes its countdown in
// the background while they carry on.
export const ExpiredOverlay = ({
  container,
  environmentToken,
  onStartNew,
}: ExpiredOverlayProps) => {
  const { t } = useLanguage();
  const [downloading, setDownloading] = useState(false);

  const download = async (format: ArchiveFormat) => {
    setDownloading(true);

    try {
      // A button that dims and then does nothing is worse than an error: the
      // user has no way to tell a slow download from a dead one, and no
      // words to search for or report.
      const result = await requestExpiredEnvironmentArchive(
        environmentToken,
        format,
      );
      const notice = archiveTicketNotice(t.archive, result);
      if (notice.kind === 'success') {
        toast.success(notice.message);
      } else {
        toast.error(notice.message);
      }
    } finally {
      setDownloading(false);
    }
  };

  return (
    // Not dismissable: the session it was covering is gone, so there is nothing
    // behind this to go back to.
    <Dialog
      modal={false}
      open
    >
      <DialogPortal container={container}>
        <DialogOverlay className="z-10" />
        <DialogContent
          className="z-10 max-w-md gap-5 p-8"
          onEscapeKeyDown={(event) => {
            event.preventDefault();
          }}
          onInteractOutside={(event) => {
            event.preventDefault();
          }}
        >
          <div className="flex flex-col gap-2">
            <DialogTitle>{t.session.expiredTitle}</DialogTitle>
            <DialogDescription>{t.session.expiredBody}</DialogDescription>
          </div>
          {/* Stacked and equal width: they are two answers to the same
              question, and sizing them by the length of their labels made one
              look like the main one by accident. */}
          <div className="flex w-full flex-col gap-2">
            <DownloadButton
              block
              disabled={downloading}
              label={t.session.expiredDownload}
              onDownload={(format) => {
                void download(format);
              }}
            />
            <button
              className="inline-flex h-10 w-full cursor-pointer items-center justify-center gap-2 rounded-md border border-input bg-background px-4 text-sm font-medium transition-colors hover:bg-accent hover:text-accent-foreground"
              onClick={() => {
                clearExpiredEnvironmentToken();
                onStartNew();
              }}
              type="button"
            >
              <Plus
                aria-hidden="true"
                className="h-4 w-4"
              />
              {t.session.expiredStartNew}
            </button>
          </div>
        </DialogContent>
      </DialogPortal>
    </Dialog>
  );
};
