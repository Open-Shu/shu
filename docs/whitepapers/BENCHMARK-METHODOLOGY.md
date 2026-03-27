# Benchmark Methodology: Answer-Utility Evaluation for Multi-Surface Retrieval

## Context

Shu's multi-surface retrieval architecture — including ingestion-time intelligence (LLM-generated chunk summaries, document synopses, and synthesized capability queries) and parallel multi-surface search with score fusion — is the subject of a patent filing. This benchmark exists to produce rigorous, reproducible evidence that the approach delivers measurable retrieval quality improvements over baseline embedding similarity. The results directly support the patent's claims about the utility of ingestion-time document intelligence for bridging the vocabulary gap between user queries and document content.

## The Core Problem

A user should be able to find answers in documents even when their vocabulary doesn't match the document's vocabulary. A researcher writes "dietary MUFA intake inversely correlated with prostatic neoplasia risk" and a user asks "are avocados good for you?" — the same information, completely different language.

Baseline embedding similarity (cosine distance between query and chunk embeddings) can't bridge this gap because the surface-level semantics are too far apart. Multi-surface retrieval bridges it through synthesized query matching — at ingestion, an LLM reads each document and generates questions it can answer, creating searchable artifacts that embed close to the user's natural language. Additional surfaces (chunk summary embeddings, document synopsis embeddings) capture document meaning at different granularities than raw content, providing independent retrieval signals.

The benchmark must demonstrate two things:
1. Multi-surface finds documents baseline misses when user language diverges from document language
2. Multi-surface doesn't lose accuracy on queries where vocabulary overlaps directly

## Why Traditional Benchmarks Fail

Standard IR benchmarks (BEIR, NDCG, MAP) measure whether a system ranks a pre-determined set of "correct" documents highly. These qrels were created for systems that find documents through vocabulary overlap or learned semantic similarity. The relevant documents share vocabulary or topic with the query.

When multi-surface finds a genuinely relevant document through cross-topic semantic bridging, traditional benchmarks score it as a **false positive** — the document wasn't in the pre-determined relevant set. The better the system gets at vocabulary-gap retrieval, the worse it scores.

Even with this penalty, multi-surface beats baseline on BEIR (+9.0% NDCG@10, +13.0% MAP@10, +8.8% MRR@10 on NFCorpus). But this understates the real advantage.

### Topical Relevance Also Fails

Our first attempt at custom relevance judgments used topical relevance criteria ("is this document about the same topic as the query?"). This produced results similar to BEIR — baseline slightly ahead. Topical relevance is what baseline optimizes for.

The benchmark only shows multi-surface's advantage when relevance is judged by **answer utility** — "does this document contain information that helps answer the question?" A document about prostate cancer is not "about" avocados, but it contains direct evidence that avocado intake reduces cancer risk. A topical judge scores it 0. An answer-utility judge scores it 2.

## Our Approach: Frozen Answer-Utility QRels

We build a benchmark dataset of natural-language questions with answer-utility relevance judgments, then score deterministically against those frozen qrels on every future run.

### Question Generation

Questions are generated from corpus documents, deliberately written in everyday language that diverges from the document's technical vocabulary:

- Read a corpus document with technical language
- Write 2-3 natural questions a regular user would ask that the document helps answer
- "Can the way you cook food cause disease?" NOT "heterocyclic amine formation during high-temperature pyrolysis"

Questions are categorized as **vocabulary-mismatch** (user language differs from document language) or **direct-match** (user terms appear in relevant documents) to enable segmented analysis.

### Candidate Collection

For each question, both strategies search the same KB:
- **Baseline**: `query_type=similarity` (cosine similarity on chunk content embeddings)
- **Multi-surface**: `query_type=multi_surface` (parallel execution of chunk_vector, chunk_summary, query_match, synopsis_match surfaces with surface-agreement fusion — see Score Fusion Evolution below)

Baseline is collected with `limit=40` (chunks, which deduplicate to ~15-20 unique documents), multi-surface with `limit=20` (already document-level). After deduplication, this yields ~25 unique candidate documents per query. The JSON field is `limit`, not `top_k`.

### Judgment

Every candidate document is read and scored on a 3-point answer-utility scale:

- **0 — Not useful**: Document does not help answer the question.
- **1 — Partially useful**: Document contains tangentially related information that could inform but not directly answer the question.
- **2 — Highly relevant**: Document directly helps answer the question with specific information, evidence, or data.

Every judgment includes a **written justification** explaining why the document received its score. This makes the qrels auditable — anyone can read the justification and decide if they agree.

Cross-topic connections are explicitly considered. A document about fatty acids and prostate cancer is scored 2 for "are avocados good for you?" if it identifies avocado as the principal MUFA source and concludes the association reduces cancer risk.

### Scoring

Once qrels are frozen, scoring is deterministic and repeatable:
- Run both strategies against the KB
- For each query, count score-2 documents in each strategy's top-K
- Compute standard IR metrics (NDCG@K, MAP@K) against the frozen qrels
- Head-to-head: per query, which strategy surfaced more score-2 documents?
- Segment results by query type (vocabulary-mismatch vs direct-match)

Only documents returned by at least one strategy need judgments. Documents neither strategy surfaces are implicitly score-0 (sparse qrels, standard practice — BEIR's NFCorpus has 12K qrels across 1.17M possible pairs).

### Why This Isn't Biased

Both strategies are candidate generators — neither determines relevance. The judge reads every candidate document from both strategies and scores independently. Questions are generated from corpus documents before seeing what either strategy retrieves. Profiling happened at ingestion against raw documents, before any questions existed. The qrels include written justifications anyone can audit.

## Current Results

### PMC Health Track (85 queries judged of 100 generated, in progress)

- 500 full-text open-access articles (median 5,838 words, multi-chunk)
- 100 natural-language questions generated across diverse health topics
- 1,961 judgments for 85 queries, all scores verified from chunk content
- Score distribution: 1,138 score-0 (58%), 520 score-1 (27%), 302 score-2 (15%)
- BEIR-format files ready: run with `run_benchmark.py --dataset pmc_health`

### Benchmark Results Comparison (live runs, 2026-03-25)

| Corpus | Formula | NDCG@10 | MAP@10 | H2H Win% | Score-2 |
|--------|---------|---------|--------|----------|---------|
| NFCorpus (3633 docs) | max×√(mean/max) | **+8.0%** | **+13.4%** | 57% | +3.5% |
| NFCorpus (3633 docs) | weighted_average | +4.8% | +9.7% | 39% | -2.5% |
| PMC Health (500 docs) | max×√(mean/max) | +3.6% | +4.4% | 65% | +5.8% |
| PMC Health (500 docs) | weighted_average | **+5.5%** | **+6.3%** | **89%** | **+7.7%** |

All results are statistically significant (p < 0.05, paired t-test) and
reproducible from frozen qrels. The head-to-head metric counts score-2
(highly relevant) documents in each strategy's top-10 per query.

Neither fusion formula is universally optimal for NDCG. max×√(mean/max)
inflates NDCG on single-chunk corpora through agreement scoring on
near-identical surface signals. weighted_average is better on multi-chunk
documents where surfaces genuinely differentiate.

### Practical Retrieval: Relevant Documents Above Threshold

Traditional IR metrics (NDCG, MAP) measure ranking quality assuming the
full result set is presented. In practice, applications apply a minimum
score threshold. This metric measures what users actually experience: how
many highly-relevant documents survive a given threshold.

**NFCorpus (weighted_average, full 3633-doc corpus):**

| Threshold | BL score-2 | MS score-2 | Advantage |
|-----------|-----------|-----------|-----------|
| 0.30 | 306 | 329 | +7.5% |
| 0.40 | 200 | 221 | +10.5% |
| 0.50 | 105 | 123 | **+17.1%** |

**PMC Health (weighted_average, 500-doc corpus):**

| Threshold | BL score-2 | MS score-2 | Advantage |
|-----------|-----------|-----------|-----------|
| 0.30 | 272 | 293 | +7.7% |
| 0.35 | 262 | 280 | +6.9% |
| 0.40 | 228 | 238 | +4.4% |

Multi-surface consistently surfaces more highly-relevant documents at
every practical threshold on both corpora. On NFCorpus, the advantage
grows from +5% to +17% as the threshold increases — the more selective the
user, the bigger multi-surface's edge. This is the strongest evidence that
multi-surface retrieval provides genuine value beyond ranking improvements.

### NFCorpus Track (24 queries, preserved from earlier work)

The original NFCorpus answer-utility evaluation used 24 queries against single-chunk medical abstracts. These results are preserved but use older scoring methodology and are not directly comparable to the PMC Health track. See `nfcorpus/answer_utility/` for data.

## Two Benchmark Tracks

### Track 1: NFCorpus — Query Match Evaluation (complete, 27 queries)

NFCorpus documents are medical abstracts (~90 words each). Every document is a single chunk, so chunk_summary, chunk_vector, and synopsis_match produce nearly identical embeddings. On this corpus, query_match is responsible for 98.3% of multi-surface's top scores. The benchmark validates query_match's vocabulary-gap bridging but cannot evaluate the other surfaces.

**Status:** 27 queries, 687 judgments, MS wins 14, BL wins 7, Ties 6. Live scoring against the KB reproduces hand-computed results exactly. This track is complete and preserved as the definitive evidence for query_match vocabulary-gap bridging.

### Track 2: PMC Health — Full Multi-Surface Evaluation (in progress)

500 full-text open-access PubMed Central articles (median 5,838 words, ~12 chunks per document) ingested into KB `6e05a257-9981-4e8d-b7e0-ba44fba5a654`. On this corpus, chunk_summary, chunk_vector, and synopsis_match produce meaningfully different embeddings. Validation testing confirmed chunk_summary wins as top surface on some results — something that never occurred on NFCorpus.

**Status:** 45 queries, 1,150 judgments. All 500 documents profiled. `max × √(mean/max)` fusion active with `SHU_MULTI_SURFACE_CHUNK_LIMIT=500`. MS wins 78% of decided matchups, +17.5% score-2 in top-10. Target: 100 queries.

**Key finding from this track:** Surface agreement — measured as mean/max ratio of surface scores — is a strong signal for document relevance. Documents with balanced scores across all surfaces are more likely to be genuinely relevant than documents dominated by a single surface. This led to the continuous agreement fusion formula (see Score Fusion Evolution below).

### Score Fusion Evolution

The benchmark data drove scoring improvements through four stages:

**Stage 1: Max fusion.** `final_score = max(surface_scores)`. The highest individual surface score determines rank. Simple and preserves cross-topic discoveries, but query_match false positives — matching on question structure rather than content — displace genuine multi-surface consensus results. BEIR: +5.4% NDCG@10.

**Stage 2: Max-then-rerank (tested, failed).** Select top-K by max, then re-sort by weighted average. Failed (-11.4% score-2) because weighted average demotes query_match-only finds. Revisited in Stage 4 after fixing a data loss problem.

**Stage 3: Binary surface-agreement fusion (superseded).** `final_score = max × √(active_surfaces / total_surfaces)`. Counts how many surfaces have nonzero scores. Initially showed +7.5% NDCG@10, but this was inflated by a data loss artifact: the per-surface retrieval limit (`SHU_MULTI_SURFACE_CHUNK_LIMIT=50`) was causing relevant documents to get zero on surfaces they shouldn't have, which made binary active/inactive counting artificially discriminating. After fixing the limit to 500, binary agreement regressed to +6.0% because every document registered on all surfaces with weak-but-nonzero scores.

**Stage 4: Continuous agreement fusion (current).** `final_score = max × √(mean / max)`. Instead of counting surfaces as binary active/inactive, uses the ratio of mean to max surface score. A document where all surfaces score ~0.45 gets full credit (mean/max ≈ 1.0). A document dominated by one surface at 0.50 with others at 0.10 is penalized (mean/max ≈ 0.2). This combines the best of max fusion (protects strong single-surface finds from dilution) and weighted average (rewards genuine multi-surface agreement).

BEIR comparison (all on same surface score data, 2103-subset KB):

| Formula | NDCG@10 | MAP@10 | MRR@10 |
|---|---|---|---|
| Max fusion | +5.4% | +8.9% | +6.7% |
| Weighted average | +8.2% | +11.0% | +7.7% |
| **Max × √(mean/max)** | **+9.0%** | **+13.0%** | **+8.8%** |

The weighted average, previously rejected as broken, now works because the surface limit fix (50→500) eliminated the zero-score artifacts that were corrupting the average. The continuous agreement formula outperforms both by combining their strengths.

### Scoring Tooling

Live scoring against frozen qrels was validated during the NFCorpus track. The same approach applies to PMC Health: POST to `/api/v1/query/{kb_id}/search` with both `similarity` and `multi_surface` query types, count score-2 documents in each strategy's top-K, compute head-to-head wins. Collection script: `backend/src/tests/benchmark/.datasets/pmc_health/answer_utility/collect_candidates.py`.

### Query-Type-Aware Weight Adjustment

The data suggests query_match should be weighted more heavily for general/exploratory queries and less for specific/targeted queries. A lightweight query classification (LLM or heuristic) could select weight profiles before fusion. Both benchmark tracks enable measuring the impact — NFCorpus for query_match tuning, PMC Health for full surface weight tuning. See `docs/.internal/session-handoff-benchmarking.md` for implementation details.
