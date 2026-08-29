export type TurnstileApi = {
  readonly execute: (widget: string) => void;
  readonly remove: (widget: string) => void;
  readonly render: (
    target: HTMLElement,
    options: TurnstileRenderOptions,
  ) => string;
};

export type TurnstileRenderOptions = {
  readonly action: string;
  readonly appearance: 'interaction-only';
  readonly 'before-interactive-callback': () => void;
  readonly callback: (token: string) => void;
  readonly cdata: string;
  readonly 'error-callback': () => void;
  readonly execution: 'execute';
  readonly sitekey: string;
  readonly 'timeout-callback': () => void;
};

type TurnstileGlobal = typeof globalThis & {
  readonly turnstile?: TurnstileApi;
};

const turnstileGlobal: TurnstileGlobal = globalThis;

const API_SRC =
  'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
const LOAD_TIMEOUT_MS = 10_000;
const state: { pending: null | Promise<null | TurnstileApi> } = {
  pending: null,
};

const injectApi = (): Promise<null | TurnstileApi> =>
  new Promise((resolve) => {
    if (turnstileGlobal.turnstile !== undefined) {
      resolve(turnstileGlobal.turnstile);
      return;
    }

    const script = document.createElement('script');
    let terminal = false;
    let timer: null | ReturnType<typeof setTimeout> = null;
    const settle = (api: null | TurnstileApi): void => {
      if (terminal) return;
      terminal = true;
      if (timer !== null) clearTimeout(timer);
      resolve(api);
    };
    timer = setTimeout(() => {
      script.remove();
      settle(null);
    }, LOAD_TIMEOUT_MS);
    script.addEventListener('load', () => {
      settle(turnstileGlobal.turnstile ?? null);
    });
    script.addEventListener('error', () => {
      settle(null);
    });
    script.async = true;
    script.src = API_SRC;
    document.head.append(script);
  });

export const loadTurnstileApi = async (): Promise<null | TurnstileApi> => {
  state.pending ??= injectApi();
  const pending = state.pending;
  const api = await pending;
  if (api === null && state.pending === pending) state.pending = null;
  return api;
};
