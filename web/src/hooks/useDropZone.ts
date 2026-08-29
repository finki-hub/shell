import { useEffect, useState } from 'react';

// Document-level listeners rather than JSX handlers, so a file can be dropped
// anywhere on the page — including onto the terminal, which is where a user
// is already looking.
export const useDropZone = (
  enabled: boolean,
  onFiles: (files: readonly File[]) => void,
) => {
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (!enabled) {
      setDragging(false);

      return () => {
        // Nothing was attached.
      };
    }

    // dragleave fires when the pointer crosses into a child element, so the
    // state is driven by a depth counter rather than by the last event seen.
    let depth = 0;

    const handleDragEnter = (event: DragEvent) => {
      event.preventDefault();
      depth += 1;
      setDragging(true);
    };
    const handleDragOver = (event: DragEvent) => {
      event.preventDefault();
    };
    const handleDragLeave = (event: DragEvent) => {
      event.preventDefault();
      depth = Math.max(0, depth - 1);

      if (depth === 0) {
        setDragging(false);
      }
    };
    const handleDrop = (event: DragEvent) => {
      event.preventDefault();
      depth = 0;
      setDragging(false);
      onFiles([...(event.dataTransfer?.files ?? [])]);
    };

    document.addEventListener('dragenter', handleDragEnter);
    document.addEventListener('dragover', handleDragOver);
    document.addEventListener('dragleave', handleDragLeave);
    document.addEventListener('drop', handleDrop);

    return () => {
      document.removeEventListener('dragenter', handleDragEnter);
      document.removeEventListener('dragover', handleDragOver);
      document.removeEventListener('dragleave', handleDragLeave);
      document.removeEventListener('drop', handleDrop);
    };
  }, [enabled, onFiles]);

  return dragging;
};
