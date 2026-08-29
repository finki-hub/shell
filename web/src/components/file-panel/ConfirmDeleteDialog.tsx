import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
} from '@/components/ui/dialog';
import { useLanguage } from '@/hooks/useLanguage';
import { type ContentsEntry } from '@/lib/contents-api';

type ConfirmDeleteDialogProps = {
  readonly container: HTMLElement | null;
  readonly entry: ContentsEntry;
  readonly onCancel: () => void;
  readonly onConfirm: () => void;
};

// Asked, not undone. `delete_to_trash` is off in the container (contract §8),
// so there is no bin to fish anything out of — the confirmation is the whole
// safeguard, and it names the thing it is about to remove.
export const ConfirmDeleteDialog = ({
  container,
  entry,
  onCancel,
  onConfirm,
}: ConfirmDeleteDialogProps) => {
  const { t } = useLanguage();

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
        <DialogContent className="z-30 items-stretch">
          <div className="flex flex-col gap-2 text-center">
            <DialogTitle>{t.files.deleteTitle}</DialogTitle>
            <p className="truncate font-mono text-sm">{entry.name}</p>
            <DialogDescription>{t.files.deleteBody}</DialogDescription>
          </div>
          <div className="flex gap-2">
            <button
              className="inline-flex h-10 flex-1 cursor-pointer items-center justify-center rounded-md border border-input bg-background px-4 text-sm font-medium transition-colors hover:bg-accent hover:text-accent-foreground"
              onClick={onCancel}
              type="button"
            >
              {t.actions.cancel}
            </button>
            <button
              className="inline-flex h-10 flex-1 cursor-pointer items-center justify-center rounded-md bg-destructive px-4 text-sm font-medium text-destructive-foreground transition-colors hover:bg-destructive/90"
              onClick={onConfirm}
              type="button"
            >
              {t.files.deleteConfirm}
            </button>
          </div>
        </DialogContent>
      </DialogPortal>
    </Dialog>
  );
};
