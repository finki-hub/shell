import { useCallback, useEffect, useRef, useState } from 'react';

import { type UploadQueue, useUploadQueue } from '@/hooks/useUploadQueue';
import {
  archiveDownloadUrl,
  type ArchiveFormat,
  type ContentsEntry,
  type ContentsErrorKind,
  type ContentsResult,
  createDirectory,
  deleteEntry,
  fileDownloadUrl,
  joinPath,
  listDirectory,
  renameEntry,
  startDownload,
} from '@/lib/contents-api';
import { type LabSession, mintUrlToken } from '@/lib/hub-api';
import { type StorageUsage } from '@/lib/storage-api';

/**
 * Everything the panel needs, and nothing it has to assemble itself. Every
 * mutation answers with the `ContentsErrorKind` that stopped it, or `null`,
 * so the component owns the wording and the hook owns none of it.
 */
export type FilesModel = UploadQueue & {
  readonly busy: boolean;
  readonly createFolder: (name: string) => Promise<ContentsErrorKind | null>;
  readonly downloadAll: (
    format: ArchiveFormat,
  ) => Promise<ContentsErrorKind | null>;
  readonly downloadFile: (
    entry: ContentsEntry,
  ) => Promise<ContentsErrorKind | null>;
  readonly entries: readonly ContentsEntry[];
  readonly error: ContentsErrorKind | null;
  readonly loading: boolean;
  readonly navigate: (path: string) => void;
  readonly open: (entry: ContentsEntry) => void;
  readonly path: string;
  readonly refresh: () => void;
  readonly remove: (entry: ContentsEntry) => Promise<ContentsErrorKind | null>;
  readonly rename: (
    entry: ContentsEntry,
    name: string,
  ) => Promise<ContentsErrorKind | null>;
  readonly storage: null | StorageUsage;
};

type UseFilesInput = {
  /** `poller.markActive` — a download is a navigation with no visible end. */
  readonly markActive: () => void;
  /** `poller.refresh` — the badge must not lag a delete by up to a minute. */
  readonly refreshStorage: () => void;
  readonly session: LabSession | null;
  /** `poller.setTransferring`, forwarded to the queue. */
  readonly setTransferring: (transferring: boolean) => void;
  readonly storage: null | StorageUsage;
};

// Paths are relative to the home directory, so the root is the empty string.
export const parentPath = (path: string): string =>
  path.split('/').slice(0, -1).join('/');

// Folders first and then by name, which is the order every file manager uses
// and the only one in which a tree is navigable by eye. `localeCompare` so
// Macedonian names sort as Macedonian rather than by code point.
const byKind = (left: ContentsEntry, right: ContentsEntry): number => {
  const leftIsDirectory = left.type === 'directory';
  if (leftIsDirectory !== (right.type === 'directory')) {
    return leftIsDirectory ? -1 : 1;
  }
  return left.name.localeCompare(right.name);
};

export const useFiles = ({
  markActive,
  refreshStorage,
  session,
  setTransferring,
  storage,
}: UseFilesInput): FilesModel => {
  const [busy, setBusy] = useState(false);
  const [entries, setEntries] = useState<readonly ContentsEntry[]>([]);
  const [error, setError] = useState<ContentsErrorKind | null>(null);
  const [loading, setLoading] = useState(false);
  const [path, setPath] = useState('');

  const listRef = useRef<AbortController | null>(null);
  const pathRef = useRef(path);

  // Read by the upload queue, which outlives the render that started it and
  // must not reload a directory the user has already navigated away from.
  useEffect(() => {
    pathRef.current = path;
  }, [path]);

  const load = useCallback(
    (target: string) => {
      if (session === null) return;
      // One listing at a time: a fast second navigation must not be overwritten
      // by the answer to the first one.
      listRef.current?.abort();
      const controller = new AbortController();
      listRef.current = controller;
      setLoading(true);
      void (async () => {
        const result = await listDirectory(session, target, controller.signal);
        if (controller.signal.aborted) return;
        setLoading(false);
        if (result.ok) {
          setEntries(result.value.toSorted(byKind));
          setError(null);
          return;
        }
        setError(result.error.kind);
      })();
    },
    [session],
  );

  // A new environment is a new tree, so the panel goes back to its root.
  useEffect(() => {
    setPath('');
  }, [session]);

  useEffect(() => {
    load(path);
  }, [load, path]);

  useEffect(
    () => () => {
      listRef.current?.abort();
    },
    [],
  );

  const refresh = useCallback(() => {
    load(pathRef.current);
  }, [load]);

  const onSettled = useCallback(
    (target: string, ok: boolean) => {
      refreshStorage();
      if (ok && target === pathRef.current) {
        load(target);
      }
    },
    [load, refreshStorage],
  );

  const directory = useCallback(() => pathRef.current, []);
  const queue = useUploadQueue({
    directory,
    onSettled,
    session,
    setTransferring,
  });

  const mutate = useCallback(
    async (
      run: (active: LabSession) => Promise<ContentsResult<true>>,
    ): Promise<ContentsErrorKind | null> => {
      if (session === null) return 'gone';
      setBusy(true);
      try {
        const result = await run(session);
        if (!result.ok) return result.error.kind;
        // Both, always: the listing because a name changed, the badge because
        // the inode count did.
        refreshStorage();
        load(pathRef.current);
        return null;
      } finally {
        setBusy(false);
      }
    },
    [load, refreshStorage, session],
  );

  const createFolder = useCallback(
    async (name: string): Promise<ContentsErrorKind | null> =>
      mutate((active) =>
        createDirectory(active, { directory: pathRef.current, name }),
      ),
    [mutate],
  );

  // Rename in place only. Moving between folders is a second concept — a
  // destination picker — and the terminal is right there for it.
  const rename = useCallback(
    async (
      entry: ContentsEntry,
      name: string,
    ): Promise<ContentsErrorKind | null> =>
      mutate((active) =>
        renameEntry(active, {
          newPath: joinPath(parentPath(entry.path), name),
          path: entry.path,
        }),
      ),
    [mutate],
  );

  const remove = useCallback(
    async (entry: ContentsEntry): Promise<ContentsErrorKind | null> =>
      mutate((active) => deleteEntry(active, entry.path)),
    [mutate],
  );

  // Both downloads are browser navigations carrying a 60-second token, so the
  // token is minted per click and never stored, and the SPA's own token never
  // reaches a URL (contract §8.4). The browser owns the transfer from there,
  // which is also why the badge cannot watch it end: `markActive` opens the
  // same one-minute window a keystroke does, and that is close enough for a
  // read that does not change the number anyway.
  const download = useCallback(
    async (
      build: (active: LabSession, token: string) => string,
    ): Promise<ContentsErrorKind | null> => {
      if (session === null) return 'gone';
      const minted = await mintUrlToken(session);
      if (minted.kind !== 'token') return 'unavailable';
      markActive();
      startDownload(build(session, minted.token));
      return null;
    },
    [markActive, session],
  );

  const downloadFile = useCallback(
    async (entry: ContentsEntry): Promise<ContentsErrorKind | null> =>
      download((active, token) => fileDownloadUrl(active, entry.path, token)),
    [download],
  );

  const downloadAll = useCallback(
    async (format: ArchiveFormat): Promise<ContentsErrorKind | null> =>
      download((active, token) =>
        archiveDownloadUrl(active, { downloadToken: token, format }),
      ),
    [download],
  );

  const navigate = useCallback((target: string) => {
    setPath(target);
  }, []);

  const open = useCallback((entry: ContentsEntry) => {
    if (entry.type === 'directory') {
      setPath(entry.path);
    }
  }, []);

  return {
    ...queue,
    busy,
    createFolder,
    downloadAll,
    downloadFile,
    entries,
    error,
    loading,
    navigate,
    open,
    path,
    refresh,
    remove,
    rename,
    storage,
  };
};
