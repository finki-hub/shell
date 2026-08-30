import { describe, expect, it } from 'vitest';

import {
  type BrowserAnalyticsClient,
  type BrowserAnalyticsOptions,
  createAnalyticsController,
} from '@/lib/analytics';

type AnalyticsCall = {
  readonly eventName: string;
  readonly properties: { readonly boundary: 'server_message' };
};

describe('browser analytics privacy boundary', () => {
  it('does nothing for missing or whitespace-only build keys', async () => {
    let loads = 0;
    const controller = createAnalyticsController({
      host: ' '.repeat(3),
      key: ' '.repeat(3),
      load: () => {
        loads += 1;
        return Promise.resolve({
          capture: () => null,
          init: () => null,
        });
      },
    });

    await controller.initialize();
    controller.captureProtocolFailure();

    expect(loads).toBe(0);
  });

  it('loads once after an intentional key and emits only the stable protocol marker', async () => {
    const load = Promise.withResolvers<BrowserAnalyticsClient>();
    const calls: AnalyticsCall[] = [];
    let loads = 0;
    const initialization: {
      value: null | {
        readonly key: string;
        readonly options: BrowserAnalyticsOptions;
      };
    } = { value: null };
    const controller = createAnalyticsController({
      host: ' '.repeat(3),
      key: ' configured ',
      load: () => {
        loads += 1;
        return load.promise;
      },
    });

    const firstInitialization = controller.initialize();
    const repeatedInitialization = controller.initialize();
    controller.captureProtocolFailure();
    load.resolve({
      capture: (eventName, properties) => {
        calls.push({ eventName, properties });
      },
      init: (key, options) => {
        initialization.value = { key, options };
      },
    });
    await Promise.all([firstInitialization, repeatedInitialization]);
    controller.captureProtocolFailure();

    expect(loads).toBe(1);
    expect(initialization.value?.key).toBe('configured');
    expect(initialization.value?.options.api_host).toBe(
      'https://eu.i.posthog.com',
    );
    expect(initialization.value?.options.autocapture).toBe(false);
    expect(initialization.value?.options.capture_dead_clicks).toBe(false);
    expect(initialization.value?.options.capture_exceptions).toBe(false);
    expect(initialization.value?.options.capture_heatmaps).toBe(false);
    expect(initialization.value?.options.capture_pageleave).toBe(false);
    expect(initialization.value?.options.capture_pageview).toBe(false);
    expect(initialization.value?.options.capture_performance).toBe(false);
    expect(
      initialization.value?.options.disable_external_dependency_loading,
    ).toBe(true);
    expect(initialization.value?.options.disable_persistence).toBe(true);
    expect(initialization.value?.options.disable_session_recording).toBe(true);
    expect(initialization.value?.options.enable_recording_console_log).toBe(
      false,
    );
    expect(initialization.value?.options.mask_all_element_attributes).toBe(
      true,
    );
    expect(initialization.value?.options.mask_all_text).toBe(true);
    expect(initialization.value?.options.rageclick).toBe(false);
    expect(
      initialization.value?.options.session_recording?.captureCanvas
        ?.recordCanvas,
    ).toBe(false);
    expect(calls).toEqual([
      {
        eventName: 'lab_protocol_failure',
        properties: { boundary: 'server_message' },
      },
    ]);
  });

  it('contains loader failure without retrying or blocking the caller', async () => {
    let loads = 0;
    const controller = createAnalyticsController({
      host: '',
      key: 'configured',
      load: () => {
        loads += 1;
        return Promise.reject(new Error('unavailable'));
      },
    });

    await controller.initialize();
    await controller.initialize();
    controller.captureProtocolFailure();

    expect(loads).toBe(1);
  });
});
