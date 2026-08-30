import { useId, useState } from 'react';

import {
  Dialog,
  DialogContent,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
} from '@/components/ui/dialog';
import { useLanguage } from '@/hooks/useLanguage';

type NameDialogProps = {
  readonly confirmLabel: string;
  readonly container: HTMLElement | null;
  readonly initialName: string;
  readonly onCancel: () => void;
  readonly onSubmit: (name: string) => void;
  readonly title: string;
};

export const NameDialog = ({
  confirmLabel,
  container,
  initialName,
  onCancel,
  onSubmit,
  title,
}: NameDialogProps) => {
  const { t } = useLanguage();
  const inputId = useId();
  const [name, setName] = useState(initialName);
  const trimmed = name.trim();
  const valid = trimmed !== '' && !trimmed.includes('/');

  return (
    <Dialog
      modal={false}
      onOpenChange={(open) => {
        if (!open) {
          onCancel();
        }
      }}
      open
    >
      <DialogPortal container={container}>
        <DialogOverlay className="z-30" />
        <DialogContent className="z-30 items-stretch text-left">
          <DialogTitle>{title}</DialogTitle>
          <form
            className="flex flex-col gap-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (valid) {
                onSubmit(trimmed);
              }
            }}
          >
            <div className="flex flex-col gap-1.5 text-sm">
              <label
                className="text-muted-foreground"
                htmlFor={inputId}
              >
                {t.files.name}
              </label>
              {/* Radix handles initial focus; avoid competing autoFocus. */}
              <input
                aria-label={t.files.name}
                className="h-10 rounded-md border border-input bg-background px-3 text-sm ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                id={inputId}
                onChange={(event) => {
                  setName(event.target.value);
                }}
                spellCheck={false}
                value={name}
              />
            </div>
            {trimmed !== '' && !valid && (
              <p className="text-xs text-destructive">{t.files.invalidName}</p>
            )}
            <div className="flex gap-2">
              <button
                className="inline-flex h-10 flex-1 cursor-pointer items-center justify-center rounded-md border border-input bg-background px-4 text-sm font-medium transition-colors hover:bg-accent hover:text-accent-foreground"
                onClick={onCancel}
                type="button"
              >
                {t.actions.cancel}
              </button>
              <button
                className="inline-flex h-10 flex-1 cursor-pointer items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50"
                disabled={!valid}
                type="submit"
              >
                {confirmLabel}
              </button>
            </div>
          </form>
        </DialogContent>
      </DialogPortal>
    </Dialog>
  );
};
