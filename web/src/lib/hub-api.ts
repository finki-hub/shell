import { z } from 'zod';

import { waitForAbortSignal } from '@/lib/session-generation';

export type HubFailure =
  | {
      readonly error: null | string;
      readonly kind: 'http';
      readonly message: null | string;
      readonly reason: null | string;
      readonly status: number;
    }
  | { readonly kind: 'aborted' }
  | { readonly kind: 'malformed' }
  | { readonly kind: 'network' };

export type LabSession = {
  readonly apiToken: string;
  readonly maxTerminals: number;
  readonly tokenExpiresAt: string;
  readonly username: string;
};

export type SendResult =
  | { readonly kind: 'aborted' | 'network' }
  | {
      readonly kind: 'response';
      readonly status: number;
      readonly text: string;
    };

type SendInput = {
  readonly body?: unknown;
  readonly headers?: Readonly<Record<string, string>>;
  readonly method: string;
  readonly session?: LabSession;
  readonly signal?: AbortSignal;
  readonly url: string;
};

// Accept both lab and JupyterHub error bodies.
const ErrorBodySchema = z.object({
  error: z.string().optional(),
  message: z.string().optional(),
  reason: z.string().optional(),
});

const LoginSchema = z.object({
  apiToken: z.string(),
  created: z.boolean(),
  maxTerminals: z.number(),
  tokenExpiresAt: z.string(),
  username: z.string(),
});

const ServerSchema = z.object({ ready: z.boolean() });

const UserSchema = z.object({
  name: z.string(),
  pending: z.string().nullish(),
  servers: z.record(z.string(), ServerSchema).nullish(),
});

const TokenSchema = z.object({ token: z.string() });

const POLL_INTERVAL_MS = 1_000;
// Deadline exceeds Spawner.start_timeout and http_timeout.
const SPAWN_DEADLINE_MS = 190_000;
// Covers Spawner.stop_timeout and DockerSpawner removal.
const STOP_DEADLINE_MS = 60_000;
const URL_TOKEN_TTL_S = 60;

const delay = (ms: number): Promise<void> =>
  new Promise((resolve) => {
    setTimeout(resolve, ms);
  });

export const sendJson = async ({
  body,
  headers,
  method,
  session,
  signal,
  url,
}: SendInput): Promise<SendResult> => {
  try {
    const response = await fetch(url, {
      ...(body !== undefined && { body: JSON.stringify(body) }),
      headers: {
        ...(body !== undefined && { 'Content-Type': 'application/json' }),
        ...(session !== undefined && {
          Authorization: `token ${session.apiToken}`,
        }),
        ...headers,
      },
      method,
      ...(signal !== undefined && { signal }),
    });
    return {
      kind: 'response',
      status: response.status,
      text: await response.text(),
    };
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      return { kind: 'aborted' };
    }
    if (error instanceof Error) {
      return { kind: 'network' };
    }
    throw error;
  }
};

export const decodeJson = <T>(schema: z.ZodType<T>, text: string): null | T => {
  let decoded: unknown;
  try {
    decoded = JSON.parse(text);
  } catch (error) {
    if (error instanceof SyntaxError) {
      return null;
    }
    throw error;
  }
  const parsed = schema.safeParse(decoded);
  return parsed.success ? parsed.data : null;
};

export const httpFailure = (status: number, text: string): HubFailure => {
  const body = decodeJson(ErrorBodySchema, text);
  return {
    error: body?.error ?? null,
    kind: 'http',
    message: body?.message ?? null,
    reason: body?.reason ?? null,
    status,
  };
};

const transportFailure = (
  result: Extract<SendResult, { kind: 'aborted' | 'network' }>,
): HubFailure => ({ kind: result.kind });

const userPath = (session: LabSession): string =>
  `/hub/api/users/${encodeURIComponent(session.username)}`;

type ModelResult<T> =
  | { readonly failure: HubFailure; readonly kind: 'failed' }
  | { readonly kind: 'model'; readonly model: T };

const fetchModel = async <T>(
  input: SendInput,
  accepted: readonly number[],
  schema: z.ZodType<T>,
): Promise<ModelResult<T>> => {
  const result = await sendJson(input);
  if (result.kind !== 'response') {
    return { failure: transportFailure(result), kind: 'failed' };
  }
  if (!accepted.includes(result.status)) {
    return {
      failure: httpFailure(result.status, result.text),
      kind: 'failed',
    };
  }
  const model = decodeJson(schema, result.text);
  return model === null
    ? { failure: { kind: 'malformed' }, kind: 'failed' }
    : { kind: 'model', model };
};

export const isStatus = (failure: HubFailure, status: number): boolean =>
  failure.kind === 'http' && failure.status === status;

export type LoginIntent = 'create' | 'resume';

export type LoginResult =
  | {
      readonly created: boolean;
      readonly kind: 'session';
      readonly session: LabSession;
    }
  | { readonly failure: HubFailure; readonly kind: 'refused' }
  // 410 is handled separately so callers can replace the stored token.
  | { readonly kind: 'gone' };

export const login = async ({
  intent,
  signal,
  token,
  turnstile,
}: {
  readonly intent: LoginIntent;
  readonly signal?: AbortSignal;
  readonly token: string;
  readonly turnstile?: null | string;
}): Promise<LoginResult> => {
  const result = await fetchModel(
    {
      body: {
        intent,
        token,
        ...(typeof turnstile === 'string' && { turnstile }),
      },
      method: 'POST',
      signal,
      url: '/hub/lab/login',
    },
    [200],
    LoginSchema,
  );
  if (result.kind === 'failed') {
    return isStatus(result.failure, 410)
      ? { kind: 'gone' }
      : { failure: result.failure, kind: 'refused' };
  }
  return {
    created: result.model.created,
    kind: 'session',
    session: {
      apiToken: result.model.apiToken,
      maxTerminals: result.model.maxTerminals,
      tokenExpiresAt: result.model.tokenExpiresAt,
      username: result.model.username,
    },
  };
};

export type SpawnResult =
  | { readonly failure: HubFailure; readonly kind: 'failed' }
  | { readonly kind: 'ready' }
  | { readonly kind: 'start-failed'; readonly message: null | string };

const pollUntilReady = async (
  session: LabSession,
  signal?: AbortSignal,
): Promise<SpawnResult> => {
  const deadline = Date.now() + SPAWN_DEADLINE_MS;
  while (Date.now() < deadline) {
    try {
      await waitForAbortSignal(delay(POLL_INTERVAL_MS), signal);
    } catch {
      return { failure: { kind: 'aborted' }, kind: 'failed' };
    }
    const polled = await fetchModel(
      { method: 'GET', session, signal, url: userPath(session) },
      [200],
      UserSchema,
    );
    if (polled.kind === 'failed') {
      return { failure: polled.failure, kind: 'failed' };
    }
    if (polled.model.servers?.['']?.ready === true) {
      return { kind: 'ready' };
    }
    if ((polled.model.pending ?? null) === null) {
      return { kind: 'start-failed', message: null };
    }
  }
  return { kind: 'start-failed', message: null };
};

export const spawnServer = async (
  session: LabSession,
  signal?: AbortSignal,
): Promise<SpawnResult> => {
  const started = await sendJson({
    method: 'POST',
    session,
    signal,
    url: `${userPath(session)}/server`,
  });
  if (started.kind !== 'response') {
    return { failure: transportFailure(started), kind: 'failed' };
  }
  if (started.status === 201) {
    return { kind: 'ready' };
  }
  // 400 means already running; poll it like 202.
  if (started.status === 202 || started.status === 400) {
    return pollUntilReady(session, signal);
  }
  if (started.status >= 500) {
    const failure = httpFailure(started.status, started.text);
    return {
      kind: 'start-failed',
      message: failure.kind === 'http' ? failure.message : null,
    };
  }
  return { failure: httpFailure(started.status, started.text), kind: 'failed' };
};

export type HubCallResult =
  | { readonly failure: HubFailure; readonly kind: 'failed' }
  | { readonly kind: 'ok' };

const expectStatus = async (
  input: SendInput,
  accepted: readonly number[],
): Promise<HubCallResult> => {
  const result = await sendJson(input);
  if (result.kind !== 'response') {
    return { failure: transportFailure(result), kind: 'failed' };
  }
  return accepted.includes(result.status)
    ? { kind: 'ok' }
    : { failure: httpFailure(result.status, result.text), kind: 'failed' };
};

export const stopServer = async (
  session: LabSession,
  signal?: AbortSignal,
): Promise<HubCallResult> =>
  expectStatus(
    { method: 'DELETE', session, signal, url: `${userPath(session)}/server` },
    [202, 204],
  );

// Poll after an asynchronous stop; a pending stop makes spawn return 400.
const pollUntilStopped = async (
  session: LabSession,
  signal?: AbortSignal,
): Promise<HubCallResult> => {
  const deadline = Date.now() + STOP_DEADLINE_MS;
  while (Date.now() < deadline) {
    try {
      await waitForAbortSignal(delay(POLL_INTERVAL_MS), signal);
    } catch {
      return { failure: { kind: 'aborted' }, kind: 'failed' };
    }
    const polled = await fetchModel(
      { method: 'GET', session, signal, url: userPath(session) },
      [200],
      UserSchema,
    );
    if (polled.kind === 'failed') {
      return { failure: polled.failure, kind: 'failed' };
    }
    if (
      (polled.model.pending ?? null) === null &&
      polled.model.servers?.[''] === undefined
    ) {
      return { kind: 'ok' };
    }
  }
  return { failure: { kind: 'network' }, kind: 'failed' };
};

// Restart replaces the container without deleting its home; discard is separate
// and destructive.
export const restartServer = async (
  session: LabSession,
  signal?: AbortSignal,
): Promise<SpawnResult> => {
  const stopped = await stopServer(session, signal);
  if (stopped.kind === 'failed') {
    // 400 and 404 both mean there is nothing to stop.
    if (!isStatus(stopped.failure, 400) && !isStatus(stopped.failure, 404)) {
      return { failure: stopped.failure, kind: 'failed' };
    }
  } else {
    const settled = await pollUntilStopped(session, signal);
    if (settled.kind === 'failed') {
      return { failure: settled.failure, kind: 'failed' };
    }
  }
  return spawnServer(session, signal);
};

export const discardEnvironmentServerSide = async (
  session: LabSession,
  signal?: AbortSignal,
): Promise<HubCallResult> =>
  expectStatus(
    { method: 'POST', session, signal, url: '/hub/lab/discard' },
    [204],
  );

export type UrlTokenResult =
  | { readonly failure: HubFailure; readonly kind: 'failed' }
  | { readonly kind: 'token'; readonly token: string };

// URLs carry only a 60-second, servers-only token; the SPA token stays in the
// Authorization header.
export const mintUrlToken = async (
  session: LabSession,
  signal?: AbortSignal,
): Promise<UrlTokenResult> => {
  const result = await fetchModel(
    {
      body: {
        // eslint-disable-next-line camelcase -- the hub's token API field name
        expires_in: URL_TOKEN_TTL_S,
        note: 'url',
        scopes: [`access:servers!user=${session.username}`],
      },
      method: 'POST',
      session,
      signal,
      url: `${userPath(session)}/tokens`,
    },
    [201],
    TokenSchema,
  );
  return result.kind === 'failed'
    ? { failure: result.failure, kind: 'failed' }
    : { kind: 'token', token: result.model.token };
};
