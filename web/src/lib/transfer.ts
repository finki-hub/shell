import {
  type ArchiveFormat,
  ArchiveTicketSuccessResponseSchema,
  UploadErrorResponseSchema,
  UploadSuccessResponseSchema,
} from '@shell/protocol';

import { classifyHttpStatus } from '@/lib/protocol';

export const UPLOAD_ERRORS = [
  'aborted',
  'already-running',
  'conflict',
  'empty-body',
  'failed',
  'insufficient-storage',
  'invalid-name',
  'length-required',
  'network',
  'quota',
  'rate-limited',
  'session-gone',
  'timeout',
  'too-large',
] as const;

export type UploadError = (typeof UPLOAD_ERRORS)[number];

const UPLOAD_ERROR_SET: ReadonlySet<string> = new Set(UPLOAD_ERRORS);

const isUploadError = (value: unknown): value is UploadError =>
  typeof value === 'string' && UPLOAD_ERROR_SET.has(value);

export type UploadFailureOutcome =
  | 'bad-request'
  | 'capacity'
  | 'conflict'
  | 'forbidden'
  | 'gone'
  | 'malformed'
  | 'network'
  | 'rate-limited'
  | 'unavailable';

export type UploadResult =
  | {
      limit: null | number;
      ok: false;
      outcome: UploadFailureOutcome;
      reason: UploadError;
    }
  | { ok: true };

const parseJson = (text: string): unknown => {
  try {
    return JSON.parse(text);
  } catch (error) {
    if (error instanceof SyntaxError) {
      return null;
    }
    throw error;
  }
};

const UPLOAD_OUTCOME_BY_STATUS: Readonly<
  Partial<Record<number, UploadFailureOutcome>>
> = {
  400: 'bad-request',
  403: 'forbidden',
  409: 'conflict',
  410: 'gone',
  429: 'rate-limited',
  503: 'unavailable',
  507: 'capacity',
};

const classifyUploadStatus = (status: number): UploadFailureOutcome =>
  UPLOAD_OUTCOME_BY_STATUS[status] ?? 'malformed';

const parseFailure = (text: string, status: number): UploadResult => {
  const parsed = UploadErrorResponseSchema.safeParse(parseJson(text));
  const reason =
    parsed.success && isUploadError(parsed.data.error)
      ? parsed.data.error
      : 'failed';
  return {
    limit: parsed.success ? (parsed.data.limit ?? null) : null,
    ok: false,
    outcome: classifyUploadStatus(status),
    reason,
  };
};

export const classifyUploadHttpResponse = (
  status: number,
  text: string,
): UploadResult => {
  if (status !== 201) {
    return parseFailure(text, status);
  }
  const parsed = UploadSuccessResponseSchema.safeParse(parseJson(text));
  return parsed.success
    ? { ok: true }
    : { limit: null, ok: false, outcome: 'malformed', reason: 'failed' };
};

// A plain anchor navigation, so the browser's own download manager owns the
// transfer: it survives a tab that is busy, shows real progress, and never
// buffers the archive in the page.
export const startArchiveDownload = (token: string, format: ArchiveFormat) => {
  const link = document.createElement('a');
  link.href = `/api/session/archive?token=${encodeURIComponent(token)}&format=${format}`;
  link.rel = 'noopener';
  link.download = '';
  document.body.append(link);
  link.click();
  link.remove();
  return { kind: 'requested' } as const;
};

export type ArchiveTicketResult =
  | { kind: 'bad-request' }
  | { kind: 'capacity' }
  | { kind: 'conflict' }
  | { kind: 'forbidden' }
  | { kind: 'gone' }
  | { kind: 'malformed' }
  | { kind: 'network' }
  | { kind: 'rate-limited' }
  | { kind: 'requested' }
  | { kind: 'unavailable' };

type ArchiveTicketError = Exclude<ArchiveTicketResult['kind'], 'requested'>;

export const archiveTicketNotice = (
  copy: {
    readonly errors: Readonly<Record<ArchiveTicketError, string>>;
    readonly requested: string;
  },
  result: ArchiveTicketResult,
): { readonly kind: 'error' | 'success'; readonly message: string } =>
  result.kind === 'requested'
    ? { kind: 'success', message: copy.requested }
    : { kind: 'error', message: copy.errors[result.kind] };

export const requestExpiredEnvironmentArchive = async (
  environmentToken: string,
  format: ArchiveFormat,
): Promise<ArchiveTicketResult> => {
  let response: Response;
  let text: string;
  try {
    response = await fetch('/api/environment/archive-ticket', {
      headers: { 'X-Lab-Environment': environmentToken },
      method: 'POST',
    });
    text = await response.text();
  } catch (error) {
    if (error instanceof Error) {
      return { kind: 'network' };
    }
    throw error;
  }

  if (!response.ok) {
    return classifyHttpStatus(response.status);
  }
  const parsed = ArchiveTicketSuccessResponseSchema.safeParse(parseJson(text));
  if (!parsed.success) {
    return { kind: 'malformed' };
  }
  return startArchiveDownload(parsed.data.token, format);
};

// Downloads from an environment that has no session — an expired one inside
// its grace window. The long-lived token is exchanged for a one-shot ticket
// over POST, so it never reaches a URL.
export const downloadExpiredEnvironment = async (
  environmentToken: string,
  format: ArchiveFormat,
) => {
  const result = await requestExpiredEnvironmentArchive(
    environmentToken,
    format,
  );
  return result.kind === 'requested';
};

export type UploadHandle = {
  cancel: () => void;
  done: Promise<UploadResult>;
};

// XMLHttpRequest rather than fetch: upload progress events are the whole
// point, and fetch still cannot report them.
export const uploadFile = (
  token: string,
  file: File,
  onProgress: (fraction: number) => void,
): UploadHandle => {
  const request = new XMLHttpRequest();
  // Resolved rather than rejected on failure: the reason and the limit the
  // server reports are both part of the answer, not an exception.
  const done = new Promise<UploadResult>((resolve) => {
    request.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable && event.total > 0) {
        onProgress(event.loaded / event.total);
      }
    });
    request.addEventListener('load', () => {
      const result = classifyUploadHttpResponse(
        request.status,
        request.responseText,
      );
      if (result.ok) {
        onProgress(1);
      }
      resolve(result);
    });
    request.addEventListener('error', () => {
      resolve({
        limit: null,
        ok: false,
        outcome: 'network',
        reason: 'network',
      });
    });
    request.addEventListener('abort', () => {
      resolve({
        limit: null,
        ok: false,
        outcome: 'network',
        reason: 'aborted',
      });
    });
  });

  request.open(
    'POST',
    `/api/session/upload?name=${encodeURIComponent(file.name)}`,
  );
  request.setRequestHeader('Content-Type', 'application/octet-stream');
  request.setRequestHeader('X-Lab-Token', token);
  request.send(file);

  return {
    cancel: () => {
      request.abort();
    },
    done,
  };
};

export const formatBytes = (bytes: number) => {
  if (bytes < 1_024) {
    return `${bytes} B`;
  }

  const megabytes = bytes / (1_024 * 1_024);

  if (megabytes < 1) {
    return `${Math.round(bytes / 1_024)} KB`;
  }

  const decimals = megabytes < 10 ? 1 : 0;

  return `${megabytes.toFixed(decimals)} MB`;
};
