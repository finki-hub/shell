import { Check, X } from 'lucide-react';

import { UploadProgress } from '@/components/UploadProgress';
import { useLanguage } from '@/hooks/useLanguage';
import { type FileUpload } from '@/hooks/useUploadQueue';

type UploadListProps = {
  readonly onCancel: (id: number) => void;
  readonly onClear: () => void;
  readonly uploads: readonly FileUpload[];
};

const isPending = (upload: FileUpload): boolean =>
  upload.state === 'running' || upload.state === 'waiting';

const UploadStatus = ({ upload }: { readonly upload: FileUpload }) => {
  const { t } = useLanguage();

  if (upload.state === 'failed') {
    const message =
      upload.error === null
        ? t.files.errors.unavailable
        : t.files.errors[upload.error];

    return (
      <span
        className="truncate text-xs text-destructive"
        title={message}
      >
        {message}
      </span>
    );
  }

  if (upload.state === 'done') {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
        <Check
          aria-hidden="true"
          className="size-3.5 text-primary"
        />
        {t.files.uploaded}
      </span>
    );
  }

  return <UploadProgress value={upload.progress} />;
};

// Beside the list rather than in a toast stack: a queue of eight files is
// eight toasts covering the terminal, and the one thing somebody wants from a
// queue — which file is stuck, and can I stop it — is exactly what a stack of
// disappearing notifications cannot answer.
export const UploadList = ({ onCancel, onClear, uploads }: UploadListProps) => {
  const { t } = useLanguage();

  if (uploads.length === 0) {
    return null;
  }

  return (
    <section
      aria-label={t.files.uploads}
      className="border-t"
    >
      <div className="flex items-center gap-2 px-3 pt-2">
        <h3 className="mr-auto text-xs font-medium text-muted-foreground">
          {t.files.uploads}
        </h3>
        {uploads.some((upload) => !isPending(upload)) && (
          <button
            className="cursor-pointer rounded-sm px-1.5 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={onClear}
            type="button"
          >
            {t.files.uploadClear}
          </button>
        )}
      </div>
      <ul className="max-h-32 overflow-y-auto px-3 py-1">
        {uploads.map((upload) => (
          <li
            className="flex items-center gap-2 py-0.5"
            key={upload.id}
          >
            <span
              className="min-w-0 flex-1 truncate text-xs"
              title={upload.name}
            >
              {upload.name}
            </span>
            <span className="flex w-32 shrink-0 justify-end">
              <UploadStatus upload={upload} />
            </span>
            <button
              aria-label={`${t.files.uploadCancel}: ${upload.name}`}
              className="inline-flex size-6 shrink-0 cursor-pointer items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:invisible"
              disabled={!isPending(upload)}
              onClick={() => {
                onCancel(upload.id);
              }}
              title={t.files.uploadCancel}
              type="button"
            >
              <X
                aria-hidden="true"
                className="size-3.5"
              />
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
};
