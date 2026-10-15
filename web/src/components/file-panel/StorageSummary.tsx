import { useLanguage } from '@/hooks/useLanguage';
import { formatNumber } from '@/lib/i18n';
import { formatBytes, formatUsage, isStorageFull } from '@/lib/storage';
import { type StorageUsage } from '@/lib/storage-api';
import { cn } from '@/lib/utils';

export const StorageSummary = ({ usage }: { readonly usage: StorageUsage }) => {
  const { language, t } = useLanguage();
  const full = isStorageFull(usage);

  return (
    <span
      className={cn(
        'min-w-0 truncate text-xs tabular-nums',
        full ? 'font-medium text-destructive' : 'text-muted-foreground',
      )}
      title={`${t.storage.title}: ${formatBytes(usage.usedBytes, language)} / ${formatBytes(usage.totalBytes, language)} · ${formatNumber(usage.inodesUsed, language)} / ${formatNumber(usage.inodesTotal, language)} ${t.storage.files}`}
    >
      {formatUsage(usage, language)} ·{' '}
      {formatNumber(usage.inodesUsed, language)} /{' '}
      {formatNumber(usage.inodesTotal, language)} {t.storage.files}
    </span>
  );
};
