import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  archiveDownloadUrl,
  type ContentsErrorKind,
  createDirectory,
  deleteEntry,
  fileDownloadUrl,
  listDirectory,
  partName,
  uploadFile,
} from '@/lib/contents-api';
import { type LabSession } from '@/lib/hub-api';

const SESSION: LabSession = {
  apiToken: 'api-token',
  maxTerminals: 2,
  tokenExpiresAt: '2099-01-01T00:00:00Z',
  username: 'user name',
};

const response = (status: number, body = ''): Response =>
  new Response(status === 204 ? null : body, { status });

const flush = async (): Promise<void> => {
  for (let turn = 0; turn < 10; turn += 1) await Promise.resolve();
};

const PART_URL = /work%20folder\/\.notes\.txt\.[0-9a-f]{8}\.part$/u;
class Uint8ArrayDouble extends Uint8Array {
  override toBase64(): string {
    return this.length === 0 ? '' : 'encoded';
  }

  override toHex(): string {
    return [...this].map((byte) => byte.toString(16).padStart(2, '0')).join('');
  }
}

const bodyText = (body: BodyInit | null | undefined): string =>
  typeof body === 'string' ? body : '';

const requestUrl = (input: RequestInfo | URL): string => {
  if (typeof input === 'string') return input;
  return input instanceof URL ? input.href : input.url;
};

describe('contents API', () => {
  beforeEach(() => {
    vi.stubGlobal('Uint8Array', Uint8ArrayDouble);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('bypasses the browser cache when listing a directory', async () => {
    const fetchRequest = vi
      .fn<typeof fetch>()
      .mockResolvedValue(response(200, '{}'));
    vi.stubGlobal('fetch', fetchRequest);

    await listDirectory(SESSION, '');

    expect(fetchRequest.mock.calls[0]?.[1]?.cache).toBe('no-store');
  });

  it('uploads numbered chunks to a unique part and renames it', async () => {
    const fetchRequest = vi.fn(
      (_input: RequestInfo | URL, init?: RequestInit) =>
        Promise.resolve(response(init?.method === 'PATCH' ? 200 : 201)),
    );
    vi.stubGlobal('fetch', fetchRequest);
    const progress: number[] = [];
    const file = new File([new Uint8Array(2 * 1_024 * 1_024 + 1)], 'notes.txt');

    await expect(
      uploadFile(SESSION, {
        directory: 'work folder',
        file,
        onProgress: (fraction) => {
          progress.push(fraction);
        },
      }),
    ).resolves.toEqual({ ok: true, value: true });

    const writes = fetchRequest.mock.calls.slice(0, 3);
    expect(
      writes.map(
        (call) =>
          JSON.parse(bodyText(call[1]?.body)) as Readonly<
            Record<string, unknown>
          >,
      ),
    ).toEqual([
      expect.objectContaining({ chunk: 1 }),
      expect.objectContaining({ chunk: 2 }),
      expect.objectContaining({ chunk: -1 }),
    ]);
    for (const call of writes) {
      expect(call[1]?.headers).toMatchObject({
        'X-Upload-Size': String(file.size),
      });
      expect(requestUrl(call[0])).toMatch(PART_URL);
    }
    expect(fetchRequest.mock.calls[3]?.[1]?.method).toBe('PATCH');
    expect(progress).toEqual([1 / 3, 2 / 3, 1]);
    expect(partName('notes.txt')).not.toBe(partName('notes.txt'));
  });

  it('sends a one-chunk file without a chunk field', async () => {
    const fetchRequest = vi.fn(
      (_input: RequestInfo | URL, init?: RequestInit) =>
        Promise.resolve(response(init?.method === 'PATCH' ? 200 : 201)),
    );
    vi.stubGlobal('fetch', fetchRequest);

    await uploadFile(SESSION, {
      directory: '',
      file: new File(['short'], 'short.txt'),
    });

    const body = JSON.parse(
      bodyText(fetchRequest.mock.calls[0]?.[1]?.body),
    ) as Readonly<Record<string, unknown>>;
    expect(body).not.toHaveProperty('chunk');
  });

  it.each([
    [400, 'bad-request'],
    [507, 'insufficient-storage'],
    [409, 'conflict'],
    [429, 'rate-limited'],
    [413, 'too-large'],
    [424, 'unavailable'],
  ] as const)('maps status %i to %s', async (status, kind) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(response(status, '{}'))),
    );

    const result = await deleteEntry(SESSION, 'file.txt');

    expect(result).toMatchObject({ error: { kind }, ok: false });
  });

  it('treats a file blocking folder creation as a name conflict', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          response(
            400,
            JSON.stringify({ message: 'Not a directory: /home/ubuntu/notes' }),
          ),
        ),
      ),
    );

    const result = await createDirectory(SESSION, {
      directory: '',
      name: 'notes',
    });

    expect(result).toMatchObject({ error: { kind: 'conflict' }, ok: false });
  });

  it.each(['failure', 'abort'] as const)(
    'deletes the part after %s',
    async (outcome) => {
      const fetchRequest = vi.fn(
        (_input: RequestInfo | URL, init?: RequestInit) => {
          if (init?.method === 'DELETE') {
            return Promise.resolve(response(204));
          }
          return outcome === 'abort'
            ? Promise.reject(new DOMException('cancelled', 'AbortError'))
            : Promise.resolve(response(507, '{}'));
        },
      );
      vi.stubGlobal('fetch', fetchRequest);

      const result = await uploadFile(SESSION, {
        directory: '',
        file: new File(['data'], 'failed.txt'),
      });
      await flush();

      const expected: ContentsErrorKind =
        outcome === 'abort' ? 'aborted' : 'insufficient-storage';
      expect(result).toMatchObject({ error: { kind: expected }, ok: false });
      expect(fetchRequest.mock.calls.at(-1)?.[1]?.method).toBe('DELETE');
    },
  );

  it('builds both download URLs exactly', () => {
    expect(fileDownloadUrl(SESSION, 'work/a b.txt', 'short token')).toBe(
      '/user/user%20name/files/work/a%20b.txt?download=1&token=short%20token',
    );
    expect(
      archiveDownloadUrl(SESSION, {
        archiveToken: 'archive-id',
        directory: '',
        downloadToken: 'short token',
        format: 'tgz',
      }),
    ).toBe(
      '/user/user%20name/directories/?archiveFormat=tgz&archiveToken=archive-id&downloadHidden=true&followSymlinks=false&token=short%20token',
    );
  });

  it('builds an archive URL for a named directory', () => {
    const url = archiveDownloadUrl(SESSION, {
      archiveToken: 'archive-id',
      directory: 'course work/week 1',
      downloadToken: 'short token',
      format: 'zip',
    });

    expect(url).toBe(
      '/user/user%20name/directories/course%20work/week%201?archiveFormat=zip&archiveToken=archive-id&downloadHidden=true&followSymlinks=false&token=short%20token',
    );
  });

  it('builds an archive URL when randomUUID is unavailable on LAN HTTP', () => {
    vi.stubGlobal('crypto', {
      getRandomValues: (bytes: Uint8Array) => {
        bytes.set([
          0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88, 0x99, 0xaa,
          0xbb, 0xcc, 0xdd, 0xee, 0xff,
        ]);
        return bytes;
      },
    });

    const url = archiveDownloadUrl(SESSION, {
      directory: '',
      downloadToken: 'short',
      format: 'zip',
    });

    expect(url).toContain('archiveToken=00112233-4455-4677-8899-aabbccddeeff');
  });
});
