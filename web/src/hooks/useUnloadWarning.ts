import { useEffect } from 'react';

// The one place a native browser prompt is warranted: a refresh or a tab close
// takes the running program with it. Files survive — that is the whole design —
// but an unsaved buffer and a half-finished build do not, and neither does the
// scrollback.
//
// Only while there is something to lose. A prompt on every close would be
// trained away within a week, and then it would not be there on the day it
// mattered.
export const useUnloadWarning = (active: boolean) => {
  useEffect(() => {
    if (!active) {
      return () => {
        // Nothing was registered.
      };
    }

    const warn = (event: BeforeUnloadEvent) => {
      // The wording is the browser's, not ours: every browser replaced custom
      // text years ago because it was used to lie to people.
      event.preventDefault();
    };

    addEventListener('beforeunload', warn);

    return () => {
      removeEventListener('beforeunload', warn);
    };
  }, [active]);
};
