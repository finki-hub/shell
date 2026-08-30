import { useEffect, useState } from 'react';

export const useDropZone = (
  enabled: boolean,
  onFiles: (files: readonly File[]) => void,
) => {
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    // Track drag depth because dragleave fires for child elements.
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
    if (enabled) {
      document.addEventListener('dragenter', handleDragEnter);
      document.addEventListener('dragover', handleDragOver);
      document.addEventListener('dragleave', handleDragLeave);
      document.addEventListener('drop', handleDrop);
    } else {
      setDragging(false);
    }

    return () => {
      document.removeEventListener('dragenter', handleDragEnter);
      document.removeEventListener('dragover', handleDragOver);
      document.removeEventListener('dragleave', handleDragLeave);
      document.removeEventListener('drop', handleDrop);
    };
  }, [enabled, onFiles]);

  return dragging;
};
