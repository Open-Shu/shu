/**
 * Data hook for the per-user "My Usage" dashboard (SHU-844).
 *
 * Mirrors useUsageData but hits the self-scoped `/billing/usage/me` endpoint
 * and drops the subscription/seat queries — the per-user page gets its
 * plan/pool context from BillingStatusContext (already polled app-wide), not a
 * second subscription fetch. Models are fetched to resolve model_id → display
 * name for the reused Cost by Model table and the chart.
 *
 * Provider names are intentionally NOT resolved here. `/llm/providers` is
 * admin-only, but this page is visible to ALL roles, so fetching it 403s for
 * non-admins (and there is no role-open endpoint that returns provider names).
 * `/llm/models` is open to every authenticated user and carries the display
 * name — the primary label — so the provider sub-label is simply omitted here.
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

const REFERENCE_STALE_MS = 5 * 60 * 1000;

const fetchMyUsage = () => billingAPI.getMyUsage().then(extractDataFromResponse);
// `model_type: 'all'` — My Usage reports billable embedding/OCR rows too, and
// /llm/models defaults to chat-only, so the full catalog is needed to resolve
// non-chat model_ids to live display names (otherwise they fall back to the
// usage row's snapshot model_name).
const fetchModels = () => llmAPI.getModels(null, 'all').then(extractDataFromResponse);

export function useMyUsageData() {
  const queryClient = useQueryClient();

  const usage = useQuery(USAGE_KEY, fetchMyUsage, { staleTime: 0 });
  const models = useQuery(MODELS_KEY, fetchModels, { staleTime: REFERENCE_STALE_MS });

  const modelsMap = useMemo(() => {
    const map = new Map();
    const modelsList = Array.isArray(models.data) ? models.data : [];
    for (const model of modelsList) {
      if (!model || !model.id) {
        continue;
      }
      map.set(model.id, {
        display_name: model.display_name || model.model_name || null,
        // Provider name is only available via the admin-only /llm/providers
        // endpoint, which this all-roles page can't call — left null so
        // CostByModelTable omits the provider sub-label.
        provider_name: null,
      });
    }
    return map;
  }, [models.data]);

  const refetch = useCallback(() => {
    queryClient.invalidateQueries(USAGE_KEY);
  }, [queryClient]);

  return {
    usage,
    modelsMap,
    modelsLoading: models.isLoading,
    refetch,
    lastUpdatedAt: usage.dataUpdatedAt || 0,
  };
}
