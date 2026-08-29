import type { ArchiveFormat } from '@shell/protocol';

export type ArchiveTracker = {
  readonly consume: (token: string) => null | {
    readonly format: ArchiveFormat;
    readonly token: string;
  };
  readonly request: (format: ArchiveFormat) => boolean;
};

export const createArchiveTracker = (): ArchiveTracker => {
  let pending: ArchiveFormat | null = null;

  return {
    consume: (token) => {
      if (pending === null) {
        return null;
      }
      const grant = { format: pending, token };
      pending = null;
      return grant;
    },
    request: (format) => {
      if (pending !== null) {
        return false;
      }
      pending = format;
      return true;
    },
  };
};
