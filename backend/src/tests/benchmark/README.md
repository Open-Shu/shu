# Shu RAG Accuracy Benchmark

Measures multi-surface retrieval accuracy against standard IR benchmarks (BEIR) with statistical rigor. Compares against BM25 (lexical baseline), chunk similarity (dense retrieval baseline), and published retrieval systems.

## Prerequisites

- Shu backend running with worker (`make up-dev` or equivalent)
- `SHU_ENABLE_DOCUMENT_PROFILING=true` for multi-surface evaluation
- A configured profiling model (LM Studio local or cloud API)
- Python dependencies: `pip install ranx rank-bm25`

## Quick Start

```bash
cd shu/backend/src

# 1. Download a BEIR dataset
python -m tests.benchmark.download_datasets --dataset nfcorpus

# 2. Run the benchmark (ingests, profiles, searches, evaluates)
python -m tests.benchmark.run_benchmark --dataset nfcorpus

# 3. View results
cat tests/benchmark/.results/benchmark_nfcorpus_*_executive_summary.md
```

## Commands

### Download Datasets

```bash
python -m tests.benchmark.download_datasets --dataset nfcorpus
python -m tests.benchmark.download_datasets --dataset scifact
python -m tests.benchmark.download_datasets --dataset all
```

Downloads to `tests/benchmark/.datasets/` (gitignored).

### Run Benchmark

```bash
# Full run: ingest + profile + search + evaluate
python -m tests.benchmark.run_benchmark --dataset nfcorpus

# Reuse an existing KB (skip ingestion — much faster)
python -m tests.benchmark.run_benchmark --dataset nfcorpus --reuse-kb <kb-id>

# Exclude keyword_match surface (recommended — it's not a novel contribution)
python -m tests.benchmark.run_benchmark --dataset nfcorpus --reuse-kb <kb-id> --exclude-surface keyword_match

# Skip ablation study
python -m tests.benchmark.run_benchmark --dataset nfcorpus --reuse-kb <kb-id> --skip-ablation

# Wait for content_processed only (skip profiling wait — faster but only chunk_vector has data)
python -m tests.benchmark.run_benchmark --dataset nfcorpus --target-status content_processed

# Custom output directory
python -m tests.benchmark.run_benchmark --dataset nfcorpus --output-dir /path/to/output

# Verbose logging
python -m tests.benchmark.run_benchmark --dataset nfcorpus --verbose
```

### Monitor Profiling Progress

When ingesting a large corpus, profiling takes time. Monitor progress:

```bash
python scripts/.internal/monitor_profiling.py
```

Or use the SQL query with `watch`:

```bash
watch -n 10 'psql -d shu -c "
  WITH stats AS (
    SELECT
      count(*) FILTER (WHERE profiling_status = '\''complete'\'') AS profiled,
      count(*) AS total,
      min(updated_at) FILTER (WHERE profiling_status = '\''complete'\'') AS first_done,
      max(updated_at) FILTER (WHERE profiling_status = '\''complete'\'') AS last_done
    FROM documents
    WHERE knowledge_base_id = (
      SELECT id FROM knowledge_bases
      WHERE name LIKE '\''benchmark-nfcorpus%'\''
      ORDER BY created_at DESC LIMIT 1
    )
  )
  SELECT profiled, total,
    round(100.0 * profiled / total, 1) AS pct,
    round(EXTRACT(EPOCH FROM (last_done - first_done)) / NULLIF(profiled - 1, 0), 1) AS secs_per_doc,
    round((total - profiled) * EXTRACT(EPOCH FROM (last_done - first_done)) / NULLIF(profiled - 1, 0) / 60, 1) AS eta_minutes
  FROM stats;
"'
```

### Re-queue Stuck Profiling Jobs

If profiling is interrupted (machine sleep, LM Studio crash), documents get stuck in `profiling` status:

```bash
# See what's stuck
python backend/scripts/.internal/requeue_profiling.py <kb-id> --dry-run

# Re-queue them
python backend/scripts/.internal/requeue_profiling.py <kb-id>

# Also re-queue errored documents
python backend/scripts/.internal/requeue_profiling.py <kb-id> --include-errors
```

## Output Files

Each benchmark run produces four files in `.results/`:

| File | Description |
|------|-------------|
| `*_executive_summary.md` | Human-readable report with metrics, leaderboard comparison, methodology descriptions, and citations |
| `*_report.json` | Machine-readable structured results |
| `*_report.txt` | Compact text report with tables |
| `*_runs.json` | Raw ranx run data (query → document → score) for reproducibility |

## What Gets Measured

### Three Baselines

- **BM25** — lexical term matching (universal IR baseline, run in-memory against the BEIR corpus)
- **Chunk similarity** — cosine similarity on chunk embeddings (standard RAG baseline, via Shu API)
- **Multi-surface** — four retrieval surfaces with score fusion (experimental, via Shu API)

### Metrics

All metrics computed by [ranx](https://github.com/AmenRa/ranx) against BEIR ground-truth relevance judgments:

- **NDCG@10** — ranking quality of top 10 results (graded relevance)
- **Precision@10** — fraction of top 10 that are relevant
- **Recall@10** — fraction of all relevant docs found in top 10
- **MRR@10** — how high the first relevant result ranks
- **MAP@10** — average precision across all relevant docs
- **Statistical significance** — paired t-test (p < 0.05)

### Per-Surface Analysis

Each surface is evaluated independently using the `surface_scores` returned by multi-surface search — no extra API calls needed:

- **chunk_vector** — chunk embeddings (includes title chunk)
- **chunk_summary** — LLM-generated chunk summary embeddings
- **query_match** — synthesized question embeddings
- **synopsis_match** — document-level synopsis embeddings
- **best_novel** — max score across chunk_summary, query_match, synopsis_match per document

## Available Datasets

| Dataset | Documents | Queries | Domain | Best For |
|---------|-----------|---------|--------|----------|
| **nfcorpus** | 3,633 | 323 | Biomedical | General evaluation (expert-authored queries) |
| **scifact** | 5,183 | 300 | Scientific claims | Claim verification, plain language → technical docs |
| **fiqa** | 57,638 | 648 | Financial QA | Vocabulary gap (casual questions → expert docs) |
| **test_subset** | 50 | 10 | Biomedical (synthetic) | Quick framework validation |

## Architecture

```
tests/benchmark/
├── README.md                    # This file
├── run_benchmark.py             # CLI entry point
├── benchmark_runner.py          # Main orchestrator
├── beir_loader.py               # BEIR format parser
├── corpus_ingestor.py           # Ingests docs via ingest_text()
├── result_collector.py          # Runs searches via API, collects ranx Runs
├── report_generator.py          # JSON, text, and executive summary output
├── ablation_runner.py           # Surface contribution analysis
├── query_classifier.py          # Heuristic query type classification
├── beir_reference_scores.py     # Published BEIR scores and methodologies
├── download_datasets.py         # Dataset download utility
├── .datasets/                   # Downloaded corpora (gitignored)
│   ├── nfcorpus/
│   ├── test_subset/
│   └── ...
└── .results/                    # Benchmark output (gitignored)
    ├── benchmark_nfcorpus_*_executive_summary.md
    ├── benchmark_nfcorpus_*_report.json
    ├── benchmark_nfcorpus_*_report.txt
    └── benchmark_nfcorpus_*_runs.json
```
