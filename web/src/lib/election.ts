// Which tab shows the challenge. Pure, and separated from the hook that uses
// it, because the ordering is the part with edges: three states, a recency
// tiebreak behind them, and a stable last resort — and a wrong answer here is
// either two modals for one challenge or none at all.
export type Claim = {
  id: string;
  lastActive: number;
  score: number;
};

// Focus beats visible beats hidden. A visible tab behind another window still
// reports `visible`, which is exactly the case where the user is not
// looking at it and a modal there would go unanswered.
export const scoreTab = (focused: boolean, hidden: boolean) => {
  if (focused) {
    return 2;
  }

  return hidden ? 0 : 1;
};

export const beats = (left: Claim, right: Claim) => {
  if (left.score !== right.score) {
    return left.score > right.score;
  }

  if (left.lastActive !== right.lastActive) {
    return left.lastActive > right.lastActive;
  }

  // A stable tiebreak, so two tabs with identical histories still agree on
  // which of them is elected instead of both showing the modal, or neither.
  return left.id > right.id;
};

export const wins = (mine: Claim, claims: readonly Claim[]) =>
  claims.every((claim) => claim.id === mine.id || beats(mine, claim));
