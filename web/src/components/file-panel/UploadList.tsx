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
        className="block w-full min-w-0 text-right text-xs text-destructive"
        title={message}
      >
        {message}
      </span>
    );
  }

  if (upload.state === 'done') {
    return (
      <span className="block w-full min-w-0 text-right text-xs text-muted-foreground">
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
            aria-label={t.files.uploadClear}
            className="inline-flex size-6 shrink-0 cursor-pointer items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={onClear}
            title={t.files.uploadClear}
            type="button"
          >
            <X
              aria-hidden="true"
              className="size-3.5"
            />
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
              className="max-w-[40%] min-w-0 shrink truncate text-xs"
              title={upload.name}
            >
              {upload.name}
            </span>
            <span className="flex min-w-0 flex-1 justify-end text-right">
              <UploadStatus upload={upload} />
            </span>
            {isPending(upload) ? (
              <button
                aria-label={`${t.files.uploadCancel}: ${upload.name}`}
                className="inline-flex size-6 shrink-0 cursor-pointer items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
            ) : (
              <span
                aria-hidden="true"
                className="inline-flex size-6 shrink-0 items-center justify-center"
              >
                {upload.state === 'done' ? (
                  <Check className="size-3.5 text-primary" />
                ) : (
                  <X className="size-3.5 text-destructive" />
                )}
              </span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
};
