import { afterEach, describe, expect, it, vi } from 'vitest';

import index from '../index.html?raw';
import session from '../src/hooks/useLabSession.ts?raw';
import css from '../src/index.css?raw';

const remoteFontPattern = /fonts\.(?:googleapis|gstatic)\.com/u;

describe('theme and font startup', () => {
  afterEach(() => {
    vi.resetModules();
    vi.unstubAllGlobals();
  });

  it('runs the same-origin prepaint bootstrap before the application module', () => {
    const bootstrap = index.indexOf('<script src="/theme.js"></script>');
    const application = index.indexOf(
      '<script src="/src/main.tsx" type="module"></script>',
    );

    expect(bootstrap).toBeGreaterThanOrEqual(0);
    expect(application).toBeGreaterThan(bootstrap);
  });

  it('selects stored theme over system and never persists an initial system choice', async () => {
    const writes: string[] = [];
    vi.stubGlobal('document', { documentElement: { dataset: {} } });
    vi.stubGlobal('localStorage', {
      getItem: () => null,
      setItem: (key: string, value: string) => {
        writes.push(`${key}:${value}`);
      },
    });
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    const { resolveTheme, setTheme } = await import('@/hooks/useTheme');

    const stored = resolveTheme('light', true);
    const system = resolveTheme(null, true);
    setTheme('light');

    expect([stored, system]).toEqual(['light', 'dark']);
    expect(writes).toEqual(['theme:light']);
  });

  it('falls back to system theme when storage is malformed or throws', async () => {
    vi.stubGlobal('document', { documentElement: { dataset: {} } });
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new DOMException('blocked', 'SecurityError');
      },
      setItem: vi.fn(),
    });
    vi.stubGlobal('matchMedia', () => ({ matches: false }));
    const { resolveTheme } = await import('@/hooks/useTheme');

    const malformed = resolveTheme('sepia', true);

    expect(malformed).toBe('dark');
    expect(document.documentElement.dataset['kbTheme']).toBeUndefined();
  });

  it('removes remote fonts and starts ticket work without waiting for fonts', () => {
    const remoteFonts = remoteFontPattern.test(css);

    expect(remoteFonts).toBe(false);
    expect(session).not.toContain('document.fonts.ready');
    expect(session).not.toContain('waitForSessionFonts');
  });
});
