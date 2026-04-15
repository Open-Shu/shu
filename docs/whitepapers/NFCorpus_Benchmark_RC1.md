# Shu RAG Multi-Surface Retrieval: Benchmark Results

- **Date:** 2026-03-26
- **Benchmark:** nfcorpus (BEIR standard IR evaluation corpus)
- **Corpus:** 3,633 documents, 323 queries with ground-truth relevance judgments
- **Embedding Model:** Snowflake/snowflake-arctic-embed-l-v2.0
- **Fusion Formula:** max_sqrt_mean_max
- **Profiling Model:** Claude Haiku 4.5 (claude-haiku-4-5-20251001)

## Result

Multi-surface retrieval with ingestion-time intelligence **outperforms standard chunk-only RAG across every standard IR metric**, with statistical significance (p < 0.05, paired t-test).

| Metric | Description | BM25 | Chunk Similarity | SHU Multi-Surface | vs BM25 | vs Chunk Sim |
|--------|-------------|------|-----------------|---------------|---------|--------------|
| precision@5 | Fraction of top 5 that are relevant | 0.3115 | 0.3232 | 0.3387 | +8.7% | +4.8% |
| precision@10 | Fraction of top 10 that are relevant | 0.2359 | 0.2520 | 0.2647 | +12.2% | +5.0% |
| recall@5 | Fraction of all relevant docs found in top 5 | 0.1263 | 0.1344 | 0.1441 | +14.1% | +7.2% |
| recall@10 | Fraction of all relevant docs found in top 10 | 0.1567 | 0.1685 | 0.1820 | +16.2% | +8.0% |
| mrr@10 | How high the first relevant result ranks | 0.5383 | 0.5341 | 0.5843 | +8.6% | +9.4% |
| ndcg@5 | Ranking quality of top 5 (graded relevance) | 0.3647 | 0.3728 | 0.4023 | +10.3% | +7.9% |
| ndcg@10 | Ranking quality of top 10 (graded relevance) | 0.3292 | 0.3426 | 0.3699 | +12.3% | +8.0% |
| map@10 | Average precision across all relevant docs | 0.1233 | 0.1276 | 0.1447 | +17.4% | +13.4% |

## Practical Retrieval: Metrics at Score Threshold

In practice, applications apply a minimum score threshold. These metrics measure what users actually experience at each threshold — not just ranking quality, but actual result sets.

**Threshold-based IR metrics (macro-averaged across queries):**
- **Precision@threshold**: Fraction of results above threshold that are relevant
- **Recall@threshold**: Fraction of all relevant documents found above threshold
- **F1@threshold**: Harmonic mean of precision and recall

| Threshold | BL Precision | BL Recall | BL F1 | MS Precision | MS Recall | MS F1 | ΔF1 |
|-----------|--------------|-----------|-------|--------------|-----------|-------|-----|
| 0.25 | 0.188 | 0.227 | 0.206 | 0.174 | 0.268 | 0.211 | +2.5% |
| 0.30 | 0.224 | 0.188 | 0.205 | 0.219 | 0.237 | 0.228 | +11.3% |
| 0.35 | 0.269 | 0.149 | 0.192 | 0.258 | 0.204 | 0.228 | +18.7% |
| 0.40 | 0.261 | 0.104 | 0.149 | 0.283 | 0.156 | 0.201 | +35.0% |
| 0.45 | 0.251 | 0.070 | 0.109 | 0.269 | 0.111 | 0.157 | +43.2% |
| 0.50 | 0.190 | 0.044 | 0.071 | 0.274 | 0.082 | 0.126 | +75.9% |

**Head-to-head at threshold (per-query score-2 comparison):**

| Threshold | MS wins | BL wins | Ties | MS win% |
|-----------|---------|---------|------|---------|
| 0.25 | 32 | 5 | 286 | 86% |
| 0.30 | 34 | 5 | 284 | 87% |
| 0.35 | 47 | 5 | 271 | 90% |
| 0.40 | 44 | 3 | 276 | 94% |
| 0.45 | 42 | 4 | 277 | 91% |
| 0.50 | 34 | 0 | 289 | 100% |

**Raw document counts above threshold:**

| Threshold | BL docs | BL relevant | BL score-2 | MS docs | MS relevant | MS score-2 | Score-2 advantage |
|-----------|---------|-------------|------------|---------|-------------|------------|-------------------|
| 0.25 | 12681 | 1622 | 335 | 18809 | 2081 | 373 | +11.3% |
| 0.30 | 8143 | 1227 | 306 | 13463 | 1675 | 348 | +13.7% |
| 0.35 | 4252 | 859 | 259 | 7969 | 1268 | 315 | +21.6% |
| 0.40 | 1915 | 559 | 200 | 4187 | 893 | 265 | +32.5% |
| 0.45 | 794 | 319 | 144 | 1831 | 579 | 202 | +40.3% |
| 0.50 | 367 | 193 | 105 | 827 | 379 | 152 | +44.8% |

## About the Benchmark

NFCorpus is a publicly available information retrieval benchmark from the BEIR (Benchmarking Information Retrieval) suite, the standard evaluation framework used across the IR research community. The corpus contains biomedical documents sourced from NutritionFacts.org, paired with test queries. Each query has been manually annotated by human judges who identified which documents are relevant and assigned graded relevance scores (0 = not relevant, 1 = partially relevant, 2 = highly relevant).

This design makes the evaluation objective and reproducible: the system retrieves its top-ranked documents for each query, and the results are scored against the pre-established human judgments. A system that ranks the known-relevant documents higher scores better. No subjective interpretation is involved — the ground truth is fixed.

## What Was Tested

**Baseline (control):** Standard RAG — cosine similarity between query embedding and content chunk embeddings. This is how virtually every production RAG system retrieves documents today.

**Multi-surface (experimental):** Four retrieval surfaces operating in parallel, with score fusion:

- **Chunk vector** — cosine similarity on chunk embeddings, title chunk also embedded
- **Chunk summary** — embeddings of LLM-generated chunk summaries, removing noise from raw text
- **Query match** — embeddings of synthesized questions generated at ingestion time
- **Synopsis match** — embeddings of document-level synopses capturing the full document's meaning

## Per-Surface Performance

| Surface | NDCG@10 | vs Baseline | Innovation |
|---------|---------|-------------|------------|
| synopsis_match | 0.3539 | +3.3% | Document-level synopsis embeddings |
| chunk_vector | 0.3513 | +2.5% | Dedicated title chunk as a searchable vector |
| chunk_summary | 0.3510 | +2.5% | LLM-generated chunk summaries as embeddings |
| query_match | 0.3451 | +0.7% | Synthesized question embeddings |
| bm25 | 0.3292 | -3.9% | BM25 |
| **best_novel (max)** | **0.3512** | **+2.5%** | Best ITI surface per document |
| **Weighted fusion** | **0.3699** | **+8.0%** | Score fusion across active surfaces |
| Baseline | 0.3426 | — | Standard RAG |

## Query Pattern Analysis: Where Query Match Excels

The query_match surface performs differently depending on query type. Analyzing the 323 test queries by linguistic pattern reveals where synthesized questions add the most value:

| Query Pattern | Count | Baseline NDCG@10 | Query Match NDCG@10 | QM vs Baseline |
|---------------|-------|------------------|---------------------|----------------|
| "how to" questions | 4 | 0.2693 | 0.3779 | **+40.3%** |
| "is X good/safe" questions | 19 | 0.4004 | 0.4291 | +7.2% |
| Other questions | 21 | 0.3313 | 0.3543 | +6.9% |
| Statement/claim style | 270 | 0.3402 | 0.3402 | 0.0% |
| Comparative ("X vs Y") | 9 | 0.3512 | 0.2804 | **-20.2%** |

**Key insight:** Query match excels on genuine "how to" and evaluative questions (+7% to +40%) but underperforms on comparative queries (-20%). This pattern makes sense: synthesized questions at ingestion time generate "What are the benefits of X?" but not "How does X compare to Y?" Comparative reasoning requires relating multiple concepts, which single-document profiling cannot anticipate.

**Implication for real-world use:** Applications serving "how do I..." and "is X good for..." queries will see the largest benefit from multi-surface retrieval. Applications heavy on comparative queries ("X vs Y", "which is better") may want to reduce query_match weight or add comparative question templates to document profiling.

## Published Benchmark Comparison

Published NDCG@10 scores for nfcorpus from BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models (Thakur et al., NeurIPS 2021), Table 2. Reference data retrieved 2026-03-19.

| System | NDCG@10 | vs BM25 | Type |
|--------|---------|---------|------|
| **Shu multi-surface fusion** | 0.370 | +12.3% | **multi-surface (ours)** |
| BM25+CE | 0.350 | +7.7% | reranker |
| **Shu chunk similarity** | 0.343 | +4.1% | **dense (ours)** |
| **Shu BM25 (measured)** | 0.329 | +0.0% | **lexical (ours)** |
| docT5query | 0.328 | +0.9% | document_expansion |
| BM25 | 0.325 | +0.0% | lexical |
| TAS-B | 0.319 | -1.8% | dense |
| GenQ | 0.319 | -1.8% | dense |
| ColBERT | 0.305 | -6.2% | late_interaction |
| SPARTA | 0.301 | -7.4% | dense |
| DeepCT | 0.283 | -12.9% | lexical |
| ANCE | 0.237 | -27.1% | dense |
| DPR | 0.189 | -41.8% | dense |

### Understanding Relative Improvement

Relative improvement measures how much a system improves over a baseline, expressed as a percentage: `(system_score - baseline_score) / baseline_score × 100`. It answers the question: "By what percentage did this approach improve retrieval quality compared to the standard method?"

All systems in the table above are measured against BM25. Published systems use the published BM25 score; Shu systems use our own measured BM25 baseline run against the same corpus subset, making the comparison direct and fair.

Relative improvement is meaningful even when absolute scores differ, because it captures the *magnitude of the gain* from a new technique applied to the same task under the same conditions.

The largest published relative improvement over BM25 on nfcorpus is **+7.7%** (BM25+CE). Shu's best novel surface achieves **+6.7%** vs our measured BM25 and **+12.3%** for weighted fusion.

## How Published Systems Work

Understanding the methodology of each system contextualizes Shu's approach:

**BM25+CE** (reranker): Two-stage: BM25 retrieves candidate documents, then a cross-encoder (BERT-based) re-ranks them by jointly encoding query and document. High quality but expensive at query time — cross-encoder inference scales linearly with candidate count.

**docT5query** (document_expansion): Document expansion: a T5 model generates predicted queries for each document, which are appended to the document text before indexing with BM25. Similar ingestion-time intelligence concept to Shu but limited to augmenting lexical retrieval rather than creating independent retrieval surfaces.

**BM25** (lexical): Lexical term-matching using TF-IDF weighting. No semantic understanding — relies entirely on exact and partial keyword overlap between query and document. The universal baseline for IR evaluation.

**TAS-B** (dense): Topic-Aware Sampling with Balanced training. Dense bi-encoder distilled from a cross-encoder teacher using topic-aware sampling of training pairs. Single embedding per document — no document-level understanding beyond what the embedding captures.

**GenQ** (dense): Generates synthetic queries from passages using a T5 model, then fine-tunes a bi-encoder on the (query, passage) pairs. Query generation happens at training time to create training data — queries are discarded after training. The resulting model is corpus-specific: new corpus requires retraining. Shu's query_match surface uses a similar concept but stores queries as persistent, searchable artifacts at ingestion time — no model retraining needed, and new documents are immediately searchable.

**ColBERT** (late_interaction): Late interaction model that encodes queries and documents into multiple token-level embeddings and computes relevance via MaxSim (maximum similarity between each query token and all document tokens). Higher quality than single-vector but requires storing per-token embeddings for every document, increasing storage 100-200x.

**SPARTA** (dense): Sparse Transformer Matching. Learns sparse representations from transformer encoders for efficient retrieval. Balances between dense and sparse approaches.

**DeepCT** (lexical): Deep Contextualized Term weighting. Uses BERT to estimate term importance for each passage, replacing raw term frequency in the BM25 formula. Enhances lexical retrieval with learned term weights but still fundamentally keyword-based.

**ANCE** (dense): Approximate Nearest Neighbor Negative Contrastive Estimation. Dense retriever that uses hard negatives from an ANN index during training to improve embedding quality. Better than DPR on zero-shot but still a single-vector dense retriever.

**DPR** (dense): Dense Passage Retrieval. Dual-encoder architecture with separate BERT encoders for queries and passages, trained on Natural Questions. Retrieval via cosine similarity of pre-computed embeddings. Poor zero-shot transfer to out-of-domain corpora.

**Shu Multi-Surface** (ingestion-time intelligence): At document ingestion, an LLM reads each document and generates multiple retrieval artifacts — chunk summaries, document synopses, and synthesized questions. Each artifact type becomes an independent retrieval surface with its own embeddings. At query time, all surfaces are searched in parallel and the best-scoring surface per document determines the ranking. Unlike GenQ, no model retraining is needed — new documents are immediately searchable. Unlike BM25+CE, no expensive cross-encoder inference runs at query time.

## Methodology

- **Corpus:** nfcorpus, a standard BEIR benchmark
- **Evaluation library:** ranx (published in ECIR, validated against TREC Eval)
- **Embedding model:** Snowflake/snowflake-arctic-embed-l-v2.0
- **Profiling model:** Claude Haiku 4.5 (claude-haiku-4-5-20251001)
- **Statistical test:** Paired t-test, significance threshold p < 0.05

## Limitations and Next Steps

**Corpus subset:** A 323-query evaluation against 3,633 documents.

**Query vocabulary gap:** NFCorpus queries are expert-authored topic phrases that closely match document vocabulary. This underrepresents the query synthesis surface's primary value proposition: bridging the gap between layperson questions and expert documents.

**Weight tuning:** Current results use equal weights across active surfaces. Data-driven weight optimization would likely improve fusion performance further. The Query Pattern Analysis above suggests a specific hypothesis: dynamically increasing query_match weight for natural language questions (detected via "how to", "is X good", interrogative patterns) could yield significant gains, given the +40% improvement observed on "how to" queries. Conversely, reducing query_match weight for comparative queries ("X vs Y") may prevent the -20% degradation observed on that pattern.

## Citations

This evaluation uses the BEIR benchmark framework and datasets. If referencing these results, please cite:

Thakur, N., Reimers, N., Rücklé, A., Srivastava, A., & Gurevych, I. (2021). "BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models." NeurIPS 2021 (Datasets and Benchmarks Track). https://openreview.net/forum?id=wCu6T5xFjeJ

Thakur, N., Reimers, N., Rücklé, A., Srivastava, A., & Gurevych, I. (2024). "Resources for Brewing BEIR: Reproducible Reference Models and an Official Leaderboard." SIGIR 2024 (Resource Track). https://dl.acm.org/doi/10.1145/3626772.3657862

Evaluation metrics computed using ranx: Bassani, E. (2022). "ranx: A Blazing-Fast Python Library for Ranking Evaluation and Comparison." ECIR 2022. https://github.com/AmenRa/ranx
