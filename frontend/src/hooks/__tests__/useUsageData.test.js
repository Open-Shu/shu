/**
 * Unit tests for the useUsageData hook.
 *
 * Tests the hook's data shape contract: that modelsMap stitches model
 * + provider data correctly, that lastUpdatedAt reflects the usage query,
 * and that refetch only invalidates billing queries (not the reference
 * data queries for models/providers).
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from 'react-query';

import { useUsageData } from '../useUsageData';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    billingAPI: {
      getUsage: vi.fn(),
      getSubscription: vi.fn(),
      getPortalUrl: vi.fn(),
    },
    llmAPI: {
      getModels: vi.fn(),
      getProviders: vi.fn(),
    },
  };
});

import { billingAPI, llmAPI } from '../../services/api';

const wrapWith = (queryClient) => {
  const Wrapper = ({ children }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  Wrapper.displayName = 'TestQueryWrapper';
  return Wrapper;
};

const newClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });

const wrapEnvelope = (data) => ({ data: { data } });

describe('useUsageData', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('builds modelsMap keyed by model id with display_name and provider_name', async () => {
    billingAPI.getUsage.mockResolvedValue(wrapEnvelope({ total_cost_usd: 0, by_model: [] }));
    billingAPI.getSubscription.mockResolvedValue(wrapEnvelope({ current_period_unknown: true }));
    llmAPI.getModels.mockResolvedValue(
      wrapEnvelope([
        { id: 'model-a', display_name: 'Claude Haiku 4.5', provider_id: 'prov-1' },
        { id: 'model-b', display_name: 'GPT-4o mini', provider_id: 'prov-2' },
      ])
    );
    llmAPI.getProviders.mockResolvedValue(
      wrapEnvelope([
        { id: 'prov-1', name: 'anthropic' },
        { id: 'prov-2', name: 'openai' },
      ])
    );

    const { result } = renderHook(() => useUsageData(), { wrapper: wrapWith(newClient()) });

    await waitFor(() => expect(result.current.modelsMap.size).toBe(2));

    expect(result.current.modelsMap.get('model-a')).toEqual({
      display_name: 'Claude Haiku 4.5',
      provider_name: 'anthropic',
    });
    expect(result.current.modelsMap.get('model-b')).toEqual({
      display_name: 'GPT-4o mini',
      provider_name: 'openai',
    });
  });

  it('returns null provider_name when the provider is missing', async () => {
    billingAPI.getUsage.mockResolvedValue(wrapEnvelope({ total_cost_usd: 0, by_model: [] }));
    billingAPI.getSubscription.mockResolvedValue(wrapEnvelope({ current_period_unknown: true }));
    llmAPI.getModels.mockResolvedValue(
      wrapEnvelope([{ id: 'model-a', display_name: 'Orphan Model', provider_id: 'gone' }])
    );
    llmAPI.getProviders.mockResolvedValue(wrapEnvelope([]));

    const { result } = renderHook(() => useUsageData(), { wrapper: wrapWith(newClient()) });

    await waitFor(() => expect(result.current.modelsMap.size).toBe(1));
    expect(result.current.modelsMap.get('model-a')).toEqual({
      display_name: 'Orphan Model',
      provider_name: null,
    });
  });

  it('returns an empty modelsMap when models or providers fail to load', async () => {
    billingAPI.getUsage.mockResolvedValue(wrapEnvelope({ total_cost_usd: 0, by_model: [] }));
    billingAPI.getSubscription.mockResolvedValue(wrapEnvelope({ current_period_unknown: true }));
    llmAPI.getModels.mockRejectedValue(new Error('boom'));
    llmAPI.getProviders.mockRejectedValue(new Error('boom'));

    const { result } = renderHook(() => useUsageData(), { wrapper: wrapWith(newClient()) });

    await waitFor(() => expect(result.current.usage.isSuccess).toBe(true));
    expect(result.current.modelsMap.size).toBe(0);
  });

  it('lastUpdatedAt reflects the usage query, not the subscription query', async () => {
    billingAPI.getUsage.mockResolvedValue(wrapEnvelope({ total_cost_usd: 0, by_model: [] }));
    billingAPI.getSubscription.mockResolvedValue(wrapEnvelope({ current_period_unknown: true }));
    llmAPI.getModels.mockResolvedValue(wrapEnvelope([]));
    llmAPI.getProviders.mockResolvedValue(wrapEnvelope([]));

    const queryClient = newClient();
    const { result } = renderHook(() => useUsageData(), { wrapper: wrapWith(queryClient) });

    await waitFor(() => expect(result.current.usage.isSuccess).toBe(true));
    const usageState = queryClient.getQueryState(['billing-usage']);
    expect(result.current.lastUpdatedAt).toBe(usageState.dataUpdatedAt);
  });

  it('refetch invalidates billing queries but not the reference (models/providers) queries', async () => {
    billingAPI.getUsage.mockResolvedValue(wrapEnvelope({ total_cost_usd: 0, by_model: [] }));
    billingAPI.getSubscription.mockResolvedValue(wrapEnvelope({ current_period_unknown: true }));
    llmAPI.getModels.mockResolvedValue(wrapEnvelope([]));
    llmAPI.getProviders.mockResolvedValue(wrapEnvelope([]));

    const queryClient = newClient();
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const { result } = renderHook(() => useUsageData(), { wrapper: wrapWith(queryClient) });

    await waitFor(() => expect(result.current.usage.isSuccess).toBe(true));

    invalidateSpy.mockClear();
    result.current.refetch();

    expect(invalidateSpy).toHaveBeenCalledWith(['billing-usage']);
    expect(invalidateSpy).toHaveBeenCalledWith(['cost-usage:billing-subscription']);
    expect(invalidateSpy).not.toHaveBeenCalledWith(['billing-llm-models']);
    expect(invalidateSpy).not.toHaveBeenCalledWith(['billing-llm-providers']);
  });
});
