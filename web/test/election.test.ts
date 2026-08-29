import { describe, expect, it } from 'vitest';

import { beats, type Claim, scoreTab, wins } from '@/lib/election';

const claim = (partial: Partial<Claim>): Claim => ({
  id: 'a',
  lastActive: 0,
  score: 1,
  ...partial,
});

describe('scoring a tab', () => {
  it('ranks focused above visible above hidden', () => {
    expect(scoreTab(true, false)).toBeGreaterThan(scoreTab(false, false));
    expect(scoreTab(false, false)).toBeGreaterThan(scoreTab(false, true));
  });

  it('treats a visible tab behind another window as second best', () => {
    // The case this exists for: a tab that reports `visible` while the user
    // is looking at something else entirely. A modal there goes unanswered,
    // and the session dies with them sitting in front of the screen.
    expect(scoreTab(false, false)).toBeLessThan(scoreTab(true, false));
  });
});

describe('electing one tab', () => {
  it('prefers the focused tab over a merely visible one', () => {
    const focused = claim({ id: 'focused', score: 2 });
    const visible = claim({ id: 'visible', score: 1 });

    expect(wins(focused, [focused, visible])).toBe(true);
    expect(wins(visible, [focused, visible])).toBe(false);
  });

  it('falls back to whichever was used most recently', () => {
    const recent = claim({ id: 'recent', lastActive: 200 });
    const stale = claim({ id: 'stale', lastActive: 100 });

    expect(wins(recent, [recent, stale])).toBe(true);
    expect(wins(stale, [recent, stale])).toBe(false);
  });

  it('elects exactly one tab when every tab looks identical', () => {
    // Two hidden tabs opened together have the same score and the same
    // history. Without the last tiebreak they would either both show the
    // modal or, worse, both stand down and let the session die unanswered.
    const first = claim({ id: 'aaa', lastActive: 0, score: 0 });
    const second = claim({ id: 'bbb', lastActive: 0, score: 0 });
    const all = [first, second];
    const winners = all.filter((tab) => wins(tab, all));

    expect(winners).toHaveLength(1);
  });

  it('elects exactly one out of any crowd', () => {
    const crowd = [
      claim({ id: 'a', lastActive: 5, score: 0 }),
      claim({ id: 'b', lastActive: 5, score: 1 }),
      claim({ id: 'c', lastActive: 9, score: 1 }),
      claim({ id: 'd', lastActive: 1, score: 2 }),
      claim({ id: 'e', lastActive: 1, score: 2 }),
    ];

    expect(crowd.filter((tab) => wins(tab, crowd))).toHaveLength(1);
  });

  it('elects nobody extra when a tab only hears about itself', () => {
    // [measured] The failure this guards against is not in the ordering, it is
    // in what reaches it. Every tab is told about a challenge at the same
    // instant and posts its claim within a millisecond of the others attaching
    // their listeners; BroadcastChannel does not replay, so a claim sent before
    // a listener existed is never seen. Both tabs then evaluated `wins` against
    // a list containing only themselves — and both won.
    const alone = claim({ id: 'aaa', lastActive: 5, score: 2 });
    const alsoAlone = claim({ id: 'bbb', lastActive: 9, score: 2 });

    expect(wins(alone, [alone])).toBe(true);
    expect(wins(alsoAlone, [alsoAlone])).toBe(true);

    // Once each has heard the other, exactly one of them still wins.
    const both = [alone, alsoAlone];

    expect(both.filter((tab) => wins(tab, both))).toHaveLength(1);
  });

  it('elects the only tab there is', () => {
    const only = claim({ id: 'only' });

    expect(wins(only, [only])).toBe(true);
  });

  it('never says a tab beats itself', () => {
    const tab = claim({ id: 'self' });

    expect(beats(tab, tab)).toBe(false);
  });
});
