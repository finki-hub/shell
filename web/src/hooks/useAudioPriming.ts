import { useEffect } from 'react';

import { primeAudio } from '@/lib/attention';

export const useAudioPriming = (): void => {
  useEffect(() => {
    const prime = (): void => {
      primeAudio();
    };
    for (const event of ['keydown', 'pointerdown']) {
      addEventListener(event, prime, { once: true, passive: true });
    }
    return () => {
      for (const event of ['keydown', 'pointerdown']) {
        removeEventListener(event, prime);
      }
    };
  }, []);
};
