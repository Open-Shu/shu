/**
 * Pure transform from the `/billing/usage/me` `by_day` payload into chart
 * series for the My Usage time-series (SHU-844).
 *
 * The backend returns one row per (UTC day, model). This pivots them into a
 * sorted date axis plus one cost series per model, so @mui/x-charts can render
 * a line per model with a visibility toggle. Kept pure (no React, no MUI) so it
 * is unit-testable in isolation.
 */

// Truncated-UUID fallback dimensions, matching the Cost by Model table.
const SHORT_ID_THRESHOLD = 12;
const SHORT_ID_CHARS = 8;

/**
 * Resolve a model's display label using the same three-tier fallback as the
 * Cost by Model table: live catalog name → backend snapshot name → short id.
 */
function resolveLabel(modelId, modelName, modelsMap) {
  if (modelId === 'unknown' || !modelId) {
    return 'Unattributed';
  }
  const resolved = modelsMap?.get?.(modelId);
  if (resolved && resolved.display_name) {
    return resolved.display_name;
  }
  if (modelName) {
    return modelName;
  }
  const id = String(modelId);
  return id.length > SHORT_ID_THRESHOLD ? `model_${id.slice(0, SHORT_ID_CHARS)}` : `model_${id}`;
}

/**
 * @param {Array<{date:string, model_id:string|null, model_name:string|null, cost_usd:number}>} byDay
 * @param {Map<string,{display_name?:string,provider_name?:string}>} [modelsMap]
 * @returns {{ dates: string[], series: Array<{ modelId:string, label:string, data:number[] }> }}
 */
export function buildDailySeries(byDay, modelsMap) {
  const rows = Array.isArray(byDay) ? byDay : [];

  const dateSet = new Set();
  const modelOrder = [];
  const modelSeen = new Set();
  // modelId -> (date -> summed cost)
  const costByModelDate = new Map();

  for (const row of rows) {
    if (!row || !row.date) {
      continue;
    }
    dateSet.add(row.date);
    const modelId = row.model_id || 'unknown';
    if (!modelSeen.has(modelId)) {
      modelSeen.add(modelId);
      modelOrder.push({ modelId, modelName: row.model_name });
    }
    let perDate = costByModelDate.get(modelId);
    if (!perDate) {
      perDate = new Map();
      costByModelDate.set(modelId, perDate);
    }
    const cost = Number(row.cost_usd) || 0;
    perDate.set(row.date, (perDate.get(row.date) || 0) + cost);
  }

  const dates = Array.from(dateSet).sort();
  const series = modelOrder.map(({ modelId, modelName }) => {
    const perDate = costByModelDate.get(modelId) || new Map();
    return {
      modelId,
      label: resolveLabel(modelId, modelName, modelsMap),
      data: dates.map((d) => perDate.get(d) || 0),
    };
  });

  return { dates, series };
}
