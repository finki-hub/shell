import { useSyncExternalStore } from 'react';

const storageKey = 'lab.chime.muted';

// Guarded because this runs at import time, and localStorage *throws* in a
// browser that blocks site data — private mode with the strictest settings,
// an enterprise policy, a site exception. Unguarded, the throw happens before
// React renders anything and the user gets a blank white page rather than a
// missing preference.
const read = () => {
  try {
    return localStorage.getItem(storageKey) === '1';
  } catch {
    return false;
  }
};

const store: { listeners: Set<() => void>; muted: boolean } = {
  listeners: new Set(),
  muted: read(),
};

const subscribe = (listener: () => void) => {
  store.listeners.add(listener);

  return () => {
    store.listeners.delete(listener);
  };
};

// Read outside React by the chime itself, which fires from a WebSocket message
// rather than from a render.
export const isChimeMuted = () => store.muted;

export const setChimeMuted = (muted: boolean) => {
  store.muted = muted;

  try {
    localStorage.setItem(storageKey, muted ? '1' : '0');
  } catch {
    // The preference holds for this page and is simply not remembered.
  }

  for (const listener of store.listeners) {
    listener();
  }
};

export const useChimeMuted = () =>
  useSyncExternalStore(subscribe, () => store.muted);
