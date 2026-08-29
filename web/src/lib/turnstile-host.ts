import type { ChallengeChrome } from '@/lib/turnstile';

export type ChallengeHost = {
  readonly release: () => void;
  readonly reveal: () => void;
  readonly showSlot: () => void;
  readonly target: HTMLElement;
};

type ActiveHost = {
  readonly card: HTMLElement;
  readonly host: HTMLElement;
  readonly owner: symbol;
  readonly shownClass: string;
  readonly slot: HTMLElement;
};

const state: { active: ActiveHost | null } = { active: null };

const isActive = (owner: symbol): boolean => state.active?.owner === owner;

export const claimSharedChallengeHost = (
  chrome: ChallengeChrome,
): ChallengeHost => {
  state.active?.host.remove();
  const mounted = document.querySelector('main') ?? document.body;
  const placement = mounted === document.body ? 'fixed' : 'absolute';
  const host = document.createElement('div');
  host.className = `pointer-events-none ${placement} inset-0 z-40 flex items-center justify-center px-4 [&>*]:pointer-events-auto`;
  const card = document.createElement('div');
  card.className =
    'pointer-events-none flex w-full max-w-sm flex-col items-center gap-4 rounded-xl border bg-card p-6 text-center opacity-0 shadow-sm';
  card.ariaHidden = 'true';
  const title = document.createElement('p');
  title.className = 'text-lg font-semibold tracking-tight text-card-foreground';
  title.textContent = chrome.title;
  const body = document.createElement('p');
  body.className = 'text-sm text-muted-foreground';
  body.textContent = chrome.body;
  const slot = document.createElement('div');
  slot.className = 'flex min-h-[70px] w-full items-center justify-center gap-2';
  const pending = document.createElement('div');
  pending.dataset['pending'] = 'true';
  pending.className = 'flex items-center gap-2 text-sm text-muted-foreground';
  const spinner = document.createElement('span');
  spinner.className =
    'size-4 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-muted-foreground';
  const label = document.createElement('span');
  label.textContent = chrome.verifying;
  pending.append(spinner, label);
  slot.append(pending);
  card.append(title, body, slot);
  host.append(card);
  mounted.append(host);

  const owner = Symbol('turnstile-host-owner');
  const shownClass = `${placement} inset-0 z-40 flex animate-in items-center justify-center bg-neutral-950/60 px-4 backdrop-blur-sm fade-in-0`;
  state.active = { card, host, owner, shownClass, slot };

  return {
    release: () => {
      if (!isActive(owner)) return;
      host.remove();
      state.active = null;
    },
    reveal: () => {
      if (!isActive(owner) || !card.classList.contains('opacity-0')) return;
      host.className = shownClass;
      card.ariaHidden = 'false';
      card.classList.remove('opacity-0', 'pointer-events-none');
      card.classList.add('animate-in', 'fade-in-0', 'zoom-in-95');
    },
    showSlot: () => {
      if (!isActive(owner)) return;
      for (const child of slot.children) {
        if (
          child instanceof HTMLElement &&
          child.dataset['pending'] === 'true'
        ) {
          child.remove();
        }
      }
    },
    target: slot,
  };
};
