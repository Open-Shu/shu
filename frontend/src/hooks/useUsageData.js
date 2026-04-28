/**
 * Data hook for the admin Cost & Usage dashboard.
 *
 * Wraps four independent React Query calls:
 *   - billing usage (manual refresh model — staleTime 0)
 *   - billing subscription (refreshes via webhook; cached briefly)
 *   - LLM models and providers (used for resolving model_id → display name + provider name)
 *
 * Returns a flat object so the page component does not need to know about
 * the underlying query layout.
 */

import { useMemo, useCallback } from 'react';
import { useQuery, useQueryClient } from 'react-query';

import { billingAPI, llmAPI, extractDataFromResponse } from '../services/api';

const USAGE_KEY = ['billing-usage'];
const SUBSCRIPTION_KEY = ['billing-subscription'];
// Namespaced keys for the model/provider lookups. Other components
// (ModernChat, LLMProviders, LLMTester, ModelConfigurations) cache
// these under unscoped keys (`'llm-models'` / `'llm-providers'`) but
// with a different fetcher shape (raw axios response, no envelope
// unwrap). Sharing the key would cause cache collisions where the
// data shape depends on which consumer ran most recently.
const MODELS_KEY = ['billing-llm-models'];
const PROVIDERS_KEY = ['billing-llm-providers'];

const SUBSCRIPTION_STALE_MS = 60 * 1000;
const REFERENCE_STALE_MS = 5 * 60 * 1000;

const fetchUsage = () => billingAPI.getUsage().then(extractDataFromResponse);
const fetchSubscription = () => billingAPI.getSubscription().then(extractDataFromResponse);
const fetchModels = () => llmAPI.getModels().then(extractDataFromResponse);
const fetchProviders = () => llmAPI.getProviders().then(extractDataFromResponse);

export function useUsageData() {
  const queryClient = useQueryClient();

  const usage = useQuery(USAGE_KEY, fetchUsage, { staleTime: 0 });
  const subscription = useQuery(SUBSCRIPTION_KEY, fetchSubscription, {
    staleTime: SUBSCRIPTION_STALE_MS,
  });
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
    queryClient.invalidateQueries(SUBSCRIPTION_KEY);
  }, [queryClient]);

  return {
    usage,
    subscription,
    modelsMap,
    modelsLoading: models.isLoading || providers.isLoading,
    refetch,
    lastUpdatedAt: usage.dataUpdatedAt || 0,
  };
}

export const __testing = {
  USAGE_KEY,
  SUBSCRIPTION_KEY,
  MODELS_KEY,
  PROVIDERS_KEY,
};
