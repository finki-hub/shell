import { RotateCcw } from 'lucide-react';

import {
  Dialog,
  DialogContent,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
} from '@/components/ui/dialog';
import { useLanguage } from '@/hooks/useLanguage';
import {
  type TicketError,
  ticketErrorMessage,
} from '@/lib/lab-session-protocol';
import { type ClientEndReason } from '@/lib/protocol';

type SessionOverlayProps = {
  readonly container: HTMLElement | null;
  readonly onRestart: () => void;
  // Everything except the expired case, which has a screen of its own with its
  // own words and its own two buttons. Stated in the type rather than trusted:
  // the string that used to live here for it was dead, and dead text drifts —
  // it had already come to contradict the screen that does render.
  readonly reason: Exclude<ClientEndReason, 'environment-expired'> | null;
  readonly ticketError: null | TicketError;
};

// Always "Try again", never "New session". Pressing it reconnects to the same
// environment — the token is still in storage, so the files, the deadline and
// the history are all the ones the user had a moment ago. The only reason
// that genuinely starts something new has its own screen, with its own button
// that says so. Offering "New session" here suggested giving something up in
// order to carry on.
export const SessionOverlay = ({
  container,
  onRestart,
  reason,
  ticketError,
}: SessionOverlayProps) => {
  const { t } = useLanguage();

  return (
    // Not dismissable, for the same reason as the expiry screen: the session is
    // over, and dismissing this would reveal a dead terminal.
    <Dialog
      modal={false}
      open
    >
      <DialogPortal container={container}>
        <DialogOverlay className="z-10" />
        <DialogContent
          // The sentence *is* the dialog's name here — there is no separate
          // heading to write, and inventing one would only repeat it.
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
            {ticketError === null
              ? t.reasons[reason ?? 'connection-closed']
              : ticketErrorMessage(t.ticket.errors, ticketError)}
          </DialogTitle>
          <button
            className="inline-flex h-10 cursor-pointer items-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
            onClick={onRestart}
            type="button"
          >
            <RotateCcw
              aria-hidden="true"
              className="h-4 w-4"
            />
            {t.session.tryAgain}
          </button>
        </DialogContent>
      </DialogPortal>
    </Dialog>
  );
};
