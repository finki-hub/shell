import { isChimeMuted } from '../hooks/useChime';

// Prefixed rather than replaced, and the prefix goes first so it survives the
// truncation a browser applies to a narrow tab: a title that reads
// "FINKI Hub / She…" says nothing at the only moment it matters. The word is
// passed in rather than written here, because the moment attention is needed is
// not the moment to switch language on somebody.
//
// No glyph, and the favicon is left alone. Both were louder than the thing they
// were announcing — a badged icon and a warning sign for a check that is
// usually answered before anybody looks, and the times it is not, there is
// already a dialog or a banner saying so in words.
const leaseKey: unique symbol = Symbol('attention-owner');

export type AttentionLease = {
  readonly [leaseKey]: symbol;
};

export type AttentionPriority = 'challenge' | 'deployment';

type LeasedAttention = {
  readonly lease: AttentionLease;
  prefix: string;
  readonly priority: AttentionPriority;
  readonly sequence: number;
};

const attention: {
  baseTitle: null | string;
  leases: LeasedAttention[];
  sequence: number;
} = { baseTitle: null, leases: [], sequence: 0 };

const priorityValue = (priority: AttentionPriority): number =>
  priority === 'deployment' ? 2 : 1;

const owner = (): LeasedAttention | null => {
  let selected: LeasedAttention | null = null;
  for (const candidate of attention.leases) {
    if (
      selected === null ||
      priorityValue(candidate.priority) > priorityValue(selected.priority) ||
      (candidate.priority === selected.priority &&
        candidate.sequence > selected.sequence)
    ) {
      selected = candidate;
    }
  }
  return selected;
};

const renderTitle = (): void => {
  if (attention.baseTitle === null) {
    return;
  }
  const currentOwner = owner();
  document.title =
    currentOwner === null
      ? attention.baseTitle
      : `${currentOwner.prefix} - ${attention.baseTitle}`;
};

export const raiseAttention = (
  prefix: string,
  priority: AttentionPriority = 'challenge',
) => {
  attention.baseTitle ??= document.title;
  const lease: AttentionLease = { [leaseKey]: Symbol('attention-lease') };
  attention.sequence += 1;
  attention.leases.push({
    lease,
    prefix,
    priority,
    sequence: attention.sequence,
  });
  renderTitle();

  return lease;
};

export const refreshAttention = (lease: AttentionLease, prefix: string) => {
  const leasedAttention = attention.leases.find(
    (candidate) => candidate.lease === lease,
  );
  if (leasedAttention === undefined || attention.baseTitle === null) {
    return;
  }

  leasedAttention.prefix = prefix;
  renderTitle();
};

// The tab carries the product name until something needs attention, and a
// user can switch language while an alert is up. Writing straight to
// document.title there would be undone by the restore below, so a language
// change during an alert changes what the restore lands on rather than what is
// on screen at the moment it matters.
export const setBaseTitle = (title: string) => {
  if (attention.baseTitle === null) {
    document.title = title;

    return;
  }

  attention.baseTitle = title;
  renderTitle();
};

export const clearAttention = (lease: AttentionLease) => {
  const index = attention.leases.findIndex(
    (candidate) => candidate.lease === lease,
  );
  if (index === -1 || attention.baseTitle === null) {
    return;
  }

  attention.leases.splice(index, 1);
  renderTitle();
  if (attention.leases.length === 0) {
    attention.baseTitle = null;
  }
};

// Synthesised rather than shipped as a file: nothing to load at the moment it
// is needed, and it sidesteps media-src entirely.
const audio: { context: AudioContext | null } = { context: null };

// Created during a real gesture on our own page, not at chime time and not
// from the click inside the Turnstile iframe — cross-origin activation does
// not propagate the same way in every browser, and an AudioContext created
// without a gesture starts suspended and stays silent.
export const primeAudio = () => {
  audio.context ??= new AudioContext();

  if (audio.context.state === 'suspended') {
    void audio.context.resume();
  }
};

const tone = (context: AudioContext, at: number, frequency: number) => {
  const oscillator = context.createOscillator();
  const gain = context.createGain();
  oscillator.frequency.value = frequency;
  oscillator.type = 'sine';
  // Shaped rather than switched: a bare start/stop on an oscillator clicks.
  gain.gain.setValueAtTime(0, at);
  gain.gain.linearRampToValueAtTime(0.18, at + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.35);
  oscillator.connect(gain).connect(context.destination);
  oscillator.start(at);
  oscillator.stop(at + 0.4);
};

const playTone = (): void => {
  const context = audio.context;

  if (context?.state !== 'running') {
    return;
  }

  const now = context.currentTime;
  tone(context, now, 880);
  tone(context, now + 0.18, 1_174.66);
};

// Only when the user is somewhere else. A visible tab behind another window
// still reports `visible`, so focus has to be asked about separately — playing
// a sound at somebody already looking at the screen is just rude.
export const playChime = () => {
  if (isChimeMuted() || (!document.hidden && document.hasFocus())) {
    return;
  }

  playTone();
};

export const playFocusedChime = () => {
  if (isChimeMuted() || document.hidden || !document.hasFocus()) {
    return;
  }

  playTone();
};
