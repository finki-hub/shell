import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useFiles } from '@/hooks/useFiles';
import { useUploadQueue } from '@/hooks/useUploadQueue';
import {
  type ArchiveFormat,
  type ContentsEntry,
  type ContentsErrorKind,
  type ContentsResult,
  type UploadInput,
} from '@/lib/contents-api';
import { type LabSession, type UrlTokenResult } from '@/lib/hub-api';
import { translations } from '@/lib/i18n';

type Effect = () => (() => void) | undefined;

const runtime = vi.hoisted(() => ({
  cursor: 0,
  effects: [] as Effect[],
  refs: [] as Array<{ current: unknown }>,
  states: [] as unknown[],
}));

const services = vi.hoisted(() => ({
  archiveDownloadUrl: vi.fn<
    (
      session: LabSession,
      input: {
        readonly directory: string;
        readonly downloadToken: string;
        readonly format: ArchiveFormat;
      },
    ) => string
  >(),
  createDirectory:
    vi.fn<
      (
        session: LabSession,
        input: { readonly directory: string; readonly name: string },
      ) => Promise<ContentsResult<true>>
    >(),
  deleteEntry:
    vi.fn<
      (
        session: LabSession,
        path: string,
        signal?: AbortSignal,
      ) => Promise<ContentsResult<true>>
    >(),
  fileDownloadUrl:
    vi.fn<(session: LabSession, path: string, token: string) => string>(),
  listDirectory:
    vi.fn<
      (
        session: LabSession,
        path: string,
        signal?: AbortSignal,
      ) => Promise<ContentsResult<readonly ContentsEntry[]>>
    >(),
  mintUrlToken:
    vi.fn<
      (session: LabSession, signal?: AbortSignal) => Promise<UrlTokenResult>
    >(),
  renameEntry:
    vi.fn<
      (
        session: LabSession,
        input: { readonly newPath: string; readonly path: string },
      ) => Promise<ContentsResult<true>>
    >(),
  startDownload: vi.fn<(url: string) => void>(),
  uploadFile:
    vi.fn<
      (session: LabSession, input: UploadInput) => Promise<ContentsResult<true>>
    >(),
}));

vi.mock('react', () => ({
  useCallback: (callback: unknown): unknown => callback,
  useEffect: (effect: Effect): void => {
    runtime.effects.push(effect);
  },
  useRef: (initial: unknown): { current: unknown } => {
    const index = runtime.cursor;
    runtime.cursor += 1;
    const existing = runtime.refs[index];
    if (existing !== undefined) return existing;
    const created = { current: initial };
    runtime.refs[index] = created;
    return created;
  },
  useState: (
    initial: unknown,
  ): readonly [unknown, (value: unknown) => void] => {
    const index = runtime.cursor;
    runtime.cursor += 1;
    if (runtime.states[index] === undefined) {
      runtime.states[index] =
        typeof initial === 'function'
          ? Reflect.apply(initial, undefined, [])
          : initial;
    }
    const setValue = (value: unknown): void => {
      runtime.states[index] =
        typeof value === 'function'
          ? Reflect.apply(value, undefined, [runtime.states[index]])
          : value;
    };
    return [runtime.states[index], setValue];
  },
}));

vi.mock('@/lib/contents-api', () => ({
  archiveDownloadUrl: services.archiveDownloadUrl,
  createDirectory: services.createDirectory,
  deleteEntry: services.deleteEntry,
  fileDownloadUrl: services.fileDownloadUrl,
  joinPath: (directory: string, name: string) =>
    [directory, name].filter((part) => part !== '').join('/'),
  listDirectory: services.listDirectory,
  renameEntry: services.renameEntry,
  startDownload: services.startDownload,
  uploadFile: services.uploadFile,
}));
vi.mock('@/lib/hub-api', () => ({ mintUrlToken: services.mintUrlToken }));

const SESSION: LabSession = {
  apiToken: 'api-token',
  maxTerminals: 2,
  tokenExpiresAt: '2099-01-01T00:00:00Z',
  username: 'user-name',
};

const CONTENTS_ERRORS = [
  'aborted',
  'bad-request',
  'conflict',
  'forbidden',
  'gone',
  'insufficient-storage',
  'malformed',
  'network',
  'not-found',
  'rate-limited',
  'too-large',
  'unavailable',
] as const satisfies readonly ContentsErrorKind[];

const flush = async (): Promise<void> => {
  for (let turn = 0; turn < 20; turn += 1) await Promise.resolve();
};

const resetRuntime = (): void => {
  runtime.cursor = 0;
  runtime.effects.length = 0;
  runtime.refs.length = 0;
  runtime.states.length = 0;
};

const useRenderedFiles = (refreshStorage: () => void) => {
  runtime.cursor = 0;
  return useFiles({
    markActive: vi.fn(),
    refreshStorage,
    session: SESSION,
    setTransferring: vi.fn(),
    storage: null,
  });
};

describe('file panel model', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetRuntime();
    services.createDirectory.mockResolvedValue({ ok: true, value: true });
    services.listDirectory.mockResolvedValue({ ok: true, value: [] });
    services.mintUrlToken.mockResolvedValue({ kind: 'token', token: 'short' });
    services.archiveDownloadUrl.mockReturnValue('archive-url');
  });

  it('reports chunk progress and never starts a cancelled queued upload', async () => {
    const first = Promise.withResolvers<{ ok: true; value: true }>();
    services.uploadFile.mockReturnValueOnce(first.promise);
    let directory = 'work';
    const onSettled = vi.fn();
    const setTransferring = vi.fn<(transferring: boolean) => void>();
    const useRenderedQueue = () => {
      runtime.cursor = 0;
      return useUploadQueue({
        directory: () => directory,
        onSettled,
        session: SESSION,
        setTransferring,
      });
    };
    const queue = useRenderedQueue();
    queue.enqueue([new File(['one'], 'one.txt'), new File(['two'], 'two.txt')]);
    await flush();
    directory = 'elsewhere';
    queue.cancelUpload(2);
    const progress = services.uploadFile.mock.calls[0]?.[1]?.onProgress;
    progress?.(0.5);
    expect(useRenderedQueue().uploads[0]?.progress).toBe(0.5);

    first.resolve({ ok: true, value: true });
    await flush();

    expect(services.uploadFile).toHaveBeenCalledOnce();
    expect(services.uploadFile.mock.calls[0]?.[1]?.directory).toBe('work');
    expect(onSettled).toHaveBeenCalledWith('work', true);
    expect(setTransferring.mock.calls.map((call) => call[0])).toEqual([
      true,
      false,
    ]);
    expect(useRenderedQueue().uploads.map((upload) => upload.state)).toEqual([
      'done',
      'failed',
    ]);
  });

  it('returns queued IDs for main-screen upload progress', () => {
    services.uploadFile.mockReturnValue(new Promise(() => {}));
    const queue = useUploadQueue({
      directory: () => '',
      onSettled: vi.fn(),
      session: SESSION,
      setTransferring: vi.fn(),
    });

    const ids = queue.enqueue([
      new File(['one'], 'one.txt'),
      new File(['two'], 'two.txt'),
    ]);

    expect(ids).toEqual([1, 2]);
  });

  it('keeps an empty directory settled while the next listing is pending', async () => {
    const first =
      Promise.withResolvers<ContentsResult<readonly ContentsEntry[]>>();
    const second =
      Promise.withResolvers<ContentsResult<readonly ContentsEntry[]>>();
    services.listDirectory
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const refreshStorage = vi.fn();
    const initial = useRenderedFiles(refreshStorage);
    initial.refresh();
    first.resolve({ ok: true, value: [] });
    await flush();
    const settled = useRenderedFiles(refreshStorage);

    settled.refresh();
    await flush();
    const pending = useRenderedFiles(refreshStorage);

    expect({
      entries: pending.entries,
      initialized: pending.initialized,
      loading: pending.loading,
    }).toEqual({ entries: [], initialized: true, loading: true });
  });

  it('aborts the previous listing and refreshes storage after a mutation', async () => {
    const requests: AbortSignal[] = [];
    services.listDirectory.mockImplementation(
      (_session: LabSession, _path: string, signal?: AbortSignal) => {
        if (signal !== undefined) requests.push(signal);
        return new Promise(() => {});
      },
    );
    const refreshStorage = vi.fn();
    const files = useRenderedFiles(refreshStorage);
    files.refresh();
    files.refresh();
    expect(requests[0]?.aborted).toBe(true);

    await expect(files.createFolder('new')).resolves.toBeNull();

    expect(refreshStorage).toHaveBeenCalledOnce();
    expect(services.listDirectory.mock.calls.length).toBeGreaterThan(2);
  });

  it('mints before navigating to download a named directory', async () => {
    const markActive = vi.fn();
    const files = useFiles({
      markActive,
      refreshStorage: vi.fn(),
      session: SESSION,
      setTransferring: vi.fn(),
      storage: null,
    });

    await expect(
      files.downloadDirectory('work folder', 'zip'),
    ).resolves.toBeNull();

    expect(services.mintUrlToken).toHaveBeenCalledWith(SESSION);
    expect(services.archiveDownloadUrl).toHaveBeenCalledWith(SESSION, {
      directory: 'work folder',
      downloadToken: 'short',
      format: 'zip',
    });
    expect(markActive).toHaveBeenCalledOnce();
    expect(services.startDownload).toHaveBeenCalledWith('archive-url');
    expect(services.mintUrlToken.mock.invocationCallOrder[0]).toBeLessThan(
      services.startDownload.mock.invocationCallOrder[0] ?? 0,
    );
  });

  it('has localized panel text for every contents failure', () => {
    for (const kind of CONTENTS_ERRORS) {
      expect(translations.en.files.errors[kind]).not.toBe('');
      expect(translations.mk.files.errors[kind]).not.toBe('');
    }
  });
});
