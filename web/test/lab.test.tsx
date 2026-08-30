import { renderToStaticMarkup } from 'react-dom/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { Lab } from '@/components/Lab';

const header = vi.hoisted(() => {
  let onToggleFiles: (() => void) | undefined;
  return {
    capture: (toggle: () => void) => {
      onToggleFiles = toggle;
    },
    read: () => onToggleFiles,
    reset: () => {
      onToggleFiles = undefined;
    },
  };
});

const files = vi.hoisted(() => ({
  downloadDirectory: vi.fn(),
  enqueue: vi.fn(() => []),
  refresh: vi.fn(),
  uploading: false,
  uploads: [],
}));

vi.mock('@/components/FilePanel', () => ({ FilePanel: () => null }));
vi.mock('@/components/Header', () => ({
  Header: ({ onToggleFiles }: { readonly onToggleFiles: () => void }) => {
    header.capture(onToggleFiles);
    return null;
  },
}));
vi.mock('@/components/LabTerminal', () => ({ LabTerminal: () => null }));
vi.mock('@/components/NewEnvironmentDialog', () => ({
  NewEnvironmentDialog: () => null,
}));
vi.mock('@/components/SessionOverlay', () => ({ SessionOverlay: () => null }));
vi.mock('@/hooks/useDropZone', () => ({ useDropZone: () => false }));
vi.mock('@/hooks/useFiles', () => ({ useFiles: () => files }));
vi.mock('@/hooks/useLanguage', () => ({
  useLanguage: () => ({
    t: { files: { errors: {} }, session: {} },
  }),
}));
vi.mock('@/hooks/useLabSession', () => ({
  useLabSession: () => ({
    containerRef: { current: null },
    created: true,
    end: null,
    fitTerminal: vi.fn(),
    focusTerminal: vi.fn(),
    markActive: vi.fn(),
    refreshStorage: vi.fn(),
    restart: vi.fn(),
    session: {},
    setTransferring: vi.fn(),
    startFresh: vi.fn(),
    status: 'running',
    storage: null,
  }),
}));
vi.mock('@/hooks/useUnloadWarning', () => ({ useUnloadWarning: vi.fn() }));

describe('lab file browser', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    header.reset();
  });

  it('refreshes the current directory when the closed panel opens', () => {
    renderToStaticMarkup(<Lab />);
    const toggle = header.read();
    expect(toggle).toBeDefined();

    toggle?.();

    expect(files.refresh).toHaveBeenCalledOnce();
  });
});
