import type { PostHogConfig } from 'posthog-js';

export type BrowserAnalyticsClient = {
  readonly capture: (
    eventName: string,
    properties: { readonly boundary: 'server_message' },
  ) => void;
  readonly init: (key: string, options: BrowserAnalyticsOptions) => void;
};

export type BrowserAnalyticsOptions = Partial<PostHogConfig>;

type BrowserAnalyticsInput = {
  readonly host: unknown;
  readonly key: unknown;
  readonly load: () => Promise<BrowserAnalyticsClient>;
};

const defaultAnalyticsHost = 'https://eu.i.posthog.com';

const postHogOption = {
  advancedDisableDecide: 'advanced_disable_decide',
  advancedDisableFeatureFlags: 'advanced_disable_feature_flags',
  advancedDisableFeatureFlagsOnFirstLoad:
    'advanced_disable_feature_flags_on_first_load',
  advancedDisableFlags: 'advanced_disable_flags',
  advancedDisableToolbarMetrics: 'advanced_disable_toolbar_metrics',
  apiHost: 'api_host',
  beforeSend: 'before_send',
  captureDeadClicks: 'capture_dead_clicks',
  captureExceptions: 'capture_exceptions',
  captureHeatmaps: 'capture_heatmaps',
  capturePageleave: 'capture_pageleave',
  capturePageview: 'capture_pageview',
  capturePerformance: 'capture_performance',
  disableCaptureUrlHashes: 'disable_capture_url_hashes',
  disableConversations: 'disable_conversations',
  disableExternalDependencyLoading: 'disable_external_dependency_loading',
  disablePersistence: 'disable_persistence',
  disableProductTours: 'disable_product_tours',
  disableScrollProperties: 'disable_scroll_properties',
  disableSessionRecording: 'disable_session_recording',
  disableSurveys: 'disable_surveys',
  disableSurveysAutomaticDisplay: 'disable_surveys_automatic_display',
  disableWebExperiments: 'disable_web_experiments',
  enableRecordingConsoleLog: 'enable_recording_console_log',
  maskAllElementAttributes: 'mask_all_element_attributes',
  maskAllText: 'mask_all_text',
  personProfiles: 'person_profiles',
  saveCampaignParams: 'save_campaign_params',
  saveReferrer: 'save_referrer',
  sessionRecording: 'session_recording',
} as const;

const readNonemptyString = (value: unknown): null | string => {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
};

const analyticsOptions = (host: string) =>
  ({
    autocapture: false,
    disableDeviceModel: true,
    persistence: 'memory',
    [postHogOption.advancedDisableDecide]: true,
    [postHogOption.advancedDisableFeatureFlags]: true,
    [postHogOption.advancedDisableFeatureFlagsOnFirstLoad]: true,
    [postHogOption.advancedDisableFlags]: true,
    [postHogOption.advancedDisableToolbarMetrics]: true,
    [postHogOption.apiHost]: host,
    [postHogOption.beforeSend]: (event) =>
      event?.event === 'lab_protocol_failure' ? event : null,
    [postHogOption.captureDeadClicks]: false,
    [postHogOption.captureExceptions]: false,
    [postHogOption.captureHeatmaps]: false,
    [postHogOption.capturePageleave]: false,
    [postHogOption.capturePageview]: false,
    [postHogOption.capturePerformance]: false,
    [postHogOption.disableCaptureUrlHashes]: true,
    [postHogOption.disableConversations]: true,
    [postHogOption.disableExternalDependencyLoading]: true,
    [postHogOption.disablePersistence]: true,
    [postHogOption.disableProductTours]: true,
    [postHogOption.disableScrollProperties]: true,
    [postHogOption.disableSessionRecording]: true,
    [postHogOption.disableSurveys]: true,
    [postHogOption.disableSurveysAutomaticDisplay]: true,
    [postHogOption.disableWebExperiments]: true,
    [postHogOption.enableRecordingConsoleLog]: false,
    [postHogOption.maskAllElementAttributes]: true,
    [postHogOption.maskAllText]: true,
    [postHogOption.personProfiles]: 'never',
    [postHogOption.saveCampaignParams]: false,
    [postHogOption.saveReferrer]: false,
    [postHogOption.sessionRecording]: {
      blockClass: 'ph-no-capture',
      captureCanvas: { recordCanvas: false },
      maskAllElementAttributes: true,
      maskAllInputs: true,
    },
    rageclick: false,
  }) satisfies BrowserAnalyticsOptions;

export const createAnalyticsController = ({
  host,
  key,
  load,
}: BrowserAnalyticsInput) => {
  const configuredKey = readNonemptyString(key);
  const configuredHost = readNonemptyString(host) ?? defaultAnalyticsHost;
  let client: BrowserAnalyticsClient | null = null;
  let initialization: null | Promise<void> = null;

  const initialize = (): Promise<void> => {
    if (configuredKey === null || initialization !== null) {
      return initialization ?? Promise.resolve();
    }
    initialization = (async (): Promise<void> => {
      try {
        const loadedClient = await load();
        loadedClient.init(configuredKey, analyticsOptions(configuredHost));
        client = loadedClient;
      } catch {
        client = null;
      }
    })();
    return initialization;
  };

  const captureFailure = (): void => {
    client?.capture('lab_protocol_failure', { boundary: 'server_message' });
  };

  return { captureProtocolFailure: captureFailure, initialize };
};

const browserEnvironment = { ...import.meta.env };

const analytics = createAnalyticsController({
  host: browserEnvironment['VITE_POSTHOG_HOST'],
  key: browserEnvironment['VITE_POSTHOG_KEY'],
  load: async () => {
    const module = await import('posthog-js');
    return module.default;
  },
});

export const initializeAnalytics = analytics.initialize;
export const captureProtocolFailure = analytics.captureProtocolFailure;
