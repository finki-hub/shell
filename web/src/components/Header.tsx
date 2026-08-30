import { Folder, FolderOpen, Plus, Upload } from 'lucide-react';
import { useRef } from 'react';
import { siGithub } from 'simple-icons';

import { DownloadMenu } from '@/components/header/DownloadMenu';
import { LanguageToggle } from '@/components/header/LanguageToggle';
import { StorageBadge } from '@/components/header/StorageBadge';
import { ThemeToggle } from '@/components/ThemeToggle';
import { IconButton, IconLink } from '@/components/ui/icon-controls';
import { type SessionStatus } from '@/hooks/useLabSession';
import { useLanguage } from '@/hooks/useLanguage';
import { GITHUB_URL } from '@/lib/constants';
import { type ArchiveFormat } from '@/lib/contents-api';
import { type StorageUsage } from '@/lib/storage-api';
import { cn } from '@/lib/utils';

type HeaderProps = {
  readonly filesOpen: boolean;
  readonly onDownload: (format: ArchiveFormat) => void;
  readonly onNewEnvironment: () => void;
  readonly onToggleFiles: () => void;
  readonly onUpload: (files: readonly File[]) => void;
  readonly status: SessionStatus;
  readonly storage: null | StorageUsage;
};

const GitHubIcon = () => (
  <svg
    aria-hidden="true"
    className="h-5 w-5"
    fill="currentColor"
    viewBox="0 0 24 24"
    xmlns="http://www.w3.org/2000/svg"
  >
    <path d={siGithub.path} />
  </svg>
);

const STATUS_DOT: Record<SessionStatus, string> = {
  'connecting-terminal': 'animate-pulse bg-amber-500',
  deleting: 'animate-pulse bg-amber-500',
  ended: 'bg-destructive',
  preparing: 'animate-pulse bg-amber-500',
  running: 'bg-primary',
  starting: 'animate-pulse bg-amber-500',
};

const StatusBadge = ({ status }: { readonly status: SessionStatus }) => {
  const { t } = useLanguage();

  return (
    <span className="inline-flex h-9 items-center gap-2 rounded-md border border-input bg-background px-3 text-xs font-medium">
      <span className={cn('size-2 rounded-full', STATUS_DOT[status])} />
      {t.session[status]}
    </span>
  );
};

export const Header = ({
  filesOpen,
  onDownload,
  onNewEnvironment,
  onToggleFiles,
  onUpload,
  status,
  storage,
}: HeaderProps) => {
  const { language, setLanguage, t } = useLanguage();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const running = status === 'running';

  return (
    <header className="border-b">
      <div className="container mx-auto flex min-h-16 flex-wrap items-center gap-3 py-3 lg:h-16 lg:flex-nowrap lg:py-0">
        <img
          alt={t.brand}
          className="h-9 w-9 shrink-0 object-contain sm:h-12 sm:w-12"
          src="/logo.png"
        />
        <h1 className="min-w-0 flex-1 text-base font-bold leading-tight tracking-tight sm:text-xl md:order-last md:basis-full lg:order-none lg:basis-auto">
          {t.brand} / {t.title}
        </h1>
        <div className="ml-auto flex flex-wrap items-center justify-end gap-1.5 lg:flex-nowrap lg:gap-2">
          {storage !== null && running && <StorageBadge usage={storage} />}
          <StatusBadge status={status} />
          <input
            aria-label={t.actions.upload}
            className="hidden"
            multiple
            onChange={(event) => {
              onUpload([...(event.target.files ?? [])]);
              event.target.value = '';
            }}
            ref={fileInputRef}
            type="file"
          />
          <IconButton
            aria-label={t.files.toggle}
            aria-pressed={filesOpen}
            disabled={!running}
            onClick={onToggleFiles}
            title={t.files.toggle}
          >
            {filesOpen ? (
              <FolderOpen
                aria-hidden="true"
                className="size-4"
              />
            ) : (
              <Folder
                aria-hidden="true"
                className="size-4"
              />
            )}
          </IconButton>
          <IconButton
            aria-label={t.actions.upload}
            data-dialog-persistent=""
            disabled={!running}
            onClick={() => {
              fileInputRef.current?.click();
            }}
            title={t.actions.upload}
          >
            <Upload
              aria-hidden="true"
              className="h-4 w-4"
            />
          </IconButton>
          <DownloadMenu
            disabled={!running}
            label={t.actions.downloadHome}
            onDownload={onDownload}
          />
          <IconButton
            aria-label={t.session.newEnvironmentTitle}
            disabled={!running}
            onClick={onNewEnvironment}
            title={t.session.newEnvironmentTitle}
          >
            <Plus
              aria-hidden="true"
              className="h-4 w-4"
            />
          </IconButton>
          <LanguageToggle
            language={language}
            setLanguage={setLanguage}
          />
          <span
            className="hidden sm:inline-flex"
            data-dialog-persistent=""
          >
            <IconLink
              href={GITHUB_URL}
              rel="noopener noreferrer"
              target="_blank"
              title="GitHub"
            >
              <GitHubIcon />
            </IconLink>
          </span>
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
};
