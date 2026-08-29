import type { UploadHandle } from '@/lib/transfer';

export type UploadGeneration = {
  readonly active: () => boolean;
  readonly end: () => void;
  readonly release: (handle: UploadHandle) => void;
  readonly retain: (handle: UploadHandle) => void;
  readonly run: (effect: () => void) => void;
};

export const createUploadGeneration = (): UploadGeneration => {
  const handles = new Set<UploadHandle>();
  let active = true;

  return {
    active: () => active,
    end: () => {
      if (!active) {
        return;
      }
      active = false;
      for (const handle of handles) {
        handle.cancel();
      }
      handles.clear();
    },
    release: (handle) => {
      handles.delete(handle);
    },
    retain: (handle) => {
      if (active) {
        handles.add(handle);
        return;
      }
      handle.cancel();
    },
    run: (effect) => {
      if (active) {
        effect();
      }
    },
  };
};
