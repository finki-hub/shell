import { useEffect } from 'react';

export const useVisibilityRefresh = (enabled: boolean, refresh: () => void) => {
  useEffect(() => {
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        refresh();
      }
    };
    if (enabled) {
      document.addEventListener('visibilitychange', handleVisibility);
      addEventListener('focus', refresh);
    }

    return () => {
      document.removeEventListener('visibilitychange', handleVisibility);
      removeEventListener('focus', refresh);
    };
  }, [enabled, refresh]);
};
