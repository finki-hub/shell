import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { RedeploymentWarningBanner } from '@/components/RedeploymentWarningBanner';
import { LanguageContext } from '@/hooks/LanguageContext';
import { clearAttention, raiseAttention, setBaseTitle } from '@/lib/attention';
import { translations } from '@/lib/i18n';
import { dispatchServerText } from '@/lib/lab-session-protocol';
import {
  createDeploymentWarningPresentation,
  reduceDeploymentWarning,
} from '@/lib/redeployment-warning';

const deadlineAt = 1_700_000_300_000;
const deploymentId = 'deployment-1';
const secondDeploymentId = 'deployment-2';
const baseTitle = 'FINKI Hub / Shell';
const warningDurationMs = 300_000;

describe('redeployment warning presentation', () => {
  beforeEach(() => {
    vi.stubGlobal('document', { title: baseTitle });
    vi.stubGlobal('localStorage', { getItem: () => null });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('freezes exact English and Macedonian copy and the time formatter contract', () => {
    const format = vi.fn(() => '22:18');
    const calls: unknown[][] = [];
    const dateTimeFormat = function (locale?: string, options?: unknown) {
      calls.push([locale, options]);
      return locale === undefined
        ? { resolvedOptions: () => ({ timeZone: 'Europe/Skopje' }) }
        : { format };
    };
    vi.stubGlobal('Intl', {
      DateTimeFormat: dateTimeFormat,
    });

    const english = createDeploymentWarningPresentation({
      deadlineAt,
      deploymentId,
      language: 'en',
      startedAt: deadlineAt - warningDurationMs,
    });
    const macedonian = createDeploymentWarningPresentation({
      deadlineAt,
      deploymentId: secondDeploymentId,
      language: 'mk',
      startedAt: deadlineAt - warningDurationMs,
    });

    expect(english.copy).toBe(
      'Server update scheduled for 22:18. Save your work now.',
    );
    expect(english.titlePrefix).toBe('Server update');
    expect(macedonian.copy).toBe(
      'Ажурирањето на серверот е закажано за 22:18. Зачувајте ја работата сега.',
    );
    expect(macedonian.titlePrefix).toBe('Ажурирање на серверот');
    expect(calls).toContainEqual([
      'en-GB',
      {
        hour: '2-digit',
        hour12: false,
        minute: '2-digit',
        timeZone: 'Europe/Skopje',
      },
    ]);
    expect(calls).toContainEqual([
      'mk-MK',
      {
        hour: '2-digit',
        hour12: false,
        minute: '2-digit',
        timeZone: 'Europe/Skopje',
      },
    ]);
    expect(format).toHaveBeenCalledTimes(2);
  });

  it('renders a non-dismissible shared warning banner', () => {
    const markup = renderToStaticMarkup(
      <LanguageContext
        value={{
          language: 'en',
          setLanguage: () => {},
          t: translations.en,
        }}
      >
        <RedeploymentWarningBanner
          presentation={{
            copy: 'Server update scheduled for 22:18. Save your work now.',
            deploymentId,
            titlePrefix: 'Server update',
          }}
        />
      </LanguageContext>,
    );

    expect(markup).toContain(
      'Server update scheduled for 22:18. Save your work now.',
    );
    expect(markup).not.toContain('aria-label="Dismiss"');
    expect(markup).not.toContain('<button');
  });

  it('accepts only the current incident and ignores stale clear and replay', () => {
    const first = reduceDeploymentWarning(null, {
      deadlineAt,
      deploymentId: secondDeploymentId,
      startedAt: deadlineAt - warningDurationMs,
      type: 'deployment-warning',
    });
    const stale = reduceDeploymentWarning(first, {
      deadlineAt: deadlineAt - 1,
      deploymentId,
      startedAt: deadlineAt - warningDurationMs - 1,
      type: 'deployment-warning',
    });
    const staleClear = reduceDeploymentWarning(stale, {
      deploymentId,
      type: 'deployment-warning-cleared',
    });
    const cleared = reduceDeploymentWarning(staleClear, {
      deploymentId: secondDeploymentId,
      type: 'deployment-warning-cleared',
    });
    const replay = reduceDeploymentWarning(cleared, {
      deadlineAt,
      deploymentId: secondDeploymentId,
      startedAt: deadlineAt - warningDurationMs,
      type: 'deployment-warning',
    });

    expect(stale.active?.deploymentId).toBe(secondDeploymentId);
    expect(staleClear.active?.deploymentId).toBe(secondDeploymentId);
    expect(cleared.active).toBeNull();
    expect(replay.active).toBeNull();
  });

  it('keeps deployment attention above a challenge and restores challenge on clear', () => {
    setBaseTitle(baseTitle);
    const challenge = raiseAttention('Verification');
    const deployment = raiseAttention('Server update', 'deployment');

    expect(document.title).toBe(`Server update - ${baseTitle}`);
    clearAttention(deployment);
    expect(document.title).toBe(`Verification - ${baseTitle}`);
    clearAttention(challenge);
    expect(document.title).toBe(baseTitle);
  });

  it('dispatches typed live deployment frames and rejects malformed frames at the protocol boundary', () => {
    const trace: string[] = [];
    const callbacks = {
      onArchiveGrant: () => {},
      onChallenge: () => {},
      onChallengeCleared: () => {},
      onDeploymentWarning: () => {
        trace.push('warning');
      },
      onDeploymentWarningAlert: () => {
        trace.push('alert');
      },
      onDeploymentWarningCleared: () => {
        trace.push('cleared');
      },
      onEnd: () => {},
      onEnvironment: () => {},
      onEnvironmentExpired: () => {},
      onEnvironmentToken: () => {},
      onExpiresAt: () => {},
      onExpiryWarning: () => {},
      onReady: () => {},
      onStatus: () => {},
      onStorage: () => {},
      onToken: () => {},
    };

    const warning = dispatchServerText(
      JSON.stringify({
        deadlineAt,
        deploymentId,
        startedAt: deadlineAt - warningDurationMs,
        type: 'deployment-warning',
      }),
      callbacks,
      Date.now,
    );
    const alert = dispatchServerText(
      JSON.stringify({
        deploymentId,
        phase: 'start',
        type: 'deployment-warning-alert',
      }),
      callbacks,
      Date.now,
    );
    const clear = dispatchServerText(
      JSON.stringify({
        deploymentId,
        type: 'deployment-warning-cleared',
      }),
      callbacks,
      Date.now,
    );
    const malformed = dispatchServerText(
      JSON.stringify({
        deploymentId,
        phase: 'later',
        type: 'deployment-warning-alert',
      }),
      callbacks,
      Date.now,
    );

    expect([warning, alert, clear]).toEqual([null, null, null]);
    expect(malformed).toEqual({
      code: 1_002,
      reason: 'invalid server message',
    });
    expect(trace).toEqual(['warning', 'alert', 'cleared']);
  });
});
