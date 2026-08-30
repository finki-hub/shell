import { type RefObject, useEffect } from 'react';

export const useDismissable = (
  open: boolean,
  ref: RefObject<HTMLElement | null>,
  close: () => void,
) => {
  useEffect(() => {
    const handlePointerDown = (event: PointerEvent) => {
      if (
        ref.current !== null &&
        event.target instanceof Node &&
        !ref.current.contains(event.target)
      ) {
        close();
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        close();
      }
    };
    if (open) {
      document.addEventListener('pointerdown', handlePointerDown);
      document.addEventListener('keydown', handleKeyDown);
    }

    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [close, open, ref]);
};
