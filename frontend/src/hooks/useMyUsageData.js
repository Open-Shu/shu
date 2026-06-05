/**
 * Data hook for the per-user "My Usage" dashboard (SHU-844).
 *
 * Mirrors useUsageData but hits the self-scoped `/billing/usage/me` endpoint
 * and drops the subscription/seat queries — the per-user page gets its
 * plan/pool context from BillingStatusContext (already polled app-wide), not a
 * second subscription fetch. Models + providers are fetched to resolve
 * model_id → display name for the reused Cost by Model table and the chart.
 *
 * Query keys are namespaced under `my-usage:` so they don't collide with the
 * admin dashboard's `billing-`/`cost-usage:` caches, which use the same
 * endpoints with a different (tenant-wide) payload shape.
 */

import { useMemo, useCallback } from 'react';
import { useQuery, useQueryClient } from 'react-query';

import { billingAPI, llmAPI, extractDataFromResponse } from '../services/api';

const USAGE_KEY = ['my-usage:usage'];
const MODELS_KEY = ['my-usage:llm-models'];
const PROVIDERS_KEY = ['my-usage:llm-providers'];

const REFERENCE_STALE_MS = 5 * 60 * 1000;

const fetchMyUsage = () => billingAPI.getMyUsage().then(extractDataFromResponse);
const fetchModels = () => llmAPI.getModels().then(extractDataFromResponse);
const fetchProviders = () => llmAPI.getProviders().then(extractDataFromResponse);

export function useMyUsageData() {
  const queryClient = useQueryClient();

  const usage = useQuery(USAGE_KEY, fetchMyUsage, { staleTime: 0 });
  const models = useQuery(MODELS_KEY, fetchModels, { staleTime: REFERENCE_STALE_MS });
  const providers = useQuery(PROVIDERS_KEY, fetchProviders, { staleTime: REFERENCE_STALE_MS });

  const modelsMap = useMemo(() => {
    const map = new Map();
    const modelsList = Array.isArray(models.data) ? models.data : [];
    const providersList = Array.isArray(providers.data) ? providers.data : [];
    const providerNamesById = new Map(providersList.map((p) => [p.id, p.name]));
    for (const model of modelsList) {
      if (!model || !model.id) {
        continue;
      }
      map.set(model.id, {
        display_name: model.display_name || model.model_name || null,
        provider_name: providerNamesById.get(model.provider_id) || null,
      });
    }
    return map;
  }, [models.data, providers.data]);

  const refetch = useCallback(() => {
    queryClient.invalidateQueries(USAGE_KEY);
  }, [queryClient]);

  return {
    usage,
    modelsMap,
    modelsLoading: models.isLoading || providers.isLoading,
    refetch,
    lastUpdatedAt: usage.dataUpdatedAt || 0,
  };
}
