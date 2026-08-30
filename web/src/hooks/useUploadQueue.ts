import { useCallback, useEffect, useRef, useState } from 'react';

import { type ContentsErrorKind, uploadFile } from '@/lib/contents-api';
import { type LabSession } from '@/lib/hub-api';

export type FileUpload = {
  readonly error: ContentsErrorKind | null;
  readonly id: number;
  readonly name: string;
  /** Progress advances once per 1 MiB chunk. */
  readonly progress: number;
  readonly state: UploadState;
};

export type UploadQueue = {
  readonly cancelUpload: (id: number) => void;
  readonly clearFinished: () => void;
  readonly enqueue: (files: readonly File[]) => readonly number[];
  readonly uploading: boolean;
  readonly uploads: readonly FileUpload[];
};

export type UploadState = 'done' | 'failed' | 'running' | 'waiting';

type Job = {
  readonly controller: AbortController;
  readonly directory: string;
  readonly file: File;
  readonly id: number;
};

type UseUploadQueueInput = {
  readonly directory: () => string;
  readonly onSettled: (directory: string, ok: boolean) => void;
  readonly session: LabSession | null;
  readonly setTransferring: (transferring: boolean) => void;
};

const isPending = (upload: FileUpload): boolean =>
  upload.state === 'running' || upload.state === 'waiting';

// Keep uploads serial: the container has a 384 MB memory limit and 8 MiB body cap.
export const useUploadQueue = ({
  directory,
  onSettled,
  session,
  setTransferring,
}: UseUploadQueueInput): UploadQueue => {
  const [uploads, setUploads] = useState<readonly FileUpload[]>([]);

  const idRef = useRef(0);
  const jobsRef = useRef(new Map<number, Job>());
  const pendingRef = useRef<Job[]>([]);
  const runningRef = useRef(false);

  // Cancel queued and in-flight work when the session changes.
  useEffect(() => {
    setUploads([]);
    for (const job of jobsRef.current.values()) {
      job.controller.abort();
    }
    jobsRef.current.clear();
    pendingRef.current = [];
  }, [session]);

  const patch = useCallback((id: number, change: Partial<FileUpload>) => {
    setUploads((current) =>
      current.map((item) => (item.id === id ? { ...item, ...change } : item)),
    );
  }, []);

  const runJob = useCallback(
    async (job: Job) => {
      // Map membership prevents canceled queued jobs from starting.
      if (session === null || !jobsRef.current.has(job.id)) return;
      patch(job.id, { state: 'running' });
      const result = await uploadFile(session, {
        directory: job.directory,
        file: job.file,
        onProgress: (fraction) => {
          patch(job.id, { progress: fraction });
        },
        signal: job.controller.signal,
      });
      jobsRef.current.delete(job.id);
      patch(
        job.id,
        result.ok
          ? { progress: 1, state: 'done' }
          : { error: result.error.kind, state: 'failed' },
      );
      onSettled(job.directory, result.ok);
    },
    [onSettled, patch, session],
  );

  const drain = useCallback(async () => {
    if (runningRef.current) return;
    runningRef.current = true;
    setTransferring(true);
    try {
      let job = pendingRef.current.shift();
      while (job !== undefined) {
        await runJob(job);
        job = pendingRef.current.shift();
      }
    } finally {
      runningRef.current = false;
      setTransferring(false);
    }
  }, [runJob, setTransferring]);

  const enqueue = useCallback(
    (files: readonly File[]) => {
      if (session === null || files.length === 0) return [];
      const target = directory();
      const added = files.map((file) => {
        idRef.current += 1;
        return {
          controller: new AbortController(),
          directory: target,
          file,
          id: idRef.current,
        };
      });
      for (const job of added) {
        jobsRef.current.set(job.id, job);
      }
      pendingRef.current.push(...added);
      setUploads((current) => [
        ...current,
        ...added.map((job): FileUpload => ({
          error: null,
          id: job.id,
          name: job.file.name,
          progress: 0,
          state: 'waiting',
        })),
      ]);
      void drain();
      return added.map((job) => job.id);
    },
    [directory, drain, session],
  );

  const cancelUpload = useCallback(
    (id: number) => {
      const job = jobsRef.current.get(id);
      if (job === undefined) return;
      jobsRef.current.delete(id);
      // Aborting removes the temporary .part file.
      job.controller.abort();
      pendingRef.current = pendingRef.current.filter(
        (queued) => queued.id !== id,
      );
      patch(id, { error: 'aborted', state: 'failed' });
    },
    [patch],
  );

  const clearFinished = useCallback(() => {
    setUploads((current) => current.filter(isPending));
  }, []);

  return {
    cancelUpload,
    clearFinished,
    enqueue,
    uploading: uploads.some(isPending),
    uploads,
  };
};
