import { useEffect } from 'react';

// A tab that was in the background may have missed several polls, and the
// first thing a returning user looks at is the number in the header.
export const useVisibilityRefresh = (enabled: boolean, refresh: () => void) => {
  useEffect(() => {
    if (!enabled) {
      return () => {
        // Nothing was attached.
      };
    }

    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        refresh();
      }
    };

    document.addEventListener('visibilitychange', handleVisibility);
    addEventListener('focus', refresh);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibility);
      removeEventListener('focus', refresh);
    };
  }, [enabled, refresh]);
};
