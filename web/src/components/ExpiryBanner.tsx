import { CalendarClock } from 'lucide-react';

import { DownloadButton } from '@/components/DownloadButton';
import { Banner } from '@/components/ui/banner';
import { useLanguage } from '@/hooks/useLanguage';
import { type ArchiveFormat } from '@/lib/protocol';

type ExpiryBannerProps = {
  readonly onDismiss: () => void;
  readonly onDownload: (format: ArchiveFormat) => void;
  // Which warning this is. The last one before the session ends has to look
  // different from the one hours earlier, or a user who has been connected
  // all along sees nothing change and is told nothing new.
  readonly thresholdMs: number;
};

// Non-modal and inline, with the action attached. A modal would interrupt work
// to deliver news the user cannot act on in the moment. The date is not
// repeated here — it is in the header, a few centimetres away, and has been
// since the session started.
//
// Dismissed for as long as the page lives and no longer: remembering it across
// refreshes would mean somebody who waved away the first warning never sees
// the last.
const LAST_CALL_MS = 60 * 60_000;

export const ExpiryBanner = ({
  onDismiss,
  onDownload,
  thresholdMs,
}: ExpiryBannerProps) => {
  const { t } = useLanguage();

  return (
    <Banner
      action={
        <DownloadButton
          label={t.session.expiredDownload}
          onDownload={onDownload}
        />
      }
      icon={CalendarClock}
      onDismiss={onDismiss}
      tone={thresholdMs <= LAST_CALL_MS ? 'urgent' : 'warning'}
    >
      {t.session.expiryWarning}
    </Banner>
  );
};
