import { z } from 'zod';

import { readConfig } from '@/lib/config';
import { loadTurnstileApi } from '@/lib/turnstile-api';
import { createTurnstileAttempt } from '@/lib/turnstile-attempt';
import {
  type ChallengeHost,
  claimSharedChallengeHost,
} from '@/lib/turnstile-host';
import { createAttemptSpacing } from '@/lib/turnstile-spacing';

export type ChallengeAction = 'env_create' | 'session_keep' | 'session_start';

export type ChallengeChrome = {
  readonly body: string;
  readonly title: string;
  readonly verifying: string;
};

export type Solved = {
  readonly nonce: string;
  readonly token: string;
};

const NonceResponseSchema = z.object({ nonce: z.string() });
const spacing = createAttemptSpacing(1_000);

const requestNonce = async (
  action: ChallengeAction,
  environmentToken: null | string,
  signal?: AbortSignal,
): Promise<null | string> => {
  try {
    const response = await fetch('/api/challenge/nonce', {
      body: JSON.stringify({ action, environmentToken }),
      headers: { 'Content-Type': 'application/json' },
      method: 'POST',
      ...(signal !== undefined && { signal }),
    });
    if (!response.ok) return null;
    const parsed = NonceResponseSchema.safeParse(await response.json());
    return parsed.success ? parsed.data.nonce : null;
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError')
      return null;
    if (!(error instanceof TypeError || error instanceof SyntaxError))
      throw error;
    return null;
  }
};

export const isChallengeConfigured = async (
  signal?: AbortSignal,
): Promise<boolean> => {
  const { sitekey } = await readConfig(signal);
  return sitekey !== null;
};

export const solveChallenge = async (
  action: ChallengeAction,
  {
    chrome,
    environmentToken,
    issued,
    onInteractive,
    signal,
    target,
  }: {
    readonly chrome?: ChallengeChrome;
    readonly environmentToken?: null | string;
    readonly issued?: string;
    readonly onInteractive?: () => void;
    readonly signal?: AbortSignal;
    readonly target?: HTMLElement;
  } = {},
): Promise<null | Solved> => {
  const attempt = createTurnstileAttempt(signal);
  const configured = await attempt.waitFor(readConfig(signal));
  if (configured.kind === 'aborted' || configured.value.sitekey === null) {
    attempt.dispose();
    return null;
  }

  const host: ChallengeHost | null =
    target === undefined && chrome !== undefined
      ? claimSharedChallengeHost(chrome)
      : null;
  const loaded = await attempt.waitFor(loadTurnstileApi());
  if (loaded.kind === 'aborted' || loaded.value === null) {
    host?.release();
    attempt.dispose();
    return null;
  }

  let nonce: null | string = issued ?? null;
  if (nonce === null) {
    const requested = await attempt.waitFor(
      requestNonce(action, environmentToken ?? null, signal),
    );
    if (requested.kind === 'aborted') {
      host?.release();
      attempt.dispose();
      return null;
    }
    nonce = requested.value;
  }

  const renderTarget = target ?? host?.target;
  if (
    nonce === null ||
    renderTarget === undefined ||
    signal?.aborted === true
  ) {
    host?.release();
    attempt.dispose();
    return null;
  }

  return attempt.render({
    action,
    api: loaded.value,
    ...(host !== null && { host }),
    nonce,
    ...(onInteractive !== undefined && { onInteractive }),
    sitekey: configured.value.sitekey,
    spacing,
    target: renderTarget,
  });
};
