import type {
  ArchiveFormat as SharedArchiveFormat,
  ClientEndReason as SharedClientEndReason,
  ClientMessage as SharedClientMessage,
  ServerMessage as SharedServerMessage,
  SessionEndReason as SharedSessionEndReason,
  StorageUsage as SharedStorageUsage,
} from '@shell/protocol';

export type ArchiveFormat = SharedArchiveFormat;
export type ClientEndReason = SharedClientEndReason;
export type ClientMessage = SharedClientMessage;
export type HttpFailureKind =
  | 'bad-request'
  | 'capacity'
  | 'conflict'
  | 'forbidden'
  | 'gone'
  | 'rate-limited'
  | 'unavailable';
export type ServerMessage = SharedServerMessage;
export type SessionEndReason = SharedSessionEndReason;

export type StorageUsage = SharedStorageUsage;

const HTTP_FAILURE_BY_STATUS: Readonly<
  Partial<Record<number, HttpFailureKind>>
> = {
  400: 'bad-request',
  403: 'forbidden',
  409: 'conflict',
  410: 'gone',
  429: 'rate-limited',
  503: 'unavailable',
  507: 'capacity',
};

export const classifyHttpStatus = (
  status: number,
): { readonly kind: HttpFailureKind } => ({
  kind: HTTP_FAILURE_BY_STATUS[status] ?? 'unavailable',
});
