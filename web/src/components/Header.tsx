import { Bell, BellOff, Plus, Upload } from 'lucide-react';
import { useRef } from 'react';
import { siGithub } from 'simple-icons';

import { DownloadMenu } from '@/components/header/DownloadMenu';
import { LanguageToggle } from '@/components/header/LanguageToggle';
import { StorageBadge } from '@/components/header/StorageBadge';
import { ThemeToggle } from '@/components/ThemeToggle';
import { IconButton, IconLink } from '@/components/ui/icon-controls';
import { setChimeMuted, useChimeMuted } from '@/hooks/useChime';
import { type SessionStatus } from '@/hooks/useLabSession';
import { useLanguage } from '@/hooks/useLanguage';
import { GITHUB_URL } from '@/lib/constants';
import { type ArchiveFormat, type StorageUsage } from '@/lib/protocol';
import { cn } from '@/lib/utils';

type HeaderProps = {
  readonly expiresAt: null | number;
  readonly onDownload: (format: ArchiveFormat) => void;
  readonly onNewEnvironment: () => void;
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

// A wall-clock time, never a countdown. The deadline is days away and fixed at
// creation, so a ticking timer would be both wrong in feel and useless in
// practice — what a user needs is a date they can plan around.
const FilesKeptUntil = ({ expiresAt }: { readonly expiresAt: number }) => {
  const { language, t } = useLanguage();
  // eslint-disable-next-line unicorn/prefer-temporal -- Temporal is not available in every browser this ships to
  const deadline = new Date(expiresAt);
  const when = deadline.toLocaleString(language === 'mk' ? 'mk-MK' : 'en-GB', {
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    month: 'short',
  });

  return (
    <span
      className="inline-flex h-9 items-center gap-1.5 rounded-md border border-input bg-background px-3 text-xs text-muted-foreground"
      title={deadline.toLocaleString()}
    >
      {t.session.filesKept}
      <span className="font-medium tabular-nums text-foreground">{when}</span>
    </span>
  );
};

// Beside the theme preference, because it is the same kind of thing: a
// standing choice about how the page behaves, not a session setting.
const ChimeToggle = () => {
  const muted = useChimeMuted();
  const { t } = useLanguage();

  return (
    <IconButton
      aria-label={muted ? t.session.challengeUnmute : t.session.challengeMute}
      onClick={() => {
        setChimeMuted(!muted);
      }}
      title={muted ? t.session.challengeUnmute : t.session.challengeMute}
    >
      {muted ? (
        <BellOff
          aria-hidden="true"
          className="h-4 w-4"
        />
      ) : (
        <Bell
          aria-hidden="true"
          className="h-4 w-4"
        />
      )}
    </IconButton>
  );
};

const STATUS_DOT: Record<SessionStatus, string> = {
  connecting: 'animate-pulse bg-amber-500',
  ended: 'bg-destructive',
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
  expiresAt,
  onDownload,
  onNewEnvironment,
  onUpload,
  status,
  storage,
}: HeaderProps) => {
  const { language, setLanguage, t } = useLanguage();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const running = status === 'running';

  return (
    <header className="border-b">
      {/* Wrapping, because the row cannot fit on a phone and the alternative
          was hiding the deadline — which the user is told they cannot
          extend, so they have to be able to see it. */}
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
          {expiresAt !== null && running && (
            <FilesKeptUntil expiresAt={expiresAt} />
          )}
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
            aria-label={t.actions.upload}
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
          <ChimeToggle />
          <LanguageToggle
            language={language}
            setLanguage={setLanguage}
          />
          {/* Hidden on a phone. Everything else in this row is part of doing
              the work; a link to the source is not, and at 320px it was the
              difference between the header taking three rows and four — half
              the screen, above a terminal that is the actual product. */}
          <span className="hidden sm:inline-flex">
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
