# Benchmark Methodology: Evaluating Multi-Surface Retrieval

## Context

Shu's multi-surface retrieval architecture — including ingestion-time intelligence (LLM-generated chunk summaries, document synopses, and synthesized capability queries) and parallel multi-surface search with score fusion — is the subject of a patent filing. This benchmark exists to produce rigorous, reproducible evidence that the approach delivers measurable retrieval quality improvements over baseline embedding similarity. The results directly support the patent's claims about the utility of ingestion-time document intelligence for bridging the vocabulary gap between user queries and document content.

## The Core Problem

A user should be able to find answers in documents even when their vocabulary doesn't match the document's vocabulary. A researcher writes "dietary MUFA intake inversely correlated with prostatic neoplasia risk" and a user asks "are avocados good for you?" — the same information, completely different language.

Baseline embedding similarity (cosine distance between query and chunk embeddings) can't bridge this gap because the surface-level semantics are too far apart. Multi-surface retrieval bridges it through synthesized query matching — at ingestion, an LLM reads each document and generates questions it can answer, creating searchable artifacts that embed close to the user's natural language. Additional surfaces (chunk summary embeddings, document synopsis embeddings) capture document meaning at different granularities than raw content, providing independent retrieval signals.

The benchmark must demonstrate two things:
1. Multi-surface finds documents baseline misses when user language diverges from document language
2. Multi-surface doesn't lose accuracy on queries where vocabulary overlaps directly

## Two Evaluation Methodologies

### 1. BEIR Benchmarks (traditional IR metrics)

Standard IR evaluation using published BEIR datasets with professional qrels (pre-determined relevance judgments). We measure NDCG@10, precision, recall, MRR, and MAP against frozen qrels using the ranx evaluation library.

**Strengths**: reproducible, comparable to published results from other systems, professionally curated qrels.

**Limitations**: qrels are incomplete — when multi-surface finds genuinely relevant documents that the original annotators didn't anticipate, BEIR scores them as false positives. The better multi-surface gets at vocabulary-gap retrieval, the more BEIR understates its advantage.

**Results on published datasets:**

| Corpus | Documents | NDCG@10 vs Baseline | Notes |
|--------|-----------|-------------------|-------|
| NFCorpus | 3,633 biomedical abstracts | **+8.0%** | Short single-paragraph documents |
| SciFact | 5,183 scientific abstracts | **+5.9%** | Declarative claim-style queries |

Multi-surface beats baseline on every standard metric across both published datasets, with statistical significance (p < 0.05, paired t-test). These results are achieved despite the incomplete qrels penalty.

### 2. Answer-Utility Case Study (LLM-as-judge)

Automated blinded A/B evaluation where LLM judges compare both strategies' result sets and determine which better equips an LLM to answer the query. This methodology was developed because BEIR's incomplete qrels cannot measure multi-surface's core value — finding relevant documents through vocabulary-gap bridging that traditional annotators didn't anticipate.

**Why we moved beyond BEIR**: on SciFact, we found that 36% of queries where BEIR says baseline wins are actually multi-surface wins when evaluated by answer utility. BEIR is positive for multi-surface but dramatically understates the real advantage, especially on long-document corpora where multi-surface's ability to find different information in different sections doesn't register in short-document BEIR datasets.

**Methodology:**
1. For each query, both strategies search independently with the same parameters (result limit, score threshold)
2. Results are formatted identically with per-document chunk caps matching the KB's RAG configuration
3. Result sets are randomly assigned to "Set A" and "Set B" (blinded, randomized position)
4. An LLM judge evaluates retrieval relevance and answer utility, producing a structured verdict
5. Verdicts are de-blinded and aggregated across all queries

**PMC Health results (100 queries, 500 full-text research papers):**

| Judge | Provider | MS Win Rate |
|-------|----------|-------------|
| Claude Haiku 4.5 | Anthropic | **84%** |
| Gemini 3.1 Pro | Google | **86%** |
| Grok 4.20 | xAI | **87%** |
| GPT-5.4 | OpenAI | **89%** |
| Claude Opus 4.5 | Anthropic | **97%** |

Five judges from four providers independently agree: multi-surface wins 84-97% of queries on long-document corpora with natural language questions.

### Why Both Methodologies Matter

BEIR provides credibility — it's the standard benchmark that reviewers and researchers expect. Multi-surface beats baseline on published BEIR datasets, which establishes that the approach doesn't hurt traditional metrics.

The case study provides truth — it measures what actually matters for RAG: does the retrieval result help an LLM answer the question? On long-document corpora where multi-surface should shine, the case study reveals an 84-97% win rate that BEIR's +4-8% NDCG improvement dramatically understates.

Together, they tell a complete story: multi-surface is at least as good as baseline on traditional metrics (BEIR), and dramatically better on the metric that matters for real-world usage (answer utility).

## Why Traditional Benchmarks Understate Multi-Surface Value

Standard IR benchmarks score against a pre-determined set of "correct" documents. These qrels were created for systems that find documents through vocabulary overlap or learned semantic similarity.

Three specific problems:

**1. Cross-topic discoveries are penalized.** When multi-surface finds a genuinely relevant document through cross-topic semantic bridging (e.g., query_match finding a prostate cancer paper for "are avocados good for you?" because the synthesized query was "can eating avocados lower prostate cancer risk?"), BEIR scores it as a false positive.

**2. Short-document datasets can't test multi-surface's strength.** NFCorpus documents are single paragraphs. SciFact documents are short abstracts. On these corpora, all surfaces embed essentially the same text — there's no opportunity for chunk_summary to find something chunk_vector missed in a different section of a long paper. The published BEIR datasets test multi-surface under the worst conditions for it.

**3. Topical relevance ≠ answer utility.** A document about prostate cancer is not "about" avocados, but it contains direct evidence that avocado intake reduces cancer risk. Traditional relevance judges score by topic overlap. Answer-utility judges score by information content.

## Score Fusion

Multi-surface search runs 5 retrieval surfaces in parallel and fuses their scores into a final document ranking. Two fusion formulas are available, selectable per query:

### Weighted Average

`final_score = Σ(score × weight) / Σ(weight)` for surfaces with weight > 0 and score > 0.

Each surface contributes proportionally to its configured weight. Allows genuine multi-surface consensus without penalizing documents found strongly by a single surface. Better for multi-chunk corpora where surfaces find genuinely different content in different parts of the document.

### Max × √(mean/max)

`final_score = max(scores) × √(mean(scores) / max(scores))`

The max score determines the ceiling. The mean/max ratio measures surface agreement. Documents with balanced scores across surfaces get full credit; those dominated by a single surface are penalized. Better for single-chunk corpora where surfaces produce correlated scores and the agreement signal filters false positives.

### Fusion Formula History

The benchmark data drove scoring improvements through four stages:

1. **Max fusion** (+5.4% NDCG@10). Simplest — highest surface score wins. Query_match false positives displace genuine results.

2. **Max-then-rerank** (failed, -11.4% score-2). Weighted average as a reranker demoted query_match-only finds.

3. **Binary surface-agreement** (+6.0% NDCG@10, superseded). Counted active surfaces. Initially showed +7.5% but this was inflated by a data loss artifact from a low per-surface retrieval limit (50 vs 500). After fixing the limit, binary counting lost discriminating power because every document registered on all surfaces.

4. **Continuous agreement** (max × √(mean/max), +9.0% NDCG@10). Replaced binary active/inactive with the continuous mean/max ratio. Combines max fusion's protection of strong single-surface finds with weighted average's reward for consensus.

The weighted average formula, previously rejected as broken (-5.6% NDCG), was rehabilitated after fixing the per-surface retrieval limit (50→500) that had been corrupting scores with truncation artifacts. With correct data, weighted average performs well on multi-chunk corpora (+5.5% NDCG@10 on PMC Health, +4.8% on NFCorpus).

Neither formula is universally optimal. The choice depends on corpus characteristics — document length, query style, and surface score correlation.

## Corpus Characteristics and Weight Guidance

| Corpus | Doc Length | Query Style | Weight Strategy |
|--------|-----------|-------------|-----------------|
| NFCorpus | Sub-chunk (single paragraph) | Keywords/phrases | cv high, sm moderate, qm low |
| SciFact | Short abstracts (1-3 chunks) | Declarative claims | cv=0.35, sm=0.30, cs=0.25, qm=0.10 |
| PMC Health | Full papers (avg 57 chunks) | Natural questions | cv=0.25, qm=0.30, sm=0.25, cs=0.20 |

**query_match** is most valuable when queries are natural language questions (PMC Health). It adds noise when queries are keyword-style claims (SciFact) or phrases (NFCorpus).

**synopsis_match** is most valuable on short documents where the synopsis captures the entire document's meaning, and on long documents where it provides the only document-level semantic signal.

**Threshold matters.** At threshold 0.0, multi-surface returns much more noise than baseline. At 0.3 (practical usage), noise is filtered and multi-surface's advantage shows through clearly.

## Key Lessons Learned

### Ablation-based weight recommendations don't work

Local ablation (zeroing a surface's scores and recomputing fusion from collected data) does not predict actual weight sensitivity. On PMC Health, ablation predicted that removing query_match would improve NDCG by +2.6%. Actually reducing query_match's weight made NDCG go down. The local recomputation shortcut doesn't match real search behavior because the document set changes when weights change. Weight recommendations and fusion impact sections have been removed from the BEIR report (SHU-653).

The real weight tuning tool is the case study evaluation — run the same queries with different weight configurations and compare LLM judge verdicts.

### Chunk cap is critical for fair evaluation

Early case study runs on PMC Health had a severe bias: multi-surface was returning up to 49 chunks per document while baseline returned 2-4. LLM judges were comparing fundamentally different amounts of information. After applying the KB's `max_chunks_per_document` RAG config cap, multi-surface's win rate actually increased (Haiku: 77% → 84%) because the cap removed noise that was costing verdicts. The chunk cap is now enforced in the result formatter.

### All surfaces underperform individually, but fusion outperforms

On PMC Health, every individual surface has lower NDCG@10 than baseline chunk similarity. But fused together they beat baseline by +4%. This demonstrates that multi-surface value comes from complementary signals, not from any single superior surface. Single-surface ablation cannot capture this interaction.

### Synthetic qrels are unreliable

Our attempt to build custom answer-utility qrels for PMC Health (generating questions, judging every document 0/1/2 with LLMs) produced a 36.6% error rate on batch judgments. The comparative case study approach (which set is better?) is more robust than absolute scoring (how relevant is this document?) because it doesn't require reading thousands of documents individually.

## Future Directions

### Query-type-aware weight adjustment

The data suggests weights should vary by query type — natural language questions benefit from high query_match weight, while keyword-style queries benefit from low query_match weight. A lightweight query classification could select weight profiles before fusion. Both benchmark corpora enable measuring the impact.

### Long-document BEIR dataset

NFCorpus and SciFact are short-document corpora that can't fully test multi-surface's advantage. A BEIR dataset with long documents and professional qrels would let us validate multi-surface where it should be strongest. CODEC's judged subset (~17,500 documents, full web pages, complex research queries) is the best candidate identified so far.

### NFCorpus case study

The case study methodology could be applied to NFCorpus to measure answer utility on short documents and compare with the existing BEIR results. This would strengthen the cross-corpus story.

## Tooling

### BEIR Benchmark
```bash
cd shu/backend/src
python -m tests.benchmark.run_benchmark --dataset nfcorpus --reuse-kb <kb-id>
python -m tests.benchmark.run_benchmark --dataset scifact --reuse-kb <kb-id>
```

### Answer-Utility Case Study
```bash
cd shu/backend/src
python -m tests.benchmark.run_answer_utility_eval \
    --dataset pmc_health --reuse-kb <kb-id> --model-config <config-id> \
    --run-name <name> --concurrency 8 --qrels-only \
    --weight chunk_vector=0.25 --weight query_match=0.30 \
    --weight synopsis_match=0.25 --weight chunk_summary=0.20
```

See `backend/src/tests/benchmark/README.md` for full documentation of both tools.
