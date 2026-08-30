import { describe, expect, it } from 'vitest';

import { isStorageFull, storagePercent } from '@/lib/storage';

const usage = (partial: Partial<Parameters<typeof storagePercent>[0]>) => ({
  inodesTotal: 1_000,
  inodesUsed: 0,
  totalBytes: 1_000,
  usedBytes: 0,
  ...partial,
});

describe('reading storage', () => {
  it('reports whichever limit is closer', () => {
    // Inode exhaustion can stop writes while byte usage still appears low.
    expect(storagePercent(usage({ inodesUsed: 900, usedBytes: 100 }))).toBe(90);
    expect(storagePercent(usage({ inodesUsed: 100, usedBytes: 900 }))).toBe(90);
  });

  it('never exceeds a hundred percent', () => {
    expect(storagePercent(usage({ usedBytes: 5_000 }))).toBe(100);
  });

  it('survives a limit of zero rather than reporting NaN', () => {
    expect(storagePercent(usage({ inodesTotal: 0, totalBytes: 0 }))).toBe(0);
  });

  it('calls it full on either limit', () => {
    expect(isStorageFull(usage({ usedBytes: 1_000 }))).toBe(true);
    expect(isStorageFull(usage({ inodesUsed: 1_000 }))).toBe(true);
    expect(isStorageFull(usage({ inodesUsed: 500, usedBytes: 500 }))).toBe(
      false,
    );
  });

  it('calls it full while the counter still shows room', () => {
    // [measured] A 50MB quota refused writes at 98.4%, before reaching 100%.
    expect(
      isStorageFull({
        inodesTotal: 20_000,
        inodesUsed: 6,
        totalBytes: 52_428_800,
        usedBytes: 51_589_120,
      }),
    ).toBe(true);
  });
});
