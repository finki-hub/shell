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

export type FilesModel = UploadQueue & {
  readonly busy: boolean;
  readonly createFolder: (name: string) => Promise<ContentsErrorKind | null>;
  readonly downloadDirectory: (
    directory: string,
    format: ArchiveFormat,
  ) => Promise<ContentsErrorKind | null>;
  readonly downloadFile: (
    entry: ContentsEntry,
  ) => Promise<ContentsErrorKind | null>;
  readonly entries: readonly ContentsEntry[];
  readonly error: ContentsErrorKind | null;
  readonly initialized: boolean;
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
  readonly markActive: () => void;
  readonly refreshStorage: () => void;
  readonly session: LabSession | null;
  readonly setTransferring: (transferring: boolean) => void;
  readonly storage: null | StorageUsage;
};

export const parentPath = (path: string): string =>
  path.split('/').slice(0, -1).join('/');

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
  const [initialized, setInitialized] = useState(false);
  const [loading, setLoading] = useState(false);
  const [path, setPath] = useState('');

  const listRef = useRef<AbortController | null>(null);
  const pathRef = useRef(path);

  useEffect(() => {
    pathRef.current = path;
  }, [path]);

  const load = useCallback(
    (target: string) => {
      if (session === null) return;
      listRef.current?.abort();
      const controller = new AbortController();
      listRef.current = controller;
      setLoading(true);
      void (async () => {
        const result = await listDirectory(session, target, controller.signal);
        if (controller.signal.aborted) return;
        setLoading(false);
        setInitialized(true);
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

  useEffect(() => {
    setInitialized(false);
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

  // Mint a per-click URL token so the session token never enters the URL.
  // Browser-managed downloads expose no completion signal, so mark activity first.
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

  const downloadDirectory = useCallback(
    async (
      archivePath: string,
      format: ArchiveFormat,
    ): Promise<ContentsErrorKind | null> =>
      download((active, token) =>
        archiveDownloadUrl(active, {
          directory: archivePath,
          downloadToken: token,
          format,
        }),
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
    downloadDirectory,
    downloadFile,
    entries,
    error,
    initialized,
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
