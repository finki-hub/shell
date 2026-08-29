import { useLanguage } from '@/hooks/useLanguage';
import { type StorageUsage } from '@/lib/protocol';
import { formatUsage, isStorageFull, storagePercent } from '@/lib/storage';
import { formatBytes } from '@/lib/transfer';
import { cn } from '@/lib/utils';

// Both vendor pseudo-elements have to be painted: Firefox fills
// ::-moz-progress-bar, WebKit fills ::-webkit-progress-value, and neither
// inherits from the other.
const fillClass = (percent: number) => {
  if (percent >= 90) {
    return '[&::-moz-progress-bar]:bg-destructive [&::-webkit-progress-value]:bg-destructive';
  }

  return percent >= 75
    ? '[&::-moz-progress-bar]:bg-amber-500 [&::-webkit-progress-value]:bg-amber-500'
    : '[&::-moz-progress-bar]:bg-primary [&::-webkit-progress-value]:bg-primary';
};

// A bar for "how close am I", which a length answers at a glance, and the two
// numbers beside it for "how much", which is what somebody deciding what to
// delete actually needs. The inode count stays on the tooltip: it is the limit
// that bites second, and only for people unpacking a source tree.
export const StorageBadge = ({ usage }: { readonly usage: StorageUsage }) => {
  const { t } = useLanguage();
  const percent = storagePercent(usage);
  const full = isStorageFull(usage);

  return (
    <span
      className={cn(
        'h-9 items-center gap-2 rounded-md border px-3 text-xs sm:inline-flex',
        full
          ? 'inline-flex border-destructive text-destructive'
          : 'hidden border-input bg-background text-muted-foreground',
      )}
      title={`${t.storage.title}: ${formatBytes(usage.usedBytes)} / ${formatBytes(usage.totalBytes)} · ${usage.inodesUsed} / ${usage.inodesTotal} ${t.storage.files}`}
    >
      {/* A real <progress>, so assistive technology reads it as one rather
          than as a div wearing a role. The floor keeps a nearly-empty bar
          visible: one that renders as nothing reads as broken rather than as
          almost empty. */}
      <progress
        aria-label={t.storage.title}
        className={cn(
          'h-1.5 w-16 appearance-none overflow-hidden rounded-full',
          'bg-muted [&::-webkit-progress-bar]:bg-muted',
          '[&::-moz-progress-bar]:rounded-full [&::-webkit-progress-value]:rounded-full',
          fillClass(percent),
        )}
        max={100}
        value={Math.max(percent > 0 ? 4 : 0, percent)}
      />
      <span className="font-mono tabular-nums">{formatUsage(usage)}</span>
    </span>
  );
};
