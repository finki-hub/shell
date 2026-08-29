import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { type LabSession } from '@/lib/hub-api';
import {
  ACTIVE_INTERVAL_MS,
  ACTIVE_WINDOW_MS,
  createStoragePoller,
  readStorage,
  type StorageResult,
} from '@/lib/storage-api';

const SESSION: LabSession = {
  apiToken: 'api-token',
  maxTerminals: 2,
  tokenExpiresAt: '2099-01-01T00:00:00Z',
  username: 'user name',
};

const USAGE: StorageResult = {
  kind: 'usage',
  usage: {
    inodesTotal: 200,
    inodesUsed: 20,
    totalBytes: 1_000,
    usedBytes: 100,
  },
};

const flush = async (): Promise<void> => {
  for (let turn = 0; turn < 10; turn += 1) await Promise.resolve();
};

describe('storage API', () => {
  const documentListeners = new Map<string, EventListener>();
  const documentState: { visibilityState: DocumentVisibilityState } = {
    visibilityState: 'visible',
  };
  const addDocumentListener = vi.fn(
    (name: string, listener: EventListenerOrEventListenerObject) => {
      if (typeof listener === 'function') documentListeners.set(name, listener);
    },
  );
  const removeDocumentListener = vi.fn((name: string) => {
    documentListeners.delete(name);
  });
  const addWindowListener = vi.fn();
  const removeWindowListener = vi.fn();

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    documentState.visibilityState = 'visible';
    documentListeners.clear();
    vi.clearAllMocks();
    vi.stubGlobal('document', {
      ...documentState,
      addEventListener: addDocumentListener,
      removeEventListener: removeDocumentListener,
      get visibilityState() {
        return documentState.visibilityState;
      },
    });
    vi.stubGlobal('addEventListener', addWindowListener);
    vi.stubGlobal('removeEventListener', removeWindowListener);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('uses the no-track URL and maps the wire model', async () => {
    const fetchRequest = vi.fn<typeof fetch>(() =>
      Promise.resolve(
        Response.json({
          bytesLimit: 1_000,
          bytesUsed: 100,
          inodesLimit: 200,
          inodesUsed: 20,
        }),
      ),
    );
    vi.stubGlobal('fetch', fetchRequest);

    await expect(readStorage(SESSION)).resolves.toEqual(USAGE);
    expect(fetchRequest.mock.calls[0]?.[0]).toBe(
      '/user/user%20name/lab/storage?no_track_activity=1',
    );
  });

  it('stops reading after the active window and refreshes only once', async () => {
    const read = vi.fn(() => Promise.resolve(USAGE));
    const poller = createStoragePoller({ onResult: vi.fn(), read });
    poller.start();
    await flush();

    await vi.advanceTimersByTimeAsync(ACTIVE_WINDOW_MS + ACTIVE_INTERVAL_MS);
    const afterWindow = read.mock.calls.length;
    await vi.advanceTimersByTimeAsync(2 * ACTIVE_INTERVAL_MS);
    expect(read).toHaveBeenCalledTimes(afterWindow);

    poller.refresh();
    await flush();
    expect(read).toHaveBeenCalledTimes(afterWindow + 1);
    await vi.advanceTimersByTimeAsync(ACTIVE_INTERVAL_MS);
    expect(read).toHaveBeenCalledTimes(afterWindow + 1);
  });

  it('keeps polling through a transfer and reads once when it ends', async () => {
    const read = vi.fn(() => Promise.resolve(USAGE));
    const poller = createStoragePoller({ onResult: vi.fn(), read });
    poller.start();
    poller.setTransferring(true);
    await vi.advanceTimersByTimeAsync(ACTIVE_WINDOW_MS + ACTIVE_INTERVAL_MS);
    const duringTransfer = read.mock.calls.length;

    poller.setTransferring(false);
    await flush();

    expect(read).toHaveBeenCalledTimes(duringTransfer + 1);
  });

  it('does not read while hidden and reads on becoming visible', async () => {
    documentState.visibilityState = 'hidden';
    const read = vi.fn(() => Promise.resolve(USAGE));
    const poller = createStoragePoller({ onResult: vi.fn(), read });
    poller.start();
    await flush();
    expect(read).not.toHaveBeenCalled();

    documentState.visibilityState = 'visible';
    documentListeners.get('visibilitychange')?.(new Event('visibilitychange'));
    await flush();

    expect(read).toHaveBeenCalledOnce();
  });

  it('stop prevents an in-flight read from rearming and detaches listeners', async () => {
    const pending = Promise.withResolvers<StorageResult>();
    const result = vi.fn();
    const read = vi.fn(() => pending.promise);
    const poller = createStoragePoller({ onResult: result, read });
    poller.start();
    await flush();

    poller.stop();
    pending.resolve(USAGE);
    await flush();

    expect(result).not.toHaveBeenCalled();
    expect(removeDocumentListener).toHaveBeenCalledWith(
      'visibilitychange',
      expect.any(Function),
    );
    expect(removeWindowListener).toHaveBeenCalledWith(
      'focus',
      expect.any(Function),
    );
    expect(vi.getTimerCount()).toBe(0);
  });
});
