import { ShieldCheck } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
} from '@/components/ui/dialog';
import { useChallengeElection } from '@/hooks/useChallengeElection';
import { useLanguage } from '@/hooks/useLanguage';
import {
  type AttentionLease,
  clearAttention,
  playChime,
  raiseAttention,
  refreshAttention,
} from '@/lib/attention';
import { solveChallenge } from '@/lib/turnstile';

type ChallengeModalProps = {
  // Where the overlay is mounted. These cover the terminal and stop at the
  // header, which a portal to `document.body` cannot do.
  readonly container: HTMLElement | null;
  readonly deadline: number;
  readonly nonce: string;
  // Where focus goes when this closes. Radix returns it to whatever had it when
  // the dialog opened, which by then is usually gone — the user clicked into
  // the Turnstile iframe — and focus lands on the body instead, leaving the
  // terminal deaf to the keyboard until it is clicked.
  readonly onClosed: () => void;
  readonly onSolved: (token: string) => void;
  // Raised when a check nobody was asked to answer fails anyway, and lowered if
  // it goes on to ask for a person. What it drives is a line above the terminal
  // rather than anything in here: there is nothing for the user to click, and
  // the one useful thing to tell them is that the session may not survive it.
  readonly onTrouble: (trouble: boolean) => void;
};

// Long enough that an instantly-failing challenge does not become a busy loop,
// short enough to be several more tries inside a window measured in minutes.
const RETRY_DELAY_MS = 3_000;

// How long an answer is given to be acted on before it is assumed lost. The
// answer path has no acknowledgement — a token goes out over the socket and
// either the challenge clears or a fresh nonce arrives — so nothing else would
// ever notice a frame that went nowhere, and the session would die at the
// deadline with a user who answered correctly watching it. Comfortably past
// the server's own answer gap and the tick that re-asks after it.
const ACK_TIMEOUT_MS = 12_000;

type Timer = ReturnType<typeof setTimeout>;

const formatRemaining = (ms: number) => {
  const total = Math.max(0, Math.round(ms / 1_000));
  const minutes = Math.floor(total / 60);

  return `${String(minutes)}:${String(total % 60).padStart(2, '0')}`;
};

// A countdown is right here and wrong for the environment deadline: this one
// is minutes long and the user has to act inside it, which is precisely
// when a ticking number is information rather than pressure.
const useCountdown = (deadline: number) => {
  const [remaining, setRemaining] = useState(() => deadline - Date.now());

  useEffect(() => {
    const interval = setInterval(() => {
      setRemaining(deadline - Date.now());
    }, 1_000);

    return () => {
      clearInterval(interval);
    };
  }, [deadline]);

  return remaining;
};

// The elected tab starts on its own, and keeps going on its own. A button would
// put a click in front of a challenge that mostly resolves without one, and a
// failed attempt is not something the user can answer better by being asked
// to click — the window is the only thing that should end this.
const useChallengeSolver = ({
  nonce,
  onSolved,
  onTrouble,
}: Pick<ChallengeModalProps, 'nonce' | 'onSolved' | 'onTrouble'>) => {
  const { t } = useLanguage();
  const elected = useChallengeElection(nonce);
  const widgetRef = useRef<HTMLDivElement | null>(null);
  const attentionLeaseRef = useRef<AttentionLease | null>(null);
  const attentionPrefixRef = useRef(t.session.challengeAlert);
  // Whether this check has already asked for the user's attention, and
  // whether it has already asked for a click. Both are read from inside the
  // attempt loop, which closes over the state it was started with.
  const alertedRef = useRef(false);
  const interactiveRef = useRef(false);
  const [failed, setFailed] = useState(false);
  // Sticky: once a check has asked for a person the dialog stays until the
  // check is over. Taking it away again between two attempts would flap a modal
  // in and out of the face of somebody trying to answer it.
  const [interactive, setInteractive] = useState(false);
  // Not the same thing: this is whether a widget is on screen *right now*. The
  // pause between two attempts has none, and a reserved empty box reads as
  // broken where a spinner reads as the wait it is.
  const [widgetUp, setWidgetUp] = useState(false);

  const clearOwnedAttention = useCallback(() => {
    const lease = attentionLeaseRef.current;
    if (lease === null) {
      return;
    }

    clearAttention(lease);
    attentionLeaseRef.current = null;
  }, []);

  useEffect(() => {
    attentionPrefixRef.current = t.session.challengeAlert;
    const lease = attentionLeaseRef.current;
    if (lease !== null) {
      refreshAttention(lease, attentionPrefixRef.current);
    }
  }, [t.session.challengeAlert]);

  // A tab that stops being the one answering must not keep what it was
  // showing. The abort below takes the widget with it, and what would be left
  // is an empty box with no spinner, no way out, and a scrim over a terminal
  // the user can no longer click — for as long as the window lasts.
  useEffect(() => {
    if (elected === true) {
      return;
    }

    interactiveRef.current = false;
    alertedRef.current = false;
    clearOwnedAttention();
    setFailed(false);
    setInteractive(false);
    setWidgetUp(false);
    onTrouble(false);
  }, [clearOwnedAttention, elected, onTrouble]);

  useEffect(() => {
    // Undecided is not the same as lost: a re-ask leaves this null until the
    // new round settles, and starting an attempt on the previous round's
    // answer is how two widgets ended up in one node.
    if (elected !== true) {
      return () => {
        // Another tab is answering, or nobody has been chosen yet.
      };
    }

    // AbortController provides native, composable cancellation across awaited
    // work and effect cleanup.
    const run = new AbortController();
    let timer: null | Timer = null;
    // Each current-state predicate call reads the signal's current state.
    const stopped = () => run.signal.aborted;

    // One interruption per check, whichever half of it came first. A user
    // called back to the tab by a failing check does not need calling again
    // when that same check turns into a puzzle.
    const callForAttention = () => {
      if (alertedRef.current) {
        return;
      }

      alertedRef.current = true;
      attentionLeaseRef.current = raiseAttention(attentionPrefixRef.current);
      playChime();
    };

    const attempt = async () => {
      const target = widgetRef.current;

      if (target === null || stopped()) {
        return;
      }

      // The server's nonce, not one of our own: it is what the pending
      // challenge is bound to, and Cloudflare echoes back the one the widget
      // was rendered with.
      const solved = await solveChallenge('session_keep', {
        issued: nonce,
        // The one moment worth interrupting somebody for, and the one tab worth
        // interrupting them in: this is the tab holding the widget, and the
        // others have nothing for them to do.
        onInteractive: () => {
          interactiveRef.current = true;
          setInteractive(true);
          setWidgetUp(true);
          // The banner said the session might close, which was true while there
          // was nothing to be done about it. Now there is, and the dialog in
          // front of them says so in more useful words.
          onTrouble(false);
          callForAttention();
        },
        signal: run.signal,
        target,
      });

      if (stopped()) {
        // The election moved, or the check was answered elsewhere.
      } else if (solved === null) {
        setFailed(true);
        setWidgetUp(false);

        // A check nobody was asked to answer has just failed, and the user
        // is looking at a terminal that shows no sign of it. Retries continue
        // either way; what they cannot work out for themselves is that the
        // session is now the thing at risk.
        if (!interactiveRef.current) {
          onTrouble(true);
          callForAttention();
        }

        // Paced rather than immediate. A challenge that fails the moment it
        // starts — no network, a blocked host — would otherwise retry as
        // fast as the event loop allows for the whole window.
        timer = setTimeout(() => {
          void attempt();
        }, RETRY_DELAY_MS);
      } else {
        onSolved(solved.token);
        // Nothing replies to an answer. If it was taken, the challenge clears
        // and this component goes with it; if it was refused for arriving too
        // soon, the server re-asks and the new nonce restarts this effect.
        // Neither happening means the answer was lost, and this is the only
        // thing that will ever notice.
        timer = setTimeout(() => {
          void attempt();
        }, ACK_TIMEOUT_MS);
      }
    };

    void attempt();

    return () => {
      run.abort();
      clearOwnedAttention();

      if (timer !== null) {
        clearTimeout(timer);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one run per election, not one per render
  }, [clearOwnedAttention, elected, nonce]);

  return { failed, interactive, widgetRef, widgetUp };
};

// Mounted for every check and shown for almost none of them. The dialog is
// built the moment a check starts — Turnstile renders into the node it is given
// and moving that node afterwards reloads the widget, so there is no version of
// this that mounts late — and then held invisible until Turnstile says it needs
// a person. A user who is asked nothing is interrupted by nothing: no card
// taking their focus off the terminal, no scrim over the work they are doing,
// nothing to explain afterwards.
export const ChallengeModal = ({
  container,
  deadline,
  nonce,
  onClosed,
  onSolved,
  onTrouble,
}: ChallengeModalProps) => {
  const { t } = useLanguage();
  const contentRef = useRef<HTMLDivElement | null>(null);
  const { failed, interactive, widgetRef, widgetUp } = useChallengeSolver({
    nonce,
    onSolved,
    onTrouble,
  });
  const remaining = useCountdown(deadline);

  // Only when the dialog becomes visible. Radix moves focus in on mount, which
  // for an invisible dialog would take the keyboard away from the terminal for
  // a check nobody was asked to answer.
  useEffect(() => {
    if (interactive) {
      contentRef.current?.focus();
    }
  }, [interactive]);

  return (
    <Dialog
      modal={false}
      open
    >
      <DialogPortal container={container}>
        {interactive && <DialogOverlay />}
        {/* There is deliberately no way out of this one: the session ends if
            the check goes unanswered, so a close button would be a button that
            loses your shell, and Escape would be the same button with no
            label. */}
        <DialogContent
          aria-hidden={!interactive}
          className={interactive ? undefined : 'pointer-events-none'}
          onCloseAutoFocus={(event) => {
            event.preventDefault();

            // Only for a dialog that was actually shown. Radix fires this on
            // every unmount, and this component mounts for every check in every
            // tab — so a silent one, which is nearly all of them, used to end by
            // pulling the keyboard back to the terminal out of whatever the
            // user was really doing.
            if (interactive) {
              onClosed();
            }
          }}
          onEscapeKeyDown={(event) => {
            event.preventDefault();
          }}
          onInteractOutside={(event) => {
            event.preventDefault();
          }}
          onOpenAutoFocus={(event) => {
            event.preventDefault();
          }}
          ref={contentRef}
          // Inline rather than a class, and one of the few places that earns
          // it: the entrance animation is keyed on the open state, which is
          // true from the moment this mounts. Suppressing it from the
          // stylesheet would be a race with Tailwind's own ordering, and an
          // animation that plays while the dialog is transparent is an
          // animation nobody sees. Dropping the declaration is what starts it.
          style={interactive ? undefined : { animation: 'none', opacity: 0 }}
        >
          <ShieldCheck
            aria-hidden="true"
            className="h-6 w-6 text-primary"
          />
          <div className="flex flex-col gap-1.5">
            <DialogTitle>{t.session.challengeTitle}</DialogTitle>
            <DialogDescription>
              {widgetUp
                ? t.session.challengeBody
                : t.session.challengeVerifying}
            </DialogDescription>
          </div>
          {/* Reserved whether or not a widget ever appears: parking it does not
              prevent the reflow, and a dialog that jumps as the challenge loads
              is a dialog the user mis-clicks. */}
          <div className="flex min-h-[70px] w-full items-center justify-center">
            {!widgetUp && (
              <span
                aria-hidden="true"
                className="size-5 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-muted-foreground"
              />
            )}
            <div ref={widgetRef} />
          </div>
          {/* Labelled, because a bare 4:59 under a dialog with no way out is a
              number the user has to guess the meaning of. */}
          <p className="text-xs text-muted-foreground">
            {failed ? `${t.session.challengeFailed} ` : ''}
            {t.session.challengeRemaining}{' '}
            <span className="tabular-nums">{formatRemaining(remaining)}</span>
          </p>
        </DialogContent>
      </DialogPortal>
    </Dialog>
  );
};
