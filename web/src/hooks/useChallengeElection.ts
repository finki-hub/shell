import { useCallback, useEffect, useState } from 'react';

import {
  acquireChallengeHolder,
  type ChallengeHolder,
} from '@/lib/challenge-holder';
import {
  CHALLENGE_CHANNEL,
  ELECTION_HEARTBEAT_MS,
  ELECTION_SILENCE_MS,
  ELECTION_WINDOW_MS,
} from '@/lib/constants';
import { scoreTab, wins } from '@/lib/election';

type Claim = {
  id: string;
  lastActive: number;
  nonce: string;
  score: number;
  type: 'claim';
};

// `holding` is both a heartbeat and a veto. A tab that is already answering
// says so in reply to any claim it hears, and a tab still inside its election
// window treats hearing one as losing: an election is only ever a question
// about tabs that are not already doing the job.
type Note = { id: string; nonce: string; type: 'holding' | 'released' };

type Traffic = Claim | Note;

const parseTraffic = (value: unknown): null | Traffic => {
  if (
    typeof value !== 'object' ||
    value === null ||
    !('id' in value) ||
    typeof value.id !== 'string' ||
    !('nonce' in value) ||
    typeof value.nonce !== 'string' ||
    !('type' in value)
  ) {
    return null;
  }

  if (value.type === 'holding' || value.type === 'released') {
    return { id: value.id, nonce: value.nonce, type: value.type };
  }

  if (
    value.type !== 'claim' ||
    !('lastActive' in value) ||
    typeof value.lastActive !== 'number' ||
    !('score' in value) ||
    typeof value.score !== 'number'
  ) {
    return null;
  }

  return {
    id: value.id,
    lastActive: value.lastActive,
    nonce: value.nonce,
    score: value.score,
    type: value.type,
  };
};

const identity = crypto.randomUUID();

const createHolderResources = (channel: BroadcastChannel, nonce: string) => ({
  announceRelease: () => {
    channel.postMessage({ id: identity, nonce, type: 'released' });
  },
  scheduleHeartbeat: (heartbeat: () => void) => {
    const interval = setInterval(heartbeat, ELECTION_HEARTBEAT_MS);

    return () => {
      clearInterval(interval);
    };
  },
  subscribePagehide: (listener: () => void) => {
    addEventListener('pagehide', listener);

    return () => {
      removeEventListener('pagehide', listener);
    };
  },
});

// Updated by real interaction, so a tab the user left open a week ago
// cannot outrank the one they were just typing in.
const activity = { at: Date.now() };

const markActive = () => {
  activity.at = Date.now();
};

const score = () => scoreTab(document.hasFocus(), document.hidden);

// Exactly one tab answers; the rest show nothing at all. Without this a
// user with four tabs open gets four widgets for one challenge, four
// answers for one solve, and three stale dialogs left behind by whichever of
// them wins.
// Three states, not two. A decision belongs to the nonce and the round it was
// taken for, and anything else is *undecided* — which is a different answer
// from "you lost". Returning false while a new nonce was being settled meant
// the caller could not tell the two apart, and the one that mattered was a
// re-ask: the nonce prop changed, this hook only *scheduled* the loser state,
// and the caller's effect ran once more against the previous round's `true`.
// That started an attempt nobody wanted, whose widget then sat in the page
// beside the real one.
export const useChallengeElection = (nonce: null | string): boolean | null => {
  // Seeded with a round no election can have rather than with null, so the
  // check below is a plain comparison: a nullable one is either an optional
  // chain the linter asks for or a null test it rejects, depending on which
  // way round it is written.
  const [decision, setDecision] = useState<{
    elected: boolean;
    nonce: string;
    round: number;
  }>({ elected: false, nonce: '', round: -1 });
  const [round, setRound] = useState(0);

  const reelect = useCallback(() => {
    setRound((current) => current + 1);
  }, []);

  useEffect(() => {
    for (const event of ['keydown', 'pointerdown']) {
      addEventListener(event, markActive, { passive: true });
    }

    return () => {
      for (const event of ['keydown', 'pointerdown']) {
        removeEventListener(event, markActive);
      }
    };
  }, []);

  useEffect(() => {
    if (nonce === null) {
      return () => {
        // Nothing was started, so there is nothing to take down.
      };
    }

    const channel = new BroadcastChannel(CHALLENGE_CHANNEL);
    const mine: Claim = {
      id: identity,
      lastActive: activity.at,
      nonce,
      score: score(),
      type: 'claim',
    };
    const claims = [mine];
    // Whether this tab is answering, and whether it has already been told not
    // to bother. Both are read from the message handler, which is why neither
    // is state: a render would be a round trip the 300ms window cannot afford.
    const status: {
      conceded: boolean;
      holder: ChallengeHolder | null;
      holding: boolean;
    } = { conceded: false, holder: null, holding: false };
    const timers: {
      silence: null | ReturnType<typeof setTimeout>;
      window: null | ReturnType<typeof setTimeout>;
    } = { silence: null, window: null };

    const announceHold = () => {
      channel.postMessage({ id: identity, nonce, type: 'holding' });
    };

    const standDown = (announce: boolean) => {
      if (!status.holding) {
        return;
      }

      status.holding = false;
      if (announce) {
        status.holder?.release();
      } else {
        status.holder?.demote();
      }
      status.holder = null;
    };

    // The elected tab can be closed, crash, or be navigated away from without
    // ever solving, and nothing else would notice: the survivors would wait out
    // the window in silence and the session would die with the user watching
    // a terminal that looked fine. So the holder says so, repeatedly, and
    // silence is itself the signal to elect somebody else. A holder that is
    // merely slow answers the resulting claim with a veto rather than losing
    // its place.
    const watchForSilence = () => {
      if (timers.silence !== null) {
        clearTimeout(timers.silence);
      }

      timers.silence = setTimeout(reelect, ELECTION_SILENCE_MS);
    };

    // [measured] A single broadcast loses the race it is trying to win. Every
    // tab is told about the challenge at the same instant, so each posts its
    // claim within a millisecond or two of the others attaching their
    // listeners — and BroadcastChannel does not replay, so a claim sent before
    // a listener existed is simply never seen. Both tabs then concluded they
    // were alone and both showed the modal, which is the one outcome the
    // election exists to prevent.
    //
    // Answering a stranger's claim with your own fixes it without a handshake:
    // whoever was late learns about whoever was early, and because the reply
    // only fires for an id nobody has seen yet, it terminates.
    const seen = new Set<string>([identity]);

    channel.addEventListener('message', (event: MessageEvent<unknown>) => {
      const message = parseTraffic(event.data);

      if (message?.nonce !== nonce) {
        return;
      }

      if (message.type === 'claim') {
        claims.push(message);

        // Answered even when the claim is one this tab has already seen, and
        // that is the point: `seen` lives in a closure keyed on the nonce and
        // the round, so a tab re-electing itself after a false silence posts a
        // claim whose id the holder recognises — and used to be met with
        // nothing at all. The re-election then won uncontested against a tab
        // that was alive and answering.
        if (status.holding) {
          announceHold();
        } else if (!seen.has(message.id)) {
          // The reply that settles a simultaneous start. Two tabs told about
          // the same challenge in the same instant each post before the other
          // is listening, and BroadcastChannel does not replay; answering a
          // stranger's claim with your own is what stops both concluding they
          // were alone. Only for ids nobody has seen, so it terminates.
          seen.add(message.id);
          channel.postMessage(mine);
        }
      } else if (message.type === 'released') {
        reelect();
      } else {
        watchForSilence();

        if (status.holding) {
          // Two holders, which the veto above is meant to prevent and a frozen
          // tab can still produce: one of them was asleep while the other was
          // elected. Resolved the way `beats` resolves everything else it
          // cannot separate — by id, so both tabs reach the same answer and
          // exactly one of them stands down.
          if (message.id > identity) {
            standDown(false);
            setDecision({ elected: false, nonce, round });
          }
        } else {
          status.conceded = true;
        }
      }
    });
    channel.postMessage(mine);

    // A short window rather than a handshake: the grace period is minutes
    // long, so a few hundred milliseconds spent agreeing costs nothing, and a
    // tab that answers late simply loses.
    timers.window = setTimeout(() => {
      // Conceding beats winning. A tab that heard a holder during its window is
      // not choosing between candidates any more — the job is taken.
      const won = !status.conceded && wins(mine, claims);
      status.holding = won;
      setDecision({ elected: won, nonce, round });

      if (won) {
        status.holder = acquireChallengeHolder(
          announceHold,
          createHolderResources(channel, nonce),
        );
      } else {
        watchForSilence();
      }
    }, ELECTION_WINDOW_MS);

    return () => {
      standDown(true);

      for (const timer of [timers.window, timers.silence]) {
        if (timer !== null) {
          clearTimeout(timer);
        }
      }
      channel.close();
    };
  }, [nonce, reelect, round]);

  // Derived rather than stored, so a new nonce is undecided during the very
  // render that introduces it. A retry changes the nonce, and carrying the
  // previous election's answer into it would let two tabs both believe they
  // were elected and both answer — burning two of the window's attempts on one
  // solve, one of them against a nonce the other had already spent.
  const settled = decision.nonce === nonce && decision.round === round;

  return settled ? decision.elected : null;
};
