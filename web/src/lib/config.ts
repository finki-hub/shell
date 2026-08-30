import { z } from 'zod';

import { waitForAbortSignal } from '@/lib/session-generation';

// Caddy emits an empty string when TURNSTILE_SITEKEY is unset.
const ConfigSchema = z.object({ sitekey: z.string() });

export type LabConfig = { readonly sitekey: null | string };

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
