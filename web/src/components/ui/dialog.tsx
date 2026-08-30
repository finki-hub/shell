import * as DialogPrimitive from '@radix-ui/react-dialog';
import { type ComponentProps } from 'react';

import { cn } from '@/lib/utils';

export const Dialog = (props: ComponentProps<typeof DialogPrimitive.Root>) => (
  <DialogPrimitive.Root {...props} />
);

export const DialogPortal = (
  props: ComponentProps<typeof DialogPrimitive.Portal>,
) => <DialogPrimitive.Portal {...props} />;

export const DialogOverlay = ({
  className,
  ...props
}: ComponentProps<'div'>) => (
  <div
    className={cn(
      'absolute inset-0 z-20 animate-in bg-neutral-950/60 backdrop-blur-sm fade-in-0',
      className,
    )}
    {...props}
  />
);

export const DialogContent = ({
  children,
  className,
  ...props
}: ComponentProps<typeof DialogPrimitive.Content>) => (
  <DialogPrimitive.Content
    className={cn(
      'absolute top-1/2 left-1/2 z-30 flex w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2',
      'flex-col items-center gap-4 rounded-xl border bg-card p-6 text-center shadow-sm',
      'data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95',
      'data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95',
      className,
    )}
    {...props}
  >
    {children}
  </DialogPrimitive.Content>
);

export const DialogTitle = ({
  className,
  ...props
}: ComponentProps<typeof DialogPrimitive.Title>) => (
  <DialogPrimitive.Title
    className={cn(
      'text-lg font-semibold tracking-tight text-card-foreground',
      className,
    )}
    {...props}
  />
);

export const DialogDescription = ({
  className,
  ...props
}: ComponentProps<typeof DialogPrimitive.Description>) => (
  <DialogPrimitive.Description
    className={cn('text-sm text-muted-foreground', className)}
    {...props}
  />
);
