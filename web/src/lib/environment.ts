import { discardEnvironmentServerSide, type LabSession } from '@/lib/hub-api';

// One key, and only one. An environment now lives until the retention culler
// takes it, so there is no expired-but-downloadable state to keep a second
// token for: whatever is in here either names a live environment or names one
// the hub has already forgotten, and the 410 on login tells the page which.
const STORAGE_KEY = 'lab.environment';

// 32 random bytes in base64url — 43 characters, comfortably inside the hub's
// `^[A-Za-z0-9_-]{32,128}$`. The token is the only name the files have, so it
// is minted from the CSPRNG and never derived from anything guessable.
const TOKEN_BYTES = 32;

export const mintEnvironmentToken = (): string => {
  const bytes = new Uint8Array(TOKEN_BYTES);
  crypto.getRandomValues(bytes);
  return bytes.toBase64({ alphabet: 'base64url', omitPadding: true });
};

// localStorage rather than sessionStorage: the point is that closing the tab
// and coming back tomorrow finds the same files, within the environment's
// retention window. Losing the token means losing the work even though the
// files are still on disk.
export const readEnvironmentToken = (): null | string => {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch (error) {
    // Private browsing, or storage disabled: the user simply gets a new
    // environment every time rather than an error.
    if (error instanceof Error) {
      return null;
    }
    throw error;
  }
};

export const writeEnvironmentToken = (token: string): boolean => {
  try {
    localStorage.setItem(STORAGE_KEY, token);
    return true;
  } catch (error) {
    // Nothing to do: continuity is lost, the session still works.
    if (error instanceof Error) {
      return false;
    }
    throw error;
  }
};

// Mints on first visit and on the visit after a 410, which is the only place
// the two paths differ: a token that came out of storage asks to resume, a
// freshly minted one asks to create.
export const readOrMintEnvironmentToken = (): {
  readonly intent: 'create' | 'resume';
  readonly token: string;
} => {
  const stored = readEnvironmentToken();
  if (stored !== null) {
    return { intent: 'resume', token: stored };
  }
  const token = mintEnvironmentToken();
  writeEnvironmentToken(token);
  return { intent: 'create', token };
};

// Compare-and-clear rather than a bare remove: a second tab may have minted a
// replacement while this one was failing, and forgetting that one would strand
// an environment nobody can name.
export const forgetEnvironmentToken = (expectedToken: string): boolean => {
  try {
    if (localStorage.getItem(STORAGE_KEY) !== expectedToken) {
      return false;
    }
    localStorage.removeItem(STORAGE_KEY);
    return true;
  } catch (error) {
    // Storage is unavailable; there was nothing remembered to forget.
    if (error instanceof Error) {
      return false;
    }
    throw error;
  }
};

// "Start over": the hub stops the container, deletes the user and removes the
// home directory, and only then is the token forgotten. Forgetting first would
// leave a home directory nobody can reach until the retention culler notices.
export const discardEnvironment = async (
  session: LabSession,
  expectedToken: string,
  signal?: AbortSignal,
): Promise<boolean> => {
  const result = await discardEnvironmentServerSide(session, signal);
  if (result.kind !== 'ok') {
    return false;
  }
  forgetEnvironmentToken(expectedToken);
  return true;
};
