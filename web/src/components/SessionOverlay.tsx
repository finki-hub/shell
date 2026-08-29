import { RotateCcw } from 'lucide-react';

import {
  Dialog,
  DialogContent,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
} from '@/components/ui/dialog';
import { useLanguage } from '@/hooks/useLanguage';
import { isRetryable, type SessionEnd } from '@/lib/end-reasons';

type SessionOverlayProps = {
  readonly container: HTMLElement | null;
  readonly end: SessionEnd;
  readonly onRestart: () => void;
  readonly onStartFresh: () => void;
};

export const SessionOverlay = ({
  container,
  end,
  onRestart,
  onStartFresh,
}: SessionOverlayProps) => {
  const { t } = useLanguage();
  const retryable = isRetryable(end.reason);

  return (
    <Dialog
      modal={false}
      open
    >
      <DialogPortal container={container}>
        <DialogOverlay className="z-10" />
        <DialogContent
          aria-describedby={undefined}
          className="z-10 max-w-md gap-5 p-8"
          onEscapeKeyDown={(event) => {
            event.preventDefault();
          }}
          onInteractOutside={(event) => {
            event.preventDefault();
          }}
        >
          <DialogTitle className="text-sm font-normal tracking-normal">
            {t.reasons[end.reason]}
          </DialogTitle>
          {end.message !== null && (
            <p className="text-sm text-muted-foreground">{end.message}</p>
          )}
          <button
            className="inline-flex h-10 cursor-pointer items-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
            onClick={retryable ? onRestart : onStartFresh}
            type="button"
          >
            <RotateCcw
              aria-hidden="true"
              className="h-4 w-4"
            />
            {retryable ? t.session.tryAgain : t.session.startNew}
          </button>
        </DialogContent>
      </DialogPortal>
    </Dialog>
  );
};
