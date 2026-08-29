import { useLanguage } from '@/hooks/useLanguage';
import { formatBytes, formatUsage, isStorageFull } from '@/lib/storage';
import { type StorageUsage } from '@/lib/storage-api';
import { cn } from '@/lib/utils';

// A sentence, not a second bar. The header badge already answers "how close am
// I" at a glance; what belongs at the foot of a file list is the number
// somebody deciding what to delete needs, including the inode count that the
// badge keeps on a tooltip and that a source tree hits first.
export const StorageSummary = ({ usage }: { readonly usage: StorageUsage }) => {
  const { t } = useLanguage();
  const full = isStorageFull(usage);

  return (
    <span
      className={cn(
        'min-w-0 truncate text-xs tabular-nums',
        full ? 'font-medium text-destructive' : 'text-muted-foreground',
      )}
      title={`${t.storage.title}: ${formatBytes(usage.usedBytes)} / ${formatBytes(usage.totalBytes)} · ${usage.inodesUsed} / ${usage.inodesTotal} ${t.storage.files}`}
    >
      {formatUsage(usage)} · {usage.inodesUsed} / {usage.inodesTotal}{' '}
      {t.storage.files}
    </span>
  );
};
