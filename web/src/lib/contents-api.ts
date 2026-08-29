import { z } from 'zod';

import {
  decodeJson,
  type LabSession,
  sendJson,
  type SendResult,
} from '@/lib/hub-api';

export type ArchiveFormat = 'tgz' | 'zip';

export type ContentsEntry = {
  readonly modifiedAt: string;
  readonly name: string;
  readonly path: string;
  readonly size: null | number;
  readonly type: 'directory' | 'file' | 'notebook';
};

export type ContentsError = {
  readonly kind: ContentsErrorKind;
  readonly message: null | string;
  readonly status: null | number;
};

export type ContentsErrorKind =
  | 'aborted'
  | 'bad-request'
  | 'conflict'
  | 'forbidden'
  | 'gone'
  | 'insufficient-storage'
  | 'malformed'
  | 'network'
  | 'not-found'
  | 'rate-limited'
  | 'too-large'
  | 'unavailable';

export type ContentsResult<T> =
  | { readonly error: ContentsError; readonly ok: false }
  | { readonly ok: true; readonly value: T };

const EntrySchema = z.object({
  // eslint-disable-next-line camelcase -- the jupyter_server contents model field name
  last_modified: z.string(),
  name: z.string(),
  path: z.string(),
  size: z.number().nullish(),
  type: z.enum(['directory', 'file', 'notebook']),
});

const DirectorySchema = EntrySchema.extend({ content: z.array(EntrySchema) });

const ErrorSchema = z.object({ message: z.string().optional() });

// jupyter_server's own statuses plus the two `QuotaAwareFileManager` adds: 507
// when the declared size does not fit the remaining quota, 409 when the
// destination is a directory or a symlink. 424 is the hub's, not the
// container's: it is what JupyterHub 5 answers for a single-user server that
// is not running, which here means the container was culled while the panel
// was open — temporary, and undone by the reconnect the end screen offers.
const ERROR_BY_STATUS: Readonly<Partial<Record<number, ContentsErrorKind>>> = {
  400: 'bad-request',
  403: 'forbidden',
  404: 'not-found',
  409: 'conflict',
  410: 'gone',
  413: 'too-large',
  424: 'unavailable',
  429: 'rate-limited',
  503: 'unavailable',
  507: 'insufficient-storage',
};

// One chunk plus base64 overhead stays well inside the container's 8 MiB
// `max_body_size`, and a failed chunk costs a megabyte of retry rather than
// the whole file.
const CHUNK_BYTES = 1_024 * 1_024;

const failure = (status: number, text: string): ContentsError => {
  const message = decodeJson(ErrorSchema, text)?.message ?? null;
  return {
    kind:
      status === 400 && message?.startsWith('Not a directory:') === true
        ? 'conflict'
        : (ERROR_BY_STATUS[status] ?? 'unavailable'),
    message,
    status,
  };
};

const transportError = (
  result: Extract<SendResult, { kind: 'aborted' | 'network' }>,
): ContentsError => ({ kind: result.kind, message: null, status: null });

const segments = (path: string): readonly string[] =>
  path.split('/').filter((segment) => segment !== '');

const encodePath = (path: string): string =>
  segments(path)
    .map((segment) => encodeURIComponent(segment))
    .join('/');

export const joinPath = (directory: string, name: string): string =>
  [...segments(directory), name].join('/');

const userRoot = (session: LabSession): string =>
  `/user/${encodeURIComponent(session.username)}`;

const contentsUrl = (session: LabSession, path: string): string =>
  `${userRoot(session)}/api/contents/${encodePath(path)}`;

const nothing = (): true => true;

const call = async <T>(
  input: Parameters<typeof sendJson>[0],
  accepted: readonly number[],
  decode: (text: string) => null | T,
): Promise<ContentsResult<T>> => {
  const result = await sendJson(input);
  if (result.kind !== 'response') {
    return { error: transportError(result), ok: false };
  }
  if (!accepted.includes(result.status)) {
    return { error: failure(result.status, result.text), ok: false };
  }
  const value = decode(result.text);
  return value === null
    ? {
        error: { kind: 'malformed', message: null, status: result.status },
        ok: false,
      }
    : { ok: true, value };
};

const toEntry = (raw: z.infer<typeof EntrySchema>): ContentsEntry => ({
  modifiedAt: raw.last_modified,
  name: raw.name,
  path: raw.path,
  size: raw.size ?? null,
  type: raw.type,
});

export const listDirectory = async (
  session: LabSession,
  path: string,
  signal?: AbortSignal,
): Promise<ContentsResult<readonly ContentsEntry[]>> =>
  call(
    {
      method: 'GET',
      session,
      signal,
      url: `${contentsUrl(session, path)}?content=1`,
    },
    [200],
    (text) => decodeJson(DirectorySchema, text)?.content.map(toEntry) ?? null,
  );

export const createDirectory = async (
  session: LabSession,
  input: {
    readonly directory: string;
    readonly name: string;
    readonly signal?: AbortSignal;
  },
): Promise<ContentsResult<true>> =>
  call(
    {
      body: { type: 'directory' },
      method: 'PUT',
      session,
      signal: input.signal,
      url: contentsUrl(session, joinPath(input.directory, input.name)),
    },
    [200, 201],
    nothing,
  );

export const renameEntry = async (
  session: LabSession,
  input: {
    readonly newPath: string;
    readonly path: string;
    readonly signal?: AbortSignal;
  },
): Promise<ContentsResult<true>> =>
  call(
    {
      body: { path: input.newPath },
      method: 'PATCH',
      session,
      signal: input.signal,
      url: contentsUrl(session, input.path),
    },
    [200],
    nothing,
  );

export const deleteEntry = async (
  session: LabSession,
  path: string,
  signal?: AbortSignal,
): Promise<ContentsResult<true>> =>
  call(
    { method: 'DELETE', session, signal, url: contentsUrl(session, path) },
    [204],
    nothing,
  );

export type UploadInput = {
  readonly directory: string;
  readonly file: File;
  readonly onProgress?: (fraction: number) => void;
  readonly signal?: AbortSignal;
};

// Chunk numbering follows `LargeFileManager`: 1 creates the file (and is where
// the quota preflight runs), 2..N-1 append, -1 marks the last one. A file that
// fits in a single chunk is sent unchunked, because chunk 1 would create it and
// there would be no last chunk left to close it.
const chunkNumber = (index: number, total: number): null | number => {
  if (total === 1) return null;
  return index === total - 1 ? -1 : index + 1;
};

const putChunk = async (
  session: LabSession,
  input: UploadInput,
  place: {
    readonly index: number;
    readonly partPath: string;
    readonly total: number;
  },
): Promise<ContentsResult<true>> => {
  const start = place.index * CHUNK_BYTES;
  const slice = input.file.slice(start, start + CHUNK_BYTES);
  const chunk = chunkNumber(place.index, place.total);
  return call(
    {
      body: {
        ...(chunk !== null && { chunk }),
        content: new Uint8Array(await slice.arrayBuffer()).toBase64(),
        format: 'base64',
        type: 'file',
      },
      // Read on the first chunk only, but sent on every one so a retry that
      // restarts at chunk 1 still declares what it is about to write.
      headers: { 'X-Upload-Size': String(input.file.size) },
      method: 'PUT',
      session,
      signal: input.signal,
      url: contentsUrl(session, place.partPath),
    },
    [200, 201],
    nothing,
  );
};

// Four random bytes, not a counter and not the file name alone: two tabs of the
// same environment uploading the same name would otherwise write into one
// `.part` and interleave each other's chunks into a file that is neither.
const PART_SUFFIX_BYTES = 4;

export const partName = (name: string): string => {
  const suffix = new Uint8Array(PART_SUFFIX_BYTES);
  crypto.getRandomValues(suffix);
  return `.${name}.${suffix.toHex()}.part`;
};

// Written to `.<name>.<random>.part` and renamed on completion, so a failed or
// abandoned upload never leaves a half-written file under the real name, and
// the rename replaces an existing file atomically.
export const uploadFile = async (
  session: LabSession,
  input: UploadInput,
): Promise<ContentsResult<true>> => {
  const partPath = joinPath(input.directory, partName(input.file.name));
  const total = Math.max(1, Math.ceil(input.file.size / CHUNK_BYTES));

  for (let index = 0; index < total; index += 1) {
    const written = await putChunk(session, input, { index, partPath, total });
    if (!written.ok) {
      // Best effort and deliberately unsignalled: the cleanup must still run
      // when the upload was the thing that got aborted.
      void deleteEntry(session, partPath);
      return written;
    }
    input.onProgress?.((index + 1) / total);
  }

  const renamed = await renameEntry(session, {
    newPath: joinPath(input.directory, input.file.name),
    path: partPath,
    signal: input.signal,
  });
  if (!renamed.ok) {
    void deleteEntry(session, partPath);
  }
  return renamed;
};

export const fileDownloadUrl = (
  session: LabSession,
  path: string,
  downloadToken: string,
): string =>
  `${userRoot(session)}/files/${encodePath(path)}` +
  `?download=1&token=${encodeURIComponent(downloadToken)}`;

const randomUuid = (): string => {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = ((bytes.at(6) ?? 0) % 16) + 64;
  bytes[8] = ((bytes.at(8) ?? 0) % 64) + 128;
  const hex = bytes.toHex();
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};

// `archiveToken` is echoed back as a cookie by jupyter-archive so the page can
// tell that the stream actually started; any value will do, so a fresh UUID
// keeps two downloads from being confused for one another.
export const archiveDownloadUrl = (
  session: LabSession,
  input: {
    readonly archiveToken?: string;
    readonly directory: string;
    readonly downloadToken: string;
    readonly format: ArchiveFormat;
  },
): string =>
  `${userRoot(session)}/directories/${encodePath(input.directory)}?archiveFormat=${input.format}` +
  `&archiveToken=${encodeURIComponent(input.archiveToken ?? randomUuid())}` +
  '&downloadHidden=true&followSymlinks=false' +
  `&token=${encodeURIComponent(input.downloadToken)}`;

// A plain anchor navigation, so the browser's own download manager owns the
// transfer: it survives a busy tab, shows real progress, and never buffers the
// archive in the page.
export const startDownload = (url: string): void => {
  const link = document.createElement('a');
  link.href = url;
  link.rel = 'noopener';
  link.download = '';
  document.body.append(link);
  link.click();
  link.remove();
};
