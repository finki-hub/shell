import { type StorageUsage } from '@/lib/storage-api';

export const storagePercent = (usage: StorageUsage) => {
  const bytes = usage.totalBytes === 0 ? 0 : usage.usedBytes / usage.totalBytes;
  const inodes =
    usage.inodesTotal === 0 ? 0 : usage.inodesUsed / usage.inodesTotal;

  return Math.min(100, Math.round(Math.max(bytes, inodes) * 100));
};

// XFS can reject writes before counters reach the hard limit; 95% surfaces the
// warning before EDQUOT.
const FULL_PERCENT = 95;

export const isStorageFull = (usage: StorageUsage) =>
  storagePercent(usage) >= FULL_PERCENT;

const MB = 1_024 * 1_024;

export const formatUsage = (usage: StorageUsage) => {
  const total = usage.totalBytes / MB;
  const used = usage.usedBytes / MB;
  const decimals = total < 10 ? 1 : 0;

  return `${used.toFixed(decimals)} / ${total.toFixed(decimals)} MB`;
};

export const formatBytes = (bytes: number) => {
  if (bytes < 1_024) {
    return `${bytes} B`;
  }

  const megabytes = bytes / (1_024 * 1_024);

  if (megabytes < 1) {
    return `${Math.round(bytes / 1_024)} KB`;
  }

  const decimals = megabytes < 10 ? 1 : 0;

  return `${megabytes.toFixed(decimals)} MB`;
};
