import * as DialogPrimitive from '@radix-ui/react-dialog';
import { type ComponentProps } from 'react';

import { cn } from '@/lib/utils';

// Radix rather than a hand-rolled div, for the parts that are tedious to get
// right and invisible when they are wrong: focus is trapped while the dialog is
// open and restored to wherever it came from when it closes, the background is
// made inert, Escape and outside clicks arrive as props rather than as document
// listeners, and the title is wired to the dialog for a screen reader.
//
// modal={false} deliberately. A modal dialog makes everything outside it
// inert, which took the header with it — the language toggle in particular,
// which is the one control a user staring at a prompt they cannot read
// actually needs. What is kept: focus moves into the dialog on open and is
// handed back on close, Escape and outside clicks arrive as props, the title is
// wired for a screen reader. What is given up: the focus trap, so Tab can
// reach the header and the terminal behind. That is the point.
//
// Positioned absolutely rather than fixed, and portalled into the element the
// caller names rather than into `document.body`. These overlays cover the
// terminal and stop at the header — a fixed overlay covers the header too,
// which reads as the whole page having been replaced rather than the session
// being interrupted.
// Wrapped rather than re-exported: a bare `export const X = Primitive.Root`
// is not recognisable as a component, and the file then exports things that
// are not components, which breaks fast refresh for everything in it.
export const Dialog = (props: ComponentProps<typeof DialogPrimitive.Root>) => (
  <DialogPrimitive.Root {...props} />
);

export const DialogPortal = (
  props: ComponentProps<typeof DialogPrimitive.Portal>,
) => <DialogPrimitive.Portal {...props} />;

// A plain element, not `Dialog.Overlay`. Radix renders its overlay only for a
// modal dialog and nothing at all otherwise — so going non-modal above silently
// took the backdrop away from all four of these, which is a thing you find out
// by looking at the page rather than at the code.
//
// It costs the exit animation, which Radix drives from the state attribute it
// would have put here. Entering is the half anyone sees.
//
// Kept below the content by z-index rather than by document order, which is
// not ours to rely on: `DialogPortal` gives every child its own portal, so an
// overlay that mounts later than the content — the challenge one does, since it
// appears only if a check turns interactive — is appended after it. It then
// paints its blur over the dialog and, worse, takes the clicks meant for it.
export const DialogOverlay = ({
  className,
  ...props
}: ComponentProps<'div'>) => (
  <div
    className={cn(
      // A neutral scrim rather than the page background. What is behind these
      // is always the terminal, which is always dark — tinting the blur with a
      // light page turns it into a flat grey slab that reads as a broken region
      // rather than a dimmed one.
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
      // Centred by the grid on the overlay's own box, so the card keeps its
      // corners on a narrow screen instead of running off both edges.
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
