import { discardEnvironmentServerSide, type LabSession } from '@/lib/hub-api';

const STORAGE_KEY = 'lab.environment';

// 32 CSPRNG bytes encoded as base64url satisfy the hub token pattern.
const TOKEN_BYTES = 32;

export const mintEnvironmentToken = (): string => {
  const bytes = new Uint8Array(TOKEN_BYTES);
  crypto.getRandomValues(bytes);
  return bytes.toBase64({ alphabet: 'base64url', omitPadding: true });
};

// localStorage preserves the token across tabs and browser restarts within retention.
export const readEnvironmentToken = (): null | string => {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch (error) {
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
    if (error instanceof Error) {
      return false;
    }
    throw error;
  }
};

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

// Compare before clearing so another tab's replacement token is preserved.
export const forgetEnvironmentToken = (expectedToken: string): boolean => {
  try {
    if (localStorage.getItem(STORAGE_KEY) !== expectedToken) {
      return false;
    }
    localStorage.removeItem(STORAGE_KEY);
    return true;
  } catch (error) {
    if (error instanceof Error) {
      return false;
    }
    throw error;
  }
};

// Server-side discard removes the home before forgetting its token.
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
