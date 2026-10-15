import { formatNumber, type Language, languageLocales } from '@/lib/i18n';
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

export const formatUsage = (usage: StorageUsage, language: Language) => {
  const total = usage.totalBytes / MB;
  const used = usage.usedBytes / MB;
  const decimals = total < 10 ? 1 : 0;

  const formatter = new Intl.NumberFormat(languageLocales[language], {
    maximumFractionDigits: decimals,
    minimumFractionDigits: decimals,
  });

  return `${formatter.format(used)} / ${formatter.format(total)} MB`;
};

export const formatBytes = (bytes: number, language: Language) => {
  if (bytes < 1_024) {
    return `${formatNumber(bytes, language)} B`;
  }

  const megabytes = bytes / (1_024 * 1_024);

  if (megabytes < 1) {
    return `${formatNumber(Math.round(bytes / 1_024), language)} KB`;
  }

  const decimals = megabytes < 10 ? 1 : 0;

  const formatted = new Intl.NumberFormat(languageLocales[language], {
    maximumFractionDigits: decimals,
    minimumFractionDigits: decimals,
  }).format(megabytes);

  return `${formatted} MB`;
};
