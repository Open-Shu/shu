# Retrieval Quality Analysis

Each search strategy is evaluated on the quality of what it actually returned —
not penalized for returning different documents than the other strategy.

## Result Quality: Top-10 Documents Returned

| Metric | Baseline (Chunk Similarity) | Multi-Surface | Delta |
|--------|---------------------------|---------------|-------|
| Avg relevant docs in top-10 | 3.3 | 3.0 | -9.3% |
| Avg highly relevant (score=2) in top-10 | 1.9 | 1.8 | -4.0% |
| Precision@10 | 32.8% | 29.8% | -9.3% |
| Precision@5 | 37.5% | 34.1% | -9.1% |
| MRR (first relevant result) | 0.464 | 0.448 | -3.3% |
| Queries with ≥1 relevant in top-10 | 159/323 | 159/323 | |
| Avg results returned per query | 16.0 | 20.0 | |

## Head-to-Head: Per-Query Comparison

For each query, which strategy returned more relevant documents in its top-10?

- **Multi-surface wins: 46** (14.2%)
- **Baseline wins: 70** (21.7%)
- **Ties: 207** (64.1%)
- Total queries: 323

## Exclusive Discovery

Relevant documents found by one strategy that the other didn't return at all.

- Multi-surface found **704** relevant docs baseline missed (209 highly relevant)
- Baseline found **557** relevant docs multi-surface missed (170 highly relevant)
- Found by both: 850

### Cross-Topic Discovery Examples (multi-surface only)

- **Query**: Do Cholesterol Statin Drugs Cause Breast Cancer?
  **Document**: Women and statin use: a women's health advocacy perspective.
  **Relevance**: Highly relevant
  **Found by**: bm25 (71.2%)
  **Why**: Women and statin use advocacy perspective - directly about women's statin use and health

- **Query**: Do Cholesterol Statin Drugs Cause Breast Cancer?
  **Document**: Management of grapefruit-drug interactions.
  **Relevance**: Partially relevant
  **Found by**: bm25 (68.1%)
  **Why**: Grapefruit-drug interactions - includes statin interactions with grapefruit

- **Query**: Do Cholesterol Statin Drugs Cause Breast Cancer?
  **Document**: Statin therapy induces ultrastructural damage in skeletal muscle in patients without myalgia.
  **Relevance**: Partially relevant
  **Found by**: bm25 (65.4%)
  **Why**: Statin-induced muscle damage - statin side effects relevant to risk-benefit

- **Query**: Do Cholesterol Statin Drugs Cause Breast Cancer?
  **Document**: Do phytoestrogens reduce the risk of breast cancer and breast cancer recurrence? What clinicians need to know.
  **Relevance**: Partially relevant
  **Found by**: bm25 (64.9%)
  **Why**: Phytoestrogens and breast cancer risk - breast cancer prevention, tangentially related

- **Query**: Do Cholesterol Statin Drugs Cause Breast Cancer?
  **Document**: Can a statin neutralize the cardiovascular risk of unhealthy dietary choices?
  **Relevance**: Highly relevant
  **Found by**: bm25 (64.3%)
  **Why**: Can a statin neutralize CVD risk of unhealthy diet - directly about statins

- **Query**: Do Cholesterol Statin Drugs Cause Breast Cancer?
  **Document**: A global survey of physicians' perceptions on cholesterol management: the From The Heart study.
  **Relevance**: Partially relevant
  **Found by**: bm25 (63.6%)
  **Why**: Physicians' cholesterol management perceptions - statin prescribing context

- **Query**: Do Cholesterol Statin Drugs Cause Breast Cancer?
  **Document**: Statin therapy, muscle function and falls risk in community-dwelling older adults.
  **Relevance**: Partially relevant
  **Found by**: bm25 (62.7%)
  **Why**: Statin therapy, muscle function and falls in elderly - statin side effects

- **Query**: Exploiting Autophagy to Live Longer
  **Document**: Saturated fatty acid metabolism is key link between cell division, cancer, and senescence in cellular and whole organism aging
  **Relevance**: Partially relevant
  **Found by**: query_match (47.9%)
  **Why**: Saturated fatty acid metabolism, cell division, cancer and senescence - cellular aging mechanisms related to autophagy

- **Query**: Exploiting Autophagy to Live Longer
  **Document**: FADD: a regulator of life and death.
  **Relevance**: Partially relevant
  **Found by**: bm25 (45.5%)
  **Why**: FADD regulator of life and death - apoptosis regulator, autophagy and apoptosis are interconnected

- **Query**: Exploiting Autophagy to Live Longer
  **Document**: Macronutrient balance and lifespan
  **Relevance**: Partially relevant
  **Found by**: query_match (45.2%)
  **Why**: Macronutrient balance and lifespan - nutrient sensing relates to autophagy regulation

## Threshold Survival

At progressively higher score thresholds, how many results survive and what fraction are relevant?

| Threshold | Baseline Survivors | BL Precision | MS Survivors | MS Precision |
|-----------|-------------------|-------------|-------------|-------------|
| 0.2 | 4404 / 5164 | 31.7% | 6281 / 6460 | 24.7% |
| 0.3 | 2823 / 5164 | 44.7% | 5253 / 6460 | 29.2% |
| 0.4 | 1183 / 5164 | 64.1% | 4715 / 6460 | 32.5% |
| 0.5 | 338 / 5164 | 79.6% | 3724 / 6460 | 38.0% |

## Natural Questions (46 queries)

| Metric | Baseline | Multi-Surface |
|--------|----------|---------------|
| Avg relevant in top-10 | 6.4 | 5.6 |
| Precision@10 | 64.6% | 56.1% |
| MRR | 0.920 | 0.874 |
| Wins | 24 | 11 |

## Topic Phrases (277 queries)

| Metric | Baseline | Multi-Surface |
|--------|----------|---------------|
| Avg relevant in top-10 | 2.8 | 2.5 |
| Precision@10 | 27.5% | 25.4% |
| MRR | 0.388 | 0.377 |
| Wins | 46 | 35 |
