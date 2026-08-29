import { CalendarClock } from 'lucide-react';
import { useEffect, useRef } from 'react';

import type { DeploymentWarningPresentation } from '@/lib/redeployment-warning';

import { Banner } from '@/components/ui/banner';
import {
  type AttentionLease,
  clearAttention,
  raiseAttention,
} from '@/lib/attention';

type RedeploymentWarningBannerProps = {
  readonly presentation: DeploymentWarningPresentation;
};

export const RedeploymentWarningBanner = ({
  presentation,
}: RedeploymentWarningBannerProps) => {
  const leaseRef = useRef<AttentionLease | null>(null);

  useEffect(() => {
    leaseRef.current = raiseAttention(presentation.titlePrefix, 'deployment');

    return () => {
      const lease = leaseRef.current;
      if (lease !== null) {
        clearAttention(lease);
        leaseRef.current = null;
      }
    };
  }, [presentation.deploymentId, presentation.titlePrefix]);

  return (
    <Banner
      icon={CalendarClock}
      tone="warning"
    >
      {presentation.copy}
    </Banner>
  );
};
