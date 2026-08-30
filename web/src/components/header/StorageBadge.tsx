import { useLanguage } from '@/hooks/useLanguage';
import {
  formatBytes,
  formatUsage,
  isStorageFull,
  storagePercent,
} from '@/lib/storage';
import { type StorageUsage } from '@/lib/storage-api';
import { cn } from '@/lib/utils';

// Firefox and WebKit use separate progress-fill pseudo-elements.
const fillClass = (percent: number) => {
  if (percent >= 90) {
    return '[&::-moz-progress-bar]:bg-destructive [&::-webkit-progress-value]:bg-destructive';
  }

  return percent >= 75
    ? '[&::-moz-progress-bar]:bg-amber-500 [&::-webkit-progress-value]:bg-amber-500'
    : '[&::-moz-progress-bar]:bg-primary [&::-webkit-progress-value]:bg-primary';
};

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
      {/* Native progress exposes storage usage to assistive technology. */}
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
