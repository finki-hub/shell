import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

import { UploadProgress } from '@/components/UploadProgress';
import { createUploadGeneration } from '@/hooks/upload-generation';
import { useLanguage } from '@/hooks/useLanguage';
import { formatBytes, uploadFile } from '@/lib/transfer';

export const useUploads = (
  token: null | string,
  onSettled: () => void,
  generation: number,
) => {
  const { t } = useLanguage();
  const pendingRef = useRef<File[]>([]);
  const runningRef = useRef(false);
  const ownerRef = useRef(createUploadGeneration());
  const mountedRef = useRef(false);
  const toastIdsRef = useRef(new Set<number | string>());
  // Surfaced so the page can warn before a refresh takes an upload with it:
  // the request lives in this tab and dies with it, and the user has no
  // other way to know that.
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    const owner = mountedRef.current
      ? createUploadGeneration()
      : ownerRef.current;
    mountedRef.current = true;
    ownerRef.current = owner;
    const toastIds = toastIdsRef.current;
    return () => {
      const wasActive = owner.active();
      owner.end();
      if (!wasActive) {
        return;
      }
      pendingRef.current = [];
      runningRef.current = false;
      for (const id of toastIds) {
        toast.dismiss(id);
      }
      toastIds.clear();
      setUploading(false);
    };
  }, [generation, token]);

  // One at a time, matching the server, which refuses a second concurrent
  // upload for the same session rather than interleaving them.
  const drain = useCallback(async () => {
    if (runningRef.current || token === null || !ownerRef.current.active()) {
      return;
    }

    runningRef.current = true;
    setUploading(true);
    const owner = ownerRef.current;

    try {
      for (;;) {
        const file = pendingRef.current.shift();

        if (file === undefined) {
          break;
        }

        const id = toast.loading(file.name, {
          description: <UploadProgress value={0} />,
        });
        toastIdsRef.current.add(id);
        const handle = uploadFile(token, file, (fraction) => {
          owner.run(() => {
            toast.loading(file.name, {
              description: <UploadProgress value={fraction} />,
              id,
            });
          });
        });
        owner.retain(handle);
        const result = await handle.done;
        owner.release(handle);

        if (!owner.active()) {
          return;
        }
        toastIdsRef.current.delete(id);

        if (result.ok) {
          toast.success(file.name, { description: t.upload.done, id });
        } else {
          // The limit is worth saying out loud: "too large" on its own leaves
          // the user guessing at a number the server already knows.
          const limit =
            result.limit === null ? '' : ` (${formatBytes(result.limit)})`;
          toast.error(file.name, {
            description: `${t.upload.errors[result.reason]}${limit}`,
            id,
          });
        }

        onSettled();
      }
    } finally {
      owner.run(() => {
        runningRef.current = false;
        setUploading(false);
      });
    }
  }, [onSettled, t, token]);

  const enqueue = useCallback(
    (files: readonly File[]) => {
      if (token === null || files.length === 0) {
        return;
      }

      pendingRef.current.push(...files);
      void drain();
    },
    [drain, token],
  );

  return { enqueue, uploading };
};
