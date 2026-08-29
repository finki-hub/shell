import { z } from 'zod';

import { waitForAbortSignal } from '@/lib/session-generation';

// Caddy renders this from `{env.TURNSTILE_SITEKEY}` with a `respond`
// directive, so an unset key arrives as an empty string rather than as a null
// or a missing field (contract §10). Empty means "no widget", which is the
// same thing the fallback says.
const ConfigSchema = z.object({ sitekey: z.string() });

export type LabConfig = { readonly sitekey: null | string };

// What the page assumes if the server cannot be asked. No sitekey means no
// widget: a page that cannot reach its own origin is not going to reach
// Cloudflare either, and rendering a challenge nobody can solve would turn a
// transient failure into a locked door.
const FALLBACK: LabConfig = { sitekey: null };

const cache: { value: LabConfig | null } = { value: null };

type ConfigLoad =
  | { readonly kind: 'fallback'; readonly value: LabConfig }
  | { readonly kind: 'resolved'; readonly value: LabConfig };

const load = async (signal?: AbortSignal): Promise<ConfigLoad> => {
  try {
    const response = await fetch('/config.json', {
      ...(signal !== undefined && { signal }),
    });
    signal?.throwIfAborted();
    const parsed = ConfigSchema.safeParse(await response.json());
    signal?.throwIfAborted();

    if (parsed.success) {
      return {
        kind: 'resolved',
        value: {
          sitekey: parsed.data.sitekey === '' ? null : parsed.data.sitekey,
        },
      };
    }
  } catch (error) {
    if (signal?.aborted === true) {
      signal.throwIfAborted();
    }
    if (!(error instanceof Error)) {
      throw error;
    }
  }

  return { kind: 'fallback', value: FALLBACK };
};

// Only a real answer is cached. A fallback is a statement about this moment,
// not about the deployment, so the next caller asks again.
const publish = (loaded: ConfigLoad): LabConfig => {
  if (loaded.kind === 'resolved') cache.value = loaded.value;
  return loaded.value;
};

export const readConfig = async (signal?: AbortSignal): Promise<LabConfig> => {
  if (cache.value !== null) {
    signal?.throwIfAborted();
    return cache.value;
  }

  const loaded = await waitForAbortSignal(load(signal), signal);
  signal?.throwIfAborted();
  return publish(loaded);
};
