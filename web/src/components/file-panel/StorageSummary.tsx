import { useLanguage } from '@/hooks/useLanguage';
import { formatBytes, formatUsage, isStorageFull } from '@/lib/storage';
import { type StorageUsage } from '@/lib/storage-api';
import { cn } from '@/lib/utils';

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
