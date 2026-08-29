import type { Solved } from '@/lib/turnstile';
import type { TurnstileApi } from '@/lib/turnstile-api';
import type { ChallengeHost } from '@/lib/turnstile-host';
import type { AttemptSpacing } from '@/lib/turnstile-spacing';

const REVEAL_AFTER_MS = 2_500;
const SOLVE_TIMEOUT_MS = 60_000;

const readyOutcome = async <T>(pending: Promise<T>) => ({
  kind: 'ready' as const,
  value: await pending,
});

const abortedOutcome = async (pending: Promise<void>) => {
  await pending;
  return { kind: 'aborted' as const };
};

export type RenderedAttemptInput = {
  readonly action: string;
  readonly api: TurnstileApi;
  readonly host?: ChallengeHost;
  readonly nonce: string;
  readonly onInteractive?: () => void;
  readonly sitekey: string;
  readonly spacing: AttemptSpacing;
  readonly target: HTMLElement;
};

export type TurnstileAttempt = {
  readonly dispose: () => void;
  readonly render: (input: RenderedAttemptInput) => Promise<null | Solved>;
  readonly waitFor: <T>(
    pending: Promise<T>,
  ) => Promise<
    { readonly kind: 'aborted' } | { readonly kind: 'ready'; readonly value: T }
  >;
};

export const createTurnstileAttempt = (
  signal?: AbortSignal,
): TurnstileAttempt => {
  const controller = new AbortController();
  const abort = (): void => {
    controller.abort();
  };
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted === true) controller.abort();
  const aborted = new Promise<void>((resolve) => {
    if (controller.signal.aborted) {
      resolve();
      return;
    }
    controller.signal.addEventListener(
      'abort',
      () => {
        resolve();
      },
      {
        once: true,
      },
    );
  });

  const detach = (): void => {
    signal?.removeEventListener('abort', abort);
  };

  return {
    dispose: () => {
      controller.abort();
      detach();
    },
    render: async (input) => {
      const permit = await input.spacing.acquire(controller.signal);
      if (permit === 'aborted' || controller.signal.aborted) {
        input.host?.release();
        detach();
        return null;
      }

      return new Promise((resolve) => {
        let removed = false;
        let terminal = false;
        let widget: null | string = null;
        let abortRender: (() => void) | null = null;
        let revealTimer: null | ReturnType<typeof setTimeout> = null;
        let solveTimer: null | ReturnType<typeof setTimeout> = null;
        const removeWidget = (): void => {
          if (removed || widget === null) return;
          removed = true;
          try {
            input.api.remove(widget);
          } catch {
            // Third-party removal is best-effort; owned settlement must continue.
            widget = null;
          }
        };
        const settle = (solved: null | Solved): void => {
          if (terminal) return;
          terminal = true;
          if (revealTimer !== null) clearTimeout(revealTimer);
          if (solveTimer !== null) clearTimeout(solveTimer);
          if (abortRender !== null) {
            controller.signal.removeEventListener('abort', abortRender);
          }
          controller.abort();
          detach();
          removeWidget();
          input.host?.release();
          resolve(solved);
        };
        revealTimer = setTimeout(() => {
          input.host?.reveal();
        }, REVEAL_AFTER_MS);
        solveTimer = setTimeout(() => {
          settle(null);
        }, SOLVE_TIMEOUT_MS);
        abortRender = () => {
          settle(null);
        };
        controller.signal.addEventListener('abort', abortRender, {
          once: true,
        });
        if (controller.signal.aborted) {
          settle(null);
          return;
        }
        widget = input.api.render(input.target, {
          action: input.action,
          appearance: 'interaction-only',
          'before-interactive-callback': () => {
            if (terminal) return;
            input.host?.reveal();
            input.host?.showSlot();
            input.onInteractive?.();
          },
          callback: (token) => {
            settle({ nonce: input.nonce, token });
          },
          cdata: input.nonce,
          'error-callback': () => {
            settle(null);
          },
          execution: 'execute',
          sitekey: input.sitekey,
          'timeout-callback': () => {
            settle(null);
          },
        });
        const isTerminal = (): boolean => terminal;
        if (isTerminal()) {
          removeWidget();
          return;
        }
        input.api.execute(widget);
      });
    },
    waitFor: async <T>(pending: Promise<T>) =>
      Promise.race([readyOutcome(pending), abortedOutcome(aborted)]),
  };
};
