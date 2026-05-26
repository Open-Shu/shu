// Verbs themed on Shu (Egyptian god of air, breath, light, atmosphere).
// Rotated during the pre-stream "thinking" phase. User-editable.
export const DEFAULT_POOL = [
  'Wafting',
  'Channelling',
  'Sculpting',
  'Weaving',
  'Gusting',
  'Susurrating',
  'Drifting',
  'Breathing',
  'Whispering',
  'Soaring',
  'Swirling',
  'Lifting',
  'Stirring',
  'Murmuring',
  'Spiriting',
];

// Used when a knowledge base is selected on the request. Themed on
// search / retrieval / sifting.
export const RAG_POOL = [
  'Searching',
  'Sifting',
  'Gathering',
  'Tracing',
  'Combing',
  'Distilling',
  'Gleaning',
  'Harvesting',
  'Foraging',
  'Threading',
  'Indexing',
  'Surfacing',
  'Recalling',
  'Tracking',
  'Unearthing',
];

// Used when a plugin is attached to the request. Themed on
// reaching out / consulting / dispatching.
export const PLUGIN_POOL = [
  'Reaching',
  'Consulting',
  'Conferring',
  'Beckoning',
  'Hailing',
  'Liaising',
  'Dispatching',
  'Petitioning',
  'Soliciting',
  'Conveying',
  'Coordinating',
  'Heralding',
  'Summoning',
  'Brokering',
  'Querying',
];

const POOLS = {
  default: DEFAULT_POOL,
  rag: RAG_POOL,
  plugin: PLUGIN_POOL,
};

export const getPoolFor = (thinkingPool) => POOLS[thinkingPool] || DEFAULT_POOL;

// Pick a random verb from `pool`, biased away from `currentWord` so the
// same word never appears twice in a row (single-element pool degenerates
// to always returning that element).
export const pickNextWord = (pool, currentWord) => {
  if (!Array.isArray(pool) || pool.length === 0) {
    return '';
  }
  if (pool.length === 1) {
    return pool[0];
  }
  const candidates = pool.filter((w) => w !== currentWord);
  return candidates[Math.floor(Math.random() * candidates.length)];
};
