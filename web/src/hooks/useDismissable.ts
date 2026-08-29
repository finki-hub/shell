import { type RefObject, useEffect } from 'react';

// Closes a popup on a click outside it or on Escape. Shared, because two
// menus needing the same behaviour is exactly how one of them ends up without
// the Escape handler.
export const useDismissable = (
  open: boolean,
  ref: RefObject<HTMLElement | null>,
  close: () => void,
) => {
  useEffect(() => {
    if (!open) {
      return () => {
        // Nothing was attached.
      };
    }

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
    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);

    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [close, open, ref]);
};
