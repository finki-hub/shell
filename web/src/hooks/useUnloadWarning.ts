import { useEffect } from 'react';

// Warn only while active runtime state could be lost on unload.
export const useUnloadWarning = (active: boolean) => {
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      // Browsers provide the prompt text.
      event.preventDefault();
    };
    if (active) {
      addEventListener('beforeunload', warn);
    }

    return () => {
      removeEventListener('beforeunload', warn);
    };
  }, [active]);
};
