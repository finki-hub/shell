import { beforeEach, describe, expect, it, vi } from 'vitest';

const controls = vi.hoisted<{
  audioState: 'running' | 'suspended';
  contexts: FakeContext[];
  muted: boolean;
  resumeCalls: number;
}>(() => ({
  audioState: 'running',
  contexts: [] as FakeContext[],
  muted: false,
  resumeCalls: 0,
}));

type FakeContext = {
  readonly createGain: () => FakeGain;
  readonly createOscillator: () => FakeOscillator;
  readonly currentTime: number;
  readonly destination: object;
  readonly oscillators: FakeOscillator[];
  readonly resume: () => Promise<void>;
  readonly state: 'running' | 'suspended';
};

type FakeGain = {
  readonly connect: () => FakeGain;
  readonly gain: {
    readonly exponentialRampToValueAtTime: () => void;
    readonly linearRampToValueAtTime: () => void;
    readonly setValueAtTime: () => void;
  };
};

type FakeOscillator = {
  readonly connect: (gain: FakeGain) => FakeGain;
  readonly frequency: { value: number };
  readonly start: () => void;
  readonly stop: () => void;
  type: string;
};

const noOp = (): void => {};

const createGain = (): FakeGain => {
  const gain: FakeGain = {
    connect: () => gain,
    gain: {
      exponentialRampToValueAtTime: noOp,
      linearRampToValueAtTime: noOp,
      setValueAtTime: noOp,
    },
  };
  return gain;
};

const createOscillator = (): FakeOscillator => ({
  connect: (gain) => gain,
  frequency: { value: 0 },
  start: noOp,
  stop: noOp,
  type: '',
});

const createContext = (): FakeContext => {
  const context: FakeContext = {
    createGain,
    createOscillator: () => {
      const oscillator = createOscillator();
      context.oscillators.push(oscillator);
      return oscillator;
    },
    currentTime: 1,
    destination: {},
    oscillators: [],
    resume: () => {
      controls.resumeCalls += 1;
      return Promise.resolve();
    },
    state: controls.audioState,
  };
  controls.contexts.push(context);
  return context;
};

const chimeMuted = (): boolean => controls.muted;

const TestAudioContext = function (): FakeContext {
  return createContext();
};

vi.mock('@/hooks/useChime', () => ({
  isChimeMuted: chimeMuted,
}));

const loadAttention = async () => {
  vi.resetModules();
  return import('@/lib/attention');
};

const firstContext = (): FakeContext => {
  const context = controls.contexts[0];
  if (context === undefined) {
    throw new Error('Expected primed audio context');
  }
  return context;
};

beforeEach(() => {
  Object.assign(controls, {
    audioState: 'running',
    contexts: [],
    muted: false,
    resumeCalls: 0,
  });
  vi.stubGlobal('AudioContext', TestAudioContext);
  vi.stubGlobal('document', {
    hasFocus: () => true,
    hidden: false,
    title: 'FINKI Hub / Shell',
  });
});

describe('attention chimes', () => {
  it('primes a suspended context during a gesture', async () => {
    controls.audioState = 'suspended';
    const attention = await loadAttention();

    attention.primeAudio();

    expect(controls.resumeCalls).toBe(1);
  });

  it('plays the unchanged two-tone chime only in a focused eligible tab', async () => {
    const attention = await loadAttention();
    attention.primeAudio();
    const context = firstContext();

    attention.playFocusedChime();
    controls.muted = true;
    attention.playFocusedChime();

    expect(context.oscillators).toHaveLength(2);
  });

  it('keeps the existing chime for an unfocused tab only', async () => {
    const attention = await loadAttention();
    attention.primeAudio();
    const context = firstContext();
    vi.stubGlobal('document', {
      hasFocus: () => false,
      hidden: false,
      title: 'FINKI Hub / Shell',
    });

    attention.playChime();

    expect(context.oscillators).toHaveLength(2);
  });
});
