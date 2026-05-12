"""Main benchmark orchestrator.

Coordinates the full evaluation pipeline: load dataset, ingest corpus,
run searches, compute metrics, and generate reports.
"""

from __future__ import annotations

from shu.core.logging import get_logger
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

logger = get_logger(__name__)


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
    weight_overrides: dict[str, float] = field(default_factory=dict)
    fusion_formula_override: str | None = None
    qrels_split: str = "test"

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

    # Model and scoring provenance — critical for reproducibility
    embedding_model: str = ""
    profiling_model: str = ""
    fusion_formula: str = ""

    # Per-surface solo scores: {surface_name: {metric: score}}
    per_surface_scores: dict[str, dict[str, float]] = field(default_factory=dict)
    # Best novel surface (max across query_match, synopsis_match, chunk_summary per doc)
    best_novel_scores: dict[str, float] = field(default_factory=dict)

    # BM25 scores (from ParadeDB BM25 surface, extracted from per-surface evaluation)
    bm25_scores: dict[str, float] = field(default_factory=dict)

    # Head-to-head: per-query comparison of score-2 (highly relevant) documents in top-10
    # This metric directly measures answer utility — which strategy surfaces more
    # documents that directly help answer the query?
    head_to_head: dict[str, Any] = field(default_factory=dict)

    # Threshold analysis: how many relevant documents survive at practical
    # score thresholds? This measures what users actually experience.
    threshold_analysis: dict[str, Any] = field(default_factory=dict)

    # Surface contribution analysis (computed locally from all_surface_scores)
    # Fusion impact: {surface_removed: {metric: score}} — what happens to fused
    # ranking when a surface is zeroed out, recomputed locally from surface scores.
    # Contribution matrix: {query_type: {surface: avg_fraction_of_fused_score}}
    contribution_matrix: dict[str, dict[str, float]] = field(default_factory=dict)
    # Effective surface weights used for this run (defaults + overrides + exclusions)
    effective_weights: dict[str, float] = field(default_factory=dict)
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
        dataset = loader.load(qrels_split=self.config.qrels_split)
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

        # 5. Collect multi-surface run (with weight overrides)
        weight_overrides: dict[str, float] = {}
        surface_weight_params = {
            "chunk_vector": "chunk_vector_weight",
            "chunk_summary": "chunk_summary_weight",
            "query_match": "query_match_weight",
            "synopsis_match": "synopsis_match_weight",
            "bm25": "bm25_weight",
        }
        # Apply explicit weight overrides first
        for surface, weight in self.config.weight_overrides.items():
            if surface in surface_weight_params:
                weight_overrides[surface_weight_params[surface]] = weight
                logger.info("Weight override: %s = %.2f", surface, weight)
        # Then apply exclusions (zeroing takes precedence)
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
        ms_run_dict, ms_surface_scores, ms_stats, ms_all_surface_scores = await collector.collect_multi_surface_run(
            dataset.queries, id_map, ms_search_cfg,
        )

        # 6. Capture model and scoring provenance
        embedding_model, profiling_model = await self._get_model_provenance(kb_id)
        fusion_formula = self._get_fusion_formula()

        # 7. Evaluate per-surface rankings using untruncated surface scores
        #    (all documents scored by any surface, not just fused top-k).
        #    BM25 baseline comes from the ParadeDB BM25 surface in the multi-surface run.
        logger.info("Evaluating per-surface rankings (untruncated: %d queries)...", len(ms_all_surface_scores))
        per_surface_scores, best_novel_scores = self._evaluate_per_surface(
            dataset.qrels, ms_all_surface_scores if ms_all_surface_scores else ms_surface_scores,
        )

        # Extract BM25 baseline from per-surface evaluation (ParadeDB BM25, not a separate run)
        bm25_scores = per_surface_scores.get("bm25", {m: 0.0 for m in self.config.metrics})

        # 8b. Compute effective weights and eval surface scores (needed for
        #     fusion override, contribution analysis, and fusion impact).
        effective_weights = self._get_effective_weights()
        eval_surface_scores = ms_all_surface_scores if ms_all_surface_scores else ms_surface_scores

        # 8c. If fusion formula override is set, recompute fused rankings locally
        #     from the collected surface scores using the alternative formula/weights.
        if self.config.fusion_formula_override:
            from shu.services.retrieval.score_fusion import _FUSION_FUNCTIONS

            override_formula = self.config.fusion_formula_override
            if override_formula not in _FUSION_FUNCTIONS:
                raise ValueError(
                    f"Unknown fusion formula '{override_formula}'. "
                    f"Supported: {list(_FUSION_FUNCTIONS.keys())}"
                )
            fuse_fn = _FUSION_FUNCTIONS[override_formula]
            override_weights = effective_weights

            logger.info(
                "Recomputing fusion locally: formula=%s, weights=%s",
                override_formula, override_weights,
            )

            # Debug: inspect first query's surface scores
            if eval_surface_scores:
                sample_qid = next(iter(eval_surface_scores))
                sample_docs = eval_surface_scores[sample_qid]
                if sample_docs:
                    sample_doc = next(iter(sample_docs))
                    logger.info(
                        "Sample surface scores: query=%s doc=%s scores=%s",
                        sample_qid, sample_doc, sample_docs[sample_doc],
                    )

            recomputed_run: dict[str, dict[str, float]] = {}
            for query_id, doc_scores_map in eval_surface_scores.items():
                query_docs: dict[str, float] = {}
                for doc_id, scores in doc_scores_map.items():
                    fused = fuse_fn(scores, override_weights)
                    if fused > 0:
                        query_docs[doc_id] = fused
                if query_docs:
                    recomputed_run[query_id] = query_docs

            ms_run_dict = recomputed_run
            fusion_formula = override_formula
            logger.info(
                "Recomputed %d queries (of %d with surface scores) with %s fusion",
                len(ms_run_dict), len(eval_surface_scores), override_formula,
            )

        # 9. Compute aggregate metrics
        logger.info("Computing IR metrics...")
        baseline_scores, ms_scores, deltas, stat_tests, comparison_table = self._evaluate(
            dataset.qrels, baseline_run_dict, ms_run_dict,
        )

        # 10. Compute head-to-head answer-utility comparison
        logger.info("Computing head-to-head answer-utility comparison...")
        head_to_head = self._compute_head_to_head(
            dataset.qrels, baseline_run_dict, ms_run_dict,
        )

        # 11. Compute threshold analysis
        logger.info("Computing threshold analysis...")
        threshold_analysis = self._compute_threshold_analysis(
            dataset.qrels, baseline_run_dict, ms_run_dict,
        )

        # 12. Compute surface contribution analysis (locally from surface scores)
        logger.info("Computing contribution matrix by query type...")
        contribution_matrix = self._compute_contribution_matrix(
            eval_surface_scores, dataset.queries, effective_weights,
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
            fusion_formula=fusion_formula,
            per_surface_scores=per_surface_scores,
            best_novel_scores=best_novel_scores,
            bm25_scores=bm25_scores,
            head_to_head=head_to_head,
            threshold_analysis=threshold_analysis,
            contribution_matrix=contribution_matrix,
            effective_weights=effective_weights,
            baseline_run_dict=baseline_run_dict,
            multi_surface_run_dict=ms_run_dict,
            multi_surface_surface_scores=ms_all_surface_scores if ms_all_surface_scores else ms_surface_scores,
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

    @staticmethod
    def _get_fusion_formula() -> str:
        """Read the current default fusion formula."""
        from shu.services.retrieval.score_fusion import DEFAULT_FUSION_FORMULA

        return DEFAULT_FUSION_FORMULA

    def _get_effective_weights(self) -> dict[str, float]:
        """Read the current default surface weights, applying overrides and exclusions."""
        from shu.services.retrieval.score_fusion import DEFAULT_SURFACE_WEIGHTS

        weights = dict(DEFAULT_SURFACE_WEIGHTS)
        # Apply explicit weight overrides
        for surface, weight in self.config.weight_overrides.items():
            if surface in weights:
                weights[surface] = weight
        # Apply exclusions (zeroing takes precedence)
        for surface in self.config.exclude_surfaces:
            if surface in weights:
                weights[surface] = 0.0
        return weights

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
        When all_surface_scores is available (untruncated), this evaluates
        on ALL documents scored by any surface, not just the fused top-k.

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

            # Extract structured stat test data from the Report object
            if hasattr(report, "comparisons") and report.comparisons:
                for run_pair, metric_results in report.comparisons.items():
                    pair_key = " vs ".join(sorted(run_pair))
                    stat_tests[pair_key] = {
                        k: v for k, v in metric_results.items()
                    }
        except Exception as e:
            logger.warning("Statistical comparison failed: %s", e)
            comparison_table = f"(comparison failed: {e})"

        return baseline_scores, ms_scores, deltas, stat_tests, comparison_table

    @staticmethod
    def _compute_head_to_head(
        qrels_dict: dict[str, dict[str, int]],
        baseline_run_dict: dict[str, dict[str, float]],
        ms_run_dict: dict[str, dict[str, float]],
        k: int = 10,
    ) -> dict[str, Any]:
        """Compute head-to-head answer-utility comparison.

        For each query, counts documents at the maximum relevance level in each
        strategy's top-k. The strategy with more top-relevance docs wins that query.
        Adapts to both graded (max=2) and binary (max=1) qrels.

        This metric directly measures which strategy surfaces more documents
        that would help an LLM answer the user's question — the core value
        proposition of multi-surface retrieval.

        Returns:
            Dict with ms_wins, bl_wins, ties, decided, ms_win_pct,
            total_bl_score2, total_ms_score2, advantage_pct, max_relevance,
            and per_query detail.
        """
        # Determine the maximum relevance label in the qrels
        all_rels = [rel for doc_rels in qrels_dict.values() for rel in doc_rels.values()]
        max_relevance = max(all_rels, default=1)

        per_query = []
        for query_id, doc_relevance in sorted(qrels_dict.items()):
            # Top-k from each strategy by score
            bl_docs = sorted(
                baseline_run_dict.get(query_id, {}).items(),
                key=lambda x: x[1],
                reverse=True,
            )[:k]
            ms_docs = sorted(
                ms_run_dict.get(query_id, {}).items(),
                key=lambda x: x[1],
                reverse=True,
            )[:k]

            bl_score2 = sum(
                1 for doc_id, _ in bl_docs if doc_relevance.get(doc_id, 0) >= max_relevance
            )
            ms_score2 = sum(
                1 for doc_id, _ in ms_docs if doc_relevance.get(doc_id, 0) >= max_relevance
            )

            if ms_score2 > bl_score2:
                winner = "MS"
            elif bl_score2 > ms_score2:
                winner = "BL"
            else:
                winner = "TIE"

            per_query.append({
                "query_id": query_id,
                "bl_score2": bl_score2,
                "ms_score2": ms_score2,
                "winner": winner,
            })

        ms_wins = sum(1 for r in per_query if r["winner"] == "MS")
        bl_wins = sum(1 for r in per_query if r["winner"] == "BL")
        ties = sum(1 for r in per_query if r["winner"] == "TIE")
        decided = ms_wins + bl_wins
        total_bl = sum(r["bl_score2"] for r in per_query)
        total_ms = sum(r["ms_score2"] for r in per_query)

        rel_label = f"score-{max_relevance}" if max_relevance > 1 else "relevant"
        logger.info(
            "  Head-to-head: MS wins %d, BL wins %d, Ties %d "
            "(MS wins %d%% of %d decided). "
            "%s in top-%d: BL=%d, MS=%d (%+.1f%%)",
            ms_wins, bl_wins, ties,
            round(ms_wins / decided * 100) if decided else 0, decided,
            rel_label, k, total_bl, total_ms,
            (total_ms - total_bl) / total_bl * 100 if total_bl else 0,
        )

        return {
            "k": k,
            "max_relevance": max_relevance,
            "queries_evaluated": len(per_query),
            "ms_wins": ms_wins,
            "bl_wins": bl_wins,
            "ties": ties,
            "decided": decided,
            "ms_win_pct": round(ms_wins / decided * 100, 1) if decided else 0,
            "total_bl_score2": total_bl,
            "total_ms_score2": total_ms,
            "advantage_pct": round(
                (total_ms - total_bl) / total_bl * 100, 1
            ) if total_bl else 0,
            "per_query": per_query,
        }

    @staticmethod
    def _compute_threshold_analysis(
        qrels_dict: dict[str, dict[str, int]],
        baseline_run_dict: dict[str, dict[str, float]],
        ms_run_dict: dict[str, dict[str, float]],
        thresholds: list[float] | None = None,
    ) -> dict[str, Any]:
        """Compute threshold-based retrieval metrics for each strategy.

        In practice, users and applications apply a relevance threshold —
        "only show results above 0.3." This metric measures what the user
        actually experiences: at a given threshold, how well does each
        strategy perform?

        Computes per-query, then macro-averages:
        - Precision@threshold: relevant_above / total_above (result cleanliness)
        - Recall@threshold: relevant_above / total_relevant (coverage)
        - F1@threshold: harmonic mean of precision and recall

        Also counts raw totals for interpretability.

        Returns:
            Dict with per-threshold breakdowns, metrics, and summary.
        """
        if thresholds is None:
            thresholds = [0.25, 0.30, 0.35, 0.40, 0.45, 0.50]

        # Detect relevance scale: binary (max=1) vs graded (max=2)
        # For head-to-head, compare docs at max relevance level
        all_rels = [rel for doc_rels in qrels_dict.values() for rel in doc_rels.values()]
        max_rel = max(all_rels) if all_rels else 1
        is_graded = max_rel >= 2  # True for NFCorpus (0/1/2), False for SciFact (0/1)

        threshold_results = []

        for t in thresholds:
            # Raw counts across all queries
            bl_total = 0
            bl_relevant = 0
            bl_score2 = 0
            ms_total = 0
            ms_relevant = 0
            ms_score2 = 0

            # Per-query metrics for macro-averaging
            bl_precisions: list[float] = []
            bl_recalls: list[float] = []
            ms_precisions: list[float] = []
            ms_recalls: list[float] = []

            # Head-to-head at threshold: per-query score-2 comparison
            h2h_ms_wins = 0
            h2h_bl_wins = 0
            h2h_ties = 0

            for query_id, doc_relevance in qrels_dict.items():
                # Total relevant docs for this query (for recall denominator)
                total_relevant_for_query = sum(1 for rel in doc_relevance.values() if rel > 0)

                # Baseline metrics for this query
                bl_above = 0
                bl_rel_above = 0
                bl_s2_this_query = 0
                for doc_id, score in baseline_run_dict.get(query_id, {}).items():
                    if score >= t:
                        bl_above += 1
                        bl_total += 1
                        rel = doc_relevance.get(doc_id, 0)
                        if rel > 0:
                            bl_rel_above += 1
                            bl_relevant += 1
                        if rel == max_rel:  # Highly relevant (score-2 for graded, score-1 for binary)
                            bl_score2 += 1
                            bl_s2_this_query += 1

                # Precision: relevant_above / total_above (0 if no results)
                bl_prec = bl_rel_above / bl_above if bl_above > 0 else 0.0
                # Recall: relevant_above / total_relevant (1.0 if no relevant docs)
                bl_rec = (
                    bl_rel_above / total_relevant_for_query
                    if total_relevant_for_query > 0 else 1.0
                )
                bl_precisions.append(bl_prec)
                bl_recalls.append(bl_rec)

                # MS metrics for this query
                ms_above = 0
                ms_rel_above = 0
                ms_s2_this_query = 0
                for doc_id, score in ms_run_dict.get(query_id, {}).items():
                    if score >= t:
                        ms_above += 1
                        ms_total += 1
                        rel = doc_relevance.get(doc_id, 0)
                        if rel > 0:
                            ms_rel_above += 1
                            ms_relevant += 1
                        if rel == max_rel:  # Highly relevant (score-2 for graded, score-1 for binary)
                            ms_score2 += 1
                            ms_s2_this_query += 1

                ms_prec = ms_rel_above / ms_above if ms_above > 0 else 0.0
                ms_rec = (
                    ms_rel_above / total_relevant_for_query
                    if total_relevant_for_query > 0 else 1.0
                )
                ms_precisions.append(ms_prec)
                ms_recalls.append(ms_rec)

                # Head-to-head comparison for this query
                if ms_s2_this_query > bl_s2_this_query:
                    h2h_ms_wins += 1
                elif bl_s2_this_query > ms_s2_this_query:
                    h2h_bl_wins += 1
                else:
                    h2h_ties += 1

            # Macro-average metrics
            bl_precision = sum(bl_precisions) / len(bl_precisions) if bl_precisions else 0.0
            bl_recall = sum(bl_recalls) / len(bl_recalls) if bl_recalls else 0.0
            bl_f1 = (
                2 * bl_precision * bl_recall / (bl_precision + bl_recall)
                if (bl_precision + bl_recall) > 0 else 0.0
            )

            ms_precision = sum(ms_precisions) / len(ms_precisions) if ms_precisions else 0.0
            ms_recall = sum(ms_recalls) / len(ms_recalls) if ms_recalls else 0.0
            ms_f1 = (
                2 * ms_precision * ms_recall / (ms_precision + ms_recall)
                if (ms_precision + ms_recall) > 0 else 0.0
            )

            # Compute deltas
            precision_delta = (
                round((ms_precision - bl_precision) / bl_precision * 100, 1)
                if bl_precision > 0 else None
            )
            recall_delta = (
                round((ms_recall - bl_recall) / bl_recall * 100, 1)
                if bl_recall > 0 else None
            )
            f1_delta = (
                round((ms_f1 - bl_f1) / bl_f1 * 100, 1)
                if bl_f1 > 0 else None
            )
            score2_advantage = (
                round((ms_score2 - bl_score2) / bl_score2 * 100, 1)
                if bl_score2 > 0 else None
            )

            # Head-to-head win rate
            h2h_decided = h2h_ms_wins + h2h_bl_wins
            h2h_win_rate = (
                round(h2h_ms_wins / h2h_decided * 100, 1)
                if h2h_decided > 0 else None
            )

            threshold_results.append({
                "threshold": t,
                # Raw counts
                "bl_total": bl_total,
                "bl_relevant": bl_relevant,
                "bl_score2": bl_score2,
                "ms_total": ms_total,
                "ms_relevant": ms_relevant,
                "ms_score2": ms_score2,
                # Macro-averaged metrics
                "bl_precision": round(bl_precision, 4),
                "bl_recall": round(bl_recall, 4),
                "bl_f1": round(bl_f1, 4),
                "ms_precision": round(ms_precision, 4),
                "ms_recall": round(ms_recall, 4),
                "ms_f1": round(ms_f1, 4),
                # Head-to-head at threshold
                "h2h_ms_wins": h2h_ms_wins,
                "h2h_bl_wins": h2h_bl_wins,
                "h2h_ties": h2h_ties,
                "h2h_win_rate": h2h_win_rate,
                # Deltas
                "precision_delta": precision_delta,
                "recall_delta": recall_delta,
                "f1_delta": f1_delta,
                "score2_advantage": score2_advantage,
            })

        logger.info("  Threshold analysis (macro-averaged metrics):")
        for r in threshold_results:
            f1_d = f"{r['f1_delta']:+.1f}%" if r['f1_delta'] is not None else "N/A"
            h2h = f"{r['h2h_win_rate']:.0f}%" if r['h2h_win_rate'] is not None else "N/A"
            logger.info(
                "    t=%.2f: BL F1=%.3f | MS F1=%.3f ΔF1=%s | H2H: MS %d, BL %d (%s win)",
                r["threshold"],
                r["bl_f1"], r["ms_f1"], f1_d,
                r["h2h_ms_wins"], r["h2h_bl_wins"], h2h,
            )

        return {
            "thresholds": threshold_results,
            "relevance_scale": "graded" if is_graded else "binary",
            "highly_relevant_threshold": max_rel,  # 2 for graded (NFCorpus), 1 for binary (SciFact)
        }

    @staticmethod
    def _compute_contribution_matrix(
        all_surface_scores: dict[str, dict[str, dict[str, float]]],
        queries: dict[str, Any],
        weights: dict[str, float],
    ) -> dict[str, dict[str, float]]:
        """Compute surface contribution fractions by query type.

        For each query+document, computes what fraction of total weighted surface
        score each surface contributed, then averages by query type.

        Args:
            all_surface_scores: {query_id: {doc_id: {surface: score}}} (untruncated).
            queries: {query_id: BeirQuery} for query text classification.
            weights: Surface weights (only surfaces with weight > 0 are included).

        Returns:
            {query_type: {surface: avg_contribution_fraction}}
        """
        from .query_classifier import QueryType, classify_query

        surfaces = [s for s in weights if weights[s] > 0]

        # Classify queries
        query_types: dict[str, QueryType] = {
            qid: classify_query(q.text) for qid, q in queries.items()
        }

        # Accumulate contributions per query type
        type_contributions: dict[str, dict[str, list[float]]] = {}
        for qtype in QueryType:
            type_contributions[qtype.value] = {s: [] for s in surfaces}

        for query_id, doc_surfaces in all_surface_scores.items():
            qtype = query_types.get(query_id, QueryType.UNKNOWN).value

            for _doc_id, scores in doc_surfaces.items():
                total = sum(scores.get(s, 0.0) for s in surfaces)
                if total <= 0:
                    continue
                for surface in surfaces:
                    fraction = scores.get(surface, 0.0) / total
                    type_contributions[qtype][surface].append(fraction)

        # Average
        matrix: dict[str, dict[str, float]] = {}
        for qtype, surface_lists in type_contributions.items():
            row = {}
            for surface, fractions in surface_lists.items():
                row[surface] = sum(fractions) / len(fractions) if fractions else 0.0
            if any(v > 0 for v in row.values()):
                matrix[qtype] = row

        return matrix
