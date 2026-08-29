export type ChallengeHolder = {
  readonly demote: () => void;
  readonly release: () => void;
};

type HolderResources = {
  readonly announceRelease: () => void;
  readonly scheduleHeartbeat: (heartbeat: () => void) => () => void;
  readonly subscribePagehide: (release: () => void) => () => void;
};

export const acquireChallengeHolder = (
  announceHold: () => void,
  resources: HolderResources,
): ChallengeHolder => {
  let active = true;
  let removePagehide: (() => void) | null = null;
  const stopHeartbeat = resources.scheduleHeartbeat(announceHold);

  const standDown = (announce: boolean) => {
    if (!active) {
      return;
    }

    active = false;
    stopHeartbeat();
    removePagehide?.();
    if (announce) {
      resources.announceRelease();
    }
  };

  const release = () => {
    standDown(true);
  };
  removePagehide = resources.subscribePagehide(release);

  return {
    demote: () => {
      standDown(false);
    },
    release,
  };
};
