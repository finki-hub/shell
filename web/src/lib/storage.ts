import { type StorageUsage } from '@/lib/storage-api';

// Bytes and inodes are capped independently, and coursework hits the inode
// limit first: unpacking a source tree is thousands of tiny files against a
// byte quota it never comes close to. Showing the larger of the two is the only
// reading that stays honest in both directions.
export const storagePercent = (usage: StorageUsage) => {
  const bytes = usage.totalBytes === 0 ? 0 : usage.usedBytes / usage.totalBytes;
  const inodes =
    usage.inodesTotal === 0 ? 0 : usage.inodesUsed / usage.inodesTotal;

  return Math.min(100, Math.round(Math.max(bytes, inodes) * 100));
};

// [measured] The exact test was wrong, and wrong in the direction that shows
// the user nothing. XFS refuses a write before the counter reaches the hard
// limit: a 50MB quota filled until `dd` failed with EDQUOT reported 51,589,120
// of 52,428,800 bytes — 98.4%, never equal, so the banner explaining what to do
// never appeared while the shell was already saying "No space left on device".
//
// A threshold is therefore not a fudge, it is the only honest test available
// from a counter. Erring early costs a user a suggestion to delete something
// while they still have a megabyte or two; erring late costs them an
// unexplained failure, which is what shipped.
const FULL_PERCENT = 95;

export const isStorageFull = (usage: StorageUsage) =>
  storagePercent(usage) >= FULL_PERCENT;

const MB = 1_024 * 1_024;

// One unit, stated once, with the same number of decimals on both sides so the
// pair does not jiggle as it fills. A bar says how close; this says how much,
// which is the number somebody deciding what to delete actually needs.
export const formatUsage = (usage: StorageUsage) => {
  const total = usage.totalBytes / MB;
  const used = usage.usedBytes / MB;
  const decimals = total < 10 ? 1 : 0;

  return `${used.toFixed(decimals)} / ${total.toFixed(decimals)} MB`;
};

// One size, on its own, for a file listing and for the storage summary's
// tooltip: the unit follows the magnitude here, because a home directory holds
// both a 200-byte dotfile and a 40 MB archive and neither reads well in the
// other's unit. Moved here unchanged from the deleted `lib/transfer.ts`.
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
