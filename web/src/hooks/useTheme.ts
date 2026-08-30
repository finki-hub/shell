import { useSyncExternalStore } from 'react';

export type Theme = 'dark' | 'light';

const storageKey = 'theme';

const isTheme = (value: null | string): value is Theme =>
  value === 'dark' || value === 'light';

export const resolveTheme = (
  stored: null | string,
  systemPrefersDark: boolean,
): Theme => {
  if (isTheme(stored)) {
    return stored;
  }
  return systemPrefersDark ? 'dark' : 'light';
};

const systemTheme = (): Theme => {
  try {
    return typeof matchMedia === 'function' &&
      matchMedia('(prefers-color-scheme: dark)').matches
      ? 'dark'
      : 'light';
  } catch {
    return 'light';
  }
};

// Storage can be unavailable at import time; retain the fallback.
const getInitialTheme = (): Theme => {
  if (typeof document !== 'undefined') {
    const prepainted = document.documentElement.dataset['kbTheme'];
    if (prepainted === 'dark' || prepainted === 'light') {
      return prepainted;
    }
  }

  try {
    return resolveTheme(
      localStorage.getItem(storageKey),
      systemTheme() === 'dark',
    );
  } catch {
    return systemTheme();
  }
};

export const applyTheme = (theme: Theme): void => {
  document.documentElement.dataset['kbTheme'] = theme;
};

const persistTheme = (theme: Theme): void => {
  try {
    localStorage.setItem(storageKey, theme);
  } catch {
    // Ignore unavailable storage; keep the in-memory theme.
  }
};

const store: { listeners: Set<() => void>; theme: Theme } = {
  listeners: new Set(),
  theme: getInitialTheme(),
};

const subscribe = (listener: () => void) => {
  store.listeners.add(listener);

  return () => {
    store.listeners.delete(listener);
  };
};

export const setTheme = (theme: Theme) => {
  store.theme = theme;
  applyTheme(theme);
  persistTheme(theme);

  for (const listener of store.listeners) {
    listener();
  }
};

export const useTheme = () =>
  useSyncExternalStore(subscribe, () => store.theme);
