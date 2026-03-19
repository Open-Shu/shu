"""Main benchmark orchestrator.

Coordinates the full evaluation pipeline: load dataset, ingest corpus,
run searches, compute metrics, and generate reports.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .beir_loader import BeirDataset, BeirLoader
from .corpus_ingestor import EMBEDDED_STATUSES, PROFILED_STATUSES, CorpusIngestor
from .result_collector import CollectionStats, ResultCollector, SearchConfig

if TYPE_CHECKING:
    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkConfig:
    """Configuration for a benchmark run."""

    dataset_dir: Path
    dataset_name: str = ""
    kb_name: str = ""
    reuse_kb_id: str | None = None
    target_status: str = "profile_processed"
    ingestion_timeout: float = 3600
    poll_interval: float = 5.0
    search_limit: int = 100
    search_threshold: float = 0.0
    metrics: list[str] = field(default_factory=lambda: [
        "precision@5",
        "precision@10",
        "recall@5",
        "recall@10",
        "mrr@10",
        "ndcg@5",
        "ndcg@10",
        "map@10",
    ])
    stat_test: str = "student"
    max_p: float = 0.05
    exclude_surfaces: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.dataset_name:
            self.dataset_name = self.dataset_dir.name
        if not self.kb_name:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            self.kb_name = f"benchmark-{self.dataset_name}-{ts}"

    @property
    def target_statuses(self) -> frozenset[str]:
        if self.target_status == "profile_processed":
            return PROFILED_STATUSES
        return EMBEDDED_STATUSES


@dataclass
class BenchmarkResults:
    """Results from a benchmark run."""

    dataset_name: str
    corpus_size: int
    query_count: int
    baseline_scores: dict[str, float]
    multi_surface_scores: dict[str, float]
    deltas: dict[str, float]
    stat_tests: dict[str, dict[str, Any]]
    comparison_table: str
    baseline_stats: CollectionStats
    multi_surface_stats: CollectionStats
    config: BenchmarkConfig
    kb_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    ingestion_time_s: float = 0.0

    # Model provenance — critical for reproducibility
    embedding_model: str = ""
    profiling_model: str = ""

    # Per-surface solo scores: {surface_name: {metric: score}}
    per_surface_scores: dict[str, dict[str, float]] = field(default_factory=dict)
    # Best novel surface (max across query_match, synopsis_match, chunk_summary per doc)
    best_novel_scores: dict[str, float] = field(default_factory=dict)

    # BM25 scores (extracted from per-surface evaluation of the bm25 surface)
    bm25_scores: dict[str, float] = field(default_factory=dict)

    # Raw run data for reproducibility
    baseline_run_dict: dict[str, dict[str, float]] = field(default_factory=dict)
    multi_surface_run_dict: dict[str, dict[str, float]] = field(default_factory=dict)
    multi_surface_surface_scores: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    qrels_dict: dict[str, dict[str, int]] = field(default_factory=dict)


class BenchmarkRunner:
    """Orchestrates the full benchmark pipeline."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        db: AsyncSession,
        auth_headers: dict[str, str],
        config: BenchmarkConfig,
    ):
        self.client = client
        self.db = db
        self.auth_headers = auth_headers
        self.config = config

    async def run(self) -> BenchmarkResults:
        """Execute the full benchmark pipeline.

        Steps:
            1. Load BEIR dataset
            2. Create KB and ingest corpus (or reuse existing)
            3. Wait for all documents to reach target processing status
            4. Run all queries through similarity search (baseline)
            5. Run all queries through multi-surface search (experimental)
            6. Build ranx Qrels and Run objects
            7. Compute metrics and statistical comparison
            8. Return structured results
        """
        # 1. Load dataset — only queries that have ground-truth relevance judgments
        logger.info("Loading BEIR dataset from %s", self.config.dataset_dir)
        loader = BeirLoader(self.config.dataset_dir, name=self.config.dataset_name)
        dataset = loader.load()
        logger.info(dataset.summary())

        # Filter to only queries with qrels (others have no ground truth to evaluate against)
        queries_with_qrels = {qid: dataset.queries[qid] for qid in dataset.qrels if qid in dataset.queries}
        if len(queries_with_qrels) < len(dataset.queries):
            logger.info(
                "Filtered to %d queries with qrels (from %d total)",
                len(queries_with_qrels),
                len(dataset.queries),
            )
            dataset.queries = queries_with_qrels

        # 2. Ingest or reuse KB
        ingestion_start = time.monotonic()
        if self.config.reuse_kb_id:
            kb_id = self.config.reuse_kb_id
            logger.info("Reusing existing KB: %s", kb_id)
        else:
            kb_id = await self._create_kb()
            logger.info("Created benchmark KB: %s (name=%s)", kb_id, self.config.kb_name)
            await self._ingest_corpus(kb_id, dataset)

        ingestion_time = time.monotonic() - ingestion_start

        # 3. Build ID map (only profiled documents when target is profile_processed)
        ingestor = CorpusIngestor(self.db, kb_id, user_id="benchmark")
        profiled_only = self.config.target_status == "profile_processed"
        id_map = await ingestor.build_id_map(profiled_only=profiled_only)
        logger.info("ID map has %d entries", len(id_map))

        if not id_map:
            raise RuntimeError(f"No documents found in KB {kb_id} with benchmark source IDs")

        # Filter qrels and queries to only include profiled documents
        profiled_beir_ids = set(id_map.values())
        original_query_count = len(dataset.queries)
        original_qrel_docs = sum(len(docs) for docs in dataset.qrels.values())

        filtered_qrels: dict[str, dict[str, int]] = {}
        for qid, docs in dataset.qrels.items():
            filtered_docs = {did: rel for did, rel in docs.items() if did in profiled_beir_ids}
            if filtered_docs:
                filtered_qrels[qid] = filtered_docs
        dataset.qrels = filtered_qrels

        # Keep only queries that still have qrels after filtering
        dataset.queries = {qid: q for qid, q in dataset.queries.items() if qid in dataset.qrels}

        filtered_qrel_docs = sum(len(docs) for docs in dataset.qrels.values())
        logger.info(
            "Filtered to profiled documents: %d/%d qrel docs, %d/%d queries",
            filtered_qrel_docs,
            original_qrel_docs,
            len(dataset.queries),
            original_query_count,
        )

        # 4. Collect baseline run
        collector = ResultCollector(self.client, kb_id, self.auth_headers)
        search_cfg = SearchConfig(limit=self.config.search_limit, threshold=self.config.search_threshold)

        logger.info("Running baseline similarity search (%d queries)...", dataset.query_count)
        baseline_run_dict, baseline_stats = await collector.collect_similarity_run(
            dataset.queries, id_map, search_cfg,
        )

        # 5. Collect multi-surface run (with excluded surfaces zeroed out)
        weight_overrides: dict[str, float] = {}
        surface_weight_params = {
            "chunk_vector": "chunk_vector_weight",
            "chunk_summary": "chunk_summary_weight",
            "query_match": "query_match_weight",
            "synopsis_match": "synopsis_match_weight",
            "bm25": "bm25_weight",
        }
        for surface in self.config.exclude_surfaces:
            if surface in surface_weight_params:
                weight_overrides[surface_weight_params[surface]] = 0.0
                logger.info("Excluding surface: %s (weight=0)", surface)

        ms_search_cfg = SearchConfig(
            limit=self.config.search_limit,
            threshold=self.config.search_threshold,
            weight_overrides=weight_overrides or None,
        )

        logger.info("Running multi-surface search (%d queries)...", dataset.query_count)
        ms_run_dict, ms_surface_scores, ms_stats = await collector.collect_multi_surface_run(
            dataset.queries, id_map, ms_search_cfg,
        )

        # 6. BM25 scores come from the per-surface evaluation below (no separate run needed —
        #    the BM25 surface runs as part of multi-surface search via the API)

        # 7. Capture model provenance
        embedding_model, profiling_model = await self._get_model_provenance(kb_id)

        # 8. Evaluate per-surface rankings from surface_scores (no extra API calls)
        logger.info("Evaluating per-surface rankings...")
        per_surface_scores, best_novel_scores = self._evaluate_per_surface(
            dataset.qrels, ms_surface_scores,
        )

        # Extract BM25 scores from per-surface evaluation (no separate in-memory run needed)
        bm25_scores = per_surface_scores.get("bm25", {m: 0.0 for m in self.config.metrics})

        # 9. Compute aggregate metrics
        logger.info("Computing IR metrics...")
        baseline_scores, ms_scores, deltas, stat_tests, comparison_table = self._evaluate(
            dataset.qrels, baseline_run_dict, ms_run_dict,
        )

        return BenchmarkResults(
            dataset_name=dataset.name,
            corpus_size=dataset.corpus_size,
            query_count=dataset.query_count,
            baseline_scores=baseline_scores,
            multi_surface_scores=ms_scores,
            deltas=deltas,
            stat_tests=stat_tests,
            comparison_table=comparison_table,
            baseline_stats=baseline_stats,
            multi_surface_stats=ms_stats,
            config=self.config,
            kb_id=kb_id,
            ingestion_time_s=ingestion_time,
            embedding_model=embedding_model,
            profiling_model=profiling_model,
            per_surface_scores=per_surface_scores,
            best_novel_scores=best_novel_scores,
            bm25_scores=bm25_scores,
            baseline_run_dict=baseline_run_dict,
            multi_surface_run_dict=ms_run_dict,
            multi_surface_surface_scores=ms_surface_scores,
            qrels_dict=dataset.qrels,
        )

    async def _get_model_provenance(self, kb_id: str) -> tuple[str, str]:
        """Query the embedding and profiling model names from the database.

        These are critical for reproducibility — the same retrieval architecture
        will produce different results with different models.

        Args:
            kb_id: Knowledge base ID to read embedding model from.

        Returns:
            Tuple of (embedding_model_name, profiling_model_name).
        """
        from sqlalchemy import text as sa_text

        embedding_model = "unknown"
        profiling_model = "not configured"

        try:
            result = await self.db.execute(
                sa_text("SELECT embedding_model FROM knowledge_bases WHERE id = :kb_id"),
                {"kb_id": kb_id},
            )
            row = result.first()
            if row and row[0]:
                embedding_model = row[0]
        except Exception as e:
            logger.warning("Could not determine embedding model: %s", e)

        try:
            result = await self.db.execute(
                sa_text("""
                    SELECT mc.name, mc.model_name
                    FROM system_settings ss
                    JOIN model_configurations mc
                      ON mc.id::text = ss.value->>'model_config_id'
                    WHERE ss.key = 'profiling_model_config_id'
                      AND mc.is_active = true
                    LIMIT 1
                """)
            )
            row = result.first()
            if row:
                profiling_model = f"{row[0]} ({row[1]})"
        except Exception as e:
            logger.warning("Could not determine profiling model: %s", e)

        logger.info("Model provenance: embedding=%s, profiling=%s", embedding_model, profiling_model)
        return embedding_model, profiling_model

    async def _create_kb(self) -> str:
        """Create a benchmark knowledge base via the API."""
        from tests.integ.response_utils import extract_data

        resp = await self.client.post(
            "/api/v1/knowledge-bases",
            json={
                "name": self.config.kb_name,
                "description": f"Benchmark evaluation KB ({self.config.dataset_name})",
                "sync_enabled": True,
            },
            headers=self.auth_headers,
        )
        if resp.status_code != 201:
            raise RuntimeError(f"Failed to create KB: {resp.status_code} {resp.text}")
        return extract_data(resp)["id"]

    async def _ingest_corpus(self, kb_id: str, dataset: BeirDataset) -> None:
        """Ingest the BEIR corpus and wait for processing."""
        ingestor = CorpusIngestor(self.db, kb_id, user_id="benchmark")

        def _log_ingestion(completed: int, total: int) -> None:
            if completed % 100 == 0 or completed == total:
                logger.info("Ingestion progress: %d / %d documents", completed, total)

        summary = await ingestor.ingest_corpus(dataset.corpus, progress_callback=_log_ingestion)
        logger.info(
            "Ingestion complete: %d ingested, %d skipped, %d failed",
            summary.ingested,
            summary.skipped,
            summary.failed,
        )

        if summary.failed > 0:
            logger.warning("%d documents failed ingestion", summary.failed)

        # Wait for processing
        def _log_processing(done: int, total: int, status_counts: dict[str, int]) -> None:
            logger.info("Processing: %d / %d done — %s", done, total, status_counts)

        logger.info(
            "Waiting for documents to reach '%s' (timeout: %.0fs)...",
            self.config.target_status,
            self.config.ingestion_timeout,
        )
        await ingestor.wait_for_processing(
            target_statuses=self.config.target_statuses,
            timeout=self.config.ingestion_timeout,
            poll_interval=self.config.poll_interval,
            progress_callback=_log_processing,
        )

    def _evaluate_single_run(
        self,
        qrels_dict: dict[str, dict[str, int]],
        run_dict: dict[str, dict[str, float]],
        name: str,
    ) -> dict[str, float]:
        """Evaluate a single run against qrels. Returns {metric: score}."""
        from ranx import Qrels, Run, evaluate

        qrels = Qrels(qrels_dict, name="ground_truth")
        run = Run(run_dict, name=name)
        scores = evaluate(qrels, run, self.config.metrics, make_comparable=True)
        logger.info("  %s: NDCG@10=%.4f  P@10=%.4f  MRR@10=%.4f", name, scores.get("ndcg@10", 0), scores.get("precision@10", 0), scores.get("mrr@10", 0))
        return scores

    def _evaluate_per_surface(
        self,
        qrels_dict: dict[str, dict[str, int]],
        surface_scores: dict[str, dict[str, dict[str, float]]],
    ) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
        """Evaluate each surface independently and compute best-novel-surface ranking.

        Uses per-surface scores from the multi-surface search response to rank
        documents by each surface's score alone. No extra API calls needed.

        Novel surfaces: chunk_summary, query_match, synopsis_match
        (excludes chunk_vector as baseline equivalent and bm25 as prior art)

        Returns:
            Tuple of:
                - {surface_name: {metric: score}} for each surface
                - {metric: score} for best-novel-surface (max across novel surfaces per doc)
        """
        from ranx import Qrels, Run, evaluate

        qrels = Qrels(qrels_dict, name="ground_truth")

        surfaces = ["chunk_vector", "chunk_summary", "query_match", "synopsis_match", "bm25"]
        novel_surfaces = ["chunk_summary", "query_match", "synopsis_match"]

        per_surface_results: dict[str, dict[str, float]] = {}

        for surface in surfaces:
            run_dict: dict[str, dict[str, float]] = {}
            for query_id, doc_surfaces in surface_scores.items():
                query_docs: dict[str, float] = {}
                for doc_id, scores in doc_surfaces.items():
                    score = scores.get(surface, 0.0)
                    if score > 0:
                        query_docs[doc_id] = score
                if query_docs:
                    run_dict[query_id] = query_docs

            if run_dict:
                run = Run(run_dict, name=f"solo_{surface}")
                scores = evaluate(qrels, run, self.config.metrics, make_comparable=True)
            else:
                scores = {m: 0.0 for m in self.config.metrics}

            per_surface_results[surface] = scores
            logger.info("  %s: NDCG@10=%.4f  P@10=%.4f", surface, scores.get("ndcg@10", 0), scores.get("precision@10", 0))

        # Best novel surface: for each query+doc, take max score across novel surfaces
        best_novel_run: dict[str, dict[str, float]] = {}
        for query_id, doc_surfaces in surface_scores.items():
            query_docs: dict[str, float] = {}
            for doc_id, scores in doc_surfaces.items():
                best = max((scores.get(s, 0.0) for s in novel_surfaces), default=0.0)
                if best > 0:
                    query_docs[doc_id] = best
            if query_docs:
                best_novel_run[query_id] = query_docs

        if best_novel_run:
            run = Run(best_novel_run, name="best_novel_surface")
            best_novel_scores = evaluate(qrels, run, self.config.metrics, make_comparable=True)
        else:
            best_novel_scores = {m: 0.0 for m in self.config.metrics}

        logger.info("  best_novel: NDCG@10=%.4f  P@10=%.4f", best_novel_scores.get("ndcg@10", 0), best_novel_scores.get("precision@10", 0))

        return per_surface_results, best_novel_scores

    def _evaluate(
        self,
        qrels_dict: dict[str, dict[str, int]],
        baseline_run_dict: dict[str, dict[str, float]],
        ms_run_dict: dict[str, dict[str, float]],
    ) -> tuple[dict[str, float], dict[str, float], dict[str, float], dict[str, dict[str, Any]], str]:
        """Compute metrics and statistical comparison using ranx.

        Returns:
            Tuple of (baseline_scores, ms_scores, deltas, stat_tests, comparison_table).
        """
        from ranx import Qrels, Run, compare, evaluate

        qrels = Qrels(qrels_dict, name="ground_truth")
        baseline_run = Run(baseline_run_dict, name="similarity_baseline")
        ms_run = Run(ms_run_dict, name="multi_surface")

        # Compute individual metrics
        # make_comparable=True handles queries with no results (adds empty entries)
        baseline_scores = evaluate(qrels, baseline_run, self.config.metrics, make_comparable=True)
        ms_scores = evaluate(qrels, ms_run, self.config.metrics, make_comparable=True)

        # Compute deltas
        deltas = {}
        for metric in self.config.metrics:
            b = baseline_scores.get(metric, 0.0)
            m = ms_scores.get(metric, 0.0)
            deltas[metric] = ((m - b) / b * 100) if b > 0 else 0.0

        # Statistical comparison
        comparison_table = ""
        stat_tests: dict[str, dict[str, Any]] = {}
        try:
            report = compare(
                qrels,
                [baseline_run, ms_run],
                self.config.metrics,
                stat_test=self.config.stat_test,
                max_p=self.config.max_p,
                make_comparable=True,
            )
            comparison_table = str(report)
        except Exception as e:
            logger.warning("Statistical comparison failed: %s", e)
            comparison_table = f"(comparison failed: {e})"

        return baseline_scores, ms_scores, deltas, stat_tests, comparison_table
