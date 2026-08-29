import { z } from 'zod';

export const HISTORICAL_BROWSER_SCHEMA_COMMIT =
  '901f40f19ddbfadac362255315f2d0692e843c0d';

// Exact ServerMessageSchema from da66f0e^ at the stamped commit above.
const HistoricalSessionEndReasonSchema = z.enum([
  'capacity',
  'challenge-required',
  'connection-lost',
  'environment-expired',
  'idle',
  'shell-exited',
  'shutdown',
  'slow-connection',
  'start-failed',
  'terminal-limit',
]);

export const HistoricalLabV1ServerMessageSchema = z.discriminatedUnion('type', [
  z.object({
    type: z.literal('starting'),
  }),
  z.object({
    environmentExpiresAt: z.number(),
    environmentToken: z.string(),
    resumed: z.boolean(),
    token: z.string(),
    type: z.literal('ready'),
  }),
  z.object({
    reason: HistoricalSessionEndReasonSchema,
    type: z.literal('end'),
  }),
  z.object({
    expiresAt: z.number(),
    thresholdMs: z.number(),
    type: z.literal('expiry-warning'),
  }),
  z.object({
    inodesTotal: z.number(),
    inodesUsed: z.number(),
    totalBytes: z.number(),
    type: z.literal('storage'),
    usedBytes: z.number(),
  }),
  z.object({
    expiresInMs: z.number(),
    token: z.string(),
    type: z.literal('archive-grant'),
  }),
  z.object({
    action: z.literal('session_keep'),
    attempt: z.number(),
    graceMs: z.number(),
    nonce: z.string(),
    type: z.literal('challenge'),
  }),
  z.object({
    type: z.literal('challenge-cleared'),
  }),
]);
