"""Ablation study runner for surface contribution analysis.

Measures the impact of each retrieval surface by running multi-surface
search with individual surfaces disabled (weight=0). Also computes a
surface x query-type contribution matrix from surface_scores.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .beir_loader import BeirQuery
from .query_classifier import QueryType, classify_query
from .result_collector import ResultCollector, SearchConfig

logger = logging.getLogger(__name__)

SURFACES = ["chunk_vector", "chunk_summary", "query_match", "synopsis_match", "bm25"]

# Surfaces representing novel contributions (for patent evidence)
# bm25 is excluded — BM25/keyword matching is well-established prior art
NOVEL_SURFACES = ["chunk_summary", "query_match", "synopsis_match"]

# Weight parameter names in the API
WEIGHT_PARAMS = {
    "chunk_vector": "chunk_vector_weight",
    "chunk_summary": "chunk_summary_weight",
    "query_match": "query_match_weight",
    "synopsis_match": "synopsis_match_weight",
    "bm25": "bm25_weight",
}


@dataclass
class AblationResults:
    """Results from an ablation study."""

    # Metrics with all surfaces enabled (reference point)
    full_run_scores: dict[str, float]

    # Per-surface ablation: {surface_removed: {metric: score}}
    ablation_run_scores: dict[str, dict[str, float]]

    # Solo surface performance: {surface_name: {metric: score}}
    # Each surface run in isolation (weight=1.0, all others=0.0)
    solo_surface_scores: dict[str, dict[str, float]]

    # Surface contribution matrix: {query_type: {surface: avg_contribution_fraction}}
    contribution_matrix: dict[str, dict[str, float]]

    # Surfaces contributing < 5% on average across all query types
    low_contribution_surfaces: list[str]

    # Suggested weight adjustments based on ablation impact
    weight_recommendations: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "full_run_scores": self.full_run_scores,
            "ablation_run_scores": self.ablation_run_scores,
            "solo_surface_scores": self.solo_surface_scores,
            "contribution_matrix": self.contribution_matrix,
            "low_contribution_surfaces": self.low_contribution_surfaces,
            "weight_recommendations": self.weight_recommendations,
        }


class AblationRunner:
    """Run ablation studies on multi-surface search."""

    def __init__(
        self,
        collector: ResultCollector,
        id_map: dict[str, str],
        queries: dict[str, BeirQuery],
        qrels_dict: dict[str, dict[str, int]],
        metrics: list[str],
        search_config: SearchConfig | None = None,
    ):
        self.collector = collector
        self.id_map = id_map
        self.queries = queries
        self.qrels_dict = qrels_dict
        self.metrics = metrics
        self.search_config = search_config or SearchConfig()

    async def run(self) -> AblationResults:
        """Run complete ablation study.

        1. Run full multi-surface search (all surfaces enabled)
        2. Derive per-surface solo rankings from surface_scores (no extra API calls)
        3. For each surface, run with that surface disabled (ablation)
        4. Compute contribution matrix from surface_scores
        5. Generate recommendations
        """
        from ranx import Qrels, Run, evaluate

        qrels = Qrels(self.qrels_dict, name="ground_truth")

        # Full run (reference) — this single call gives us everything:
        # - fused scores for the full-system evaluation
        # - per-surface scores for solo-surface evaluation (no extra API calls)
        logger.info("Running full multi-surface search (all surfaces)...")
        full_run_dict, full_surface_scores, _, full_all_surface_scores = await self.collector.collect_multi_surface_run(
            self.queries, self.id_map, self.search_config,
        )
        full_run = Run(full_run_dict, name="full_multi_surface")
        full_scores = evaluate(qrels, full_run, self.metrics, make_comparable=True)
        logger.info("Full run scores: %s", {k: f"{v:.4f}" for k, v in full_scores.items()})

        # Solo surface evaluation — use untruncated surface scores (all documents
        # scored by any surface, not just fused top-k) for unbiased evaluation.
        eval_scores = full_all_surface_scores if full_all_surface_scores else full_surface_scores
        solo_surface_scores = self._evaluate_solo_surfaces(qrels, eval_scores)

        # Ablation: disable each surface one at a time (requires API calls
        # because the fusion changes which documents appear in results)
        ablation_run_scores: dict[str, dict[str, float]] = {}
        for surface in SURFACES:
            logger.info("Ablation: disabling '%s'...", surface)
            ablated_run_dict = await self._run_without_surface(surface)

            if ablated_run_dict:
                ablated_run = Run(ablated_run_dict, name=f"without_{surface}")
                ablated_scores = evaluate(qrels, ablated_run, self.metrics, make_comparable=True)
            else:
                ablated_scores = {m: 0.0 for m in self.metrics}

            ablation_run_scores[surface] = ablated_scores

            # Log impact
            ndcg_full = full_scores.get("ndcg@10", 0.0)
            ndcg_ablated = ablated_scores.get("ndcg@10", 0.0)
            impact = ((ndcg_ablated - ndcg_full) / ndcg_full * 100) if ndcg_full > 0 else 0.0
            logger.info("  without %s: NDCG@10 = %.4f (%+.1f%%)", surface, ndcg_ablated, impact)

        # Contribution matrix from untruncated surface_scores
        contribution_matrix = self._build_contribution_matrix(eval_scores)

        # Identify low-contribution surfaces
        low_contribution = self._find_low_contribution_surfaces(contribution_matrix)

        # Generate weight recommendations
        weight_recommendations = self._generate_weight_recommendations(
            full_scores, ablation_run_scores,
        )

        return AblationResults(
            full_run_scores=full_scores,
            ablation_run_scores=ablation_run_scores,
            solo_surface_scores=solo_surface_scores,
            contribution_matrix=contribution_matrix,
            low_contribution_surfaces=low_contribution,
            weight_recommendations=weight_recommendations,
        )

    async def _run_without_surface(self, surface_name: str) -> dict[str, dict[str, float]]:
        """Run multi-surface search with one surface disabled."""
        weight_param = WEIGHT_PARAMS[surface_name]
        config = SearchConfig(
            limit=self.search_config.limit,
            threshold=self.search_config.threshold,
            weight_overrides={weight_param: 0.0},
        )
        run_dict, _, _, _ = await self.collector.collect_multi_surface_run(
            self.queries, self.id_map, config,
        )
        return run_dict

    def _evaluate_solo_surfaces(
        self,
        qrels: Any,
        surface_scores: dict[str, dict[str, dict[str, float]]],
    ) -> dict[str, dict[str, float]]:
        """Evaluate each surface's standalone ranking quality.

        Derives per-surface rankings from the surface_scores already returned
        by the full multi-surface search — no additional API calls needed.
        Each surface's raw score is used as the document ranking score.

        Args:
            qrels: ranx Qrels object.
            surface_scores: {query_id: {beir_doc_id: {surface: score}}}

        Returns:
            {surface_name: {metric: score}}
        """
        from ranx import Run, evaluate

        solo_scores: dict[str, dict[str, float]] = {}

        for surface in SURFACES:
            # Build a run using only this surface's scores
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
                scores = evaluate(qrels, run, self.metrics, make_comparable=True)
            else:
                scores = {m: 0.0 for m in self.metrics}

            solo_scores[surface] = scores
            logger.info("  solo %s: NDCG@10 = %.4f", surface, scores.get("ndcg@10", 0.0))

        return solo_scores

    def _build_contribution_matrix(
        self,
        surface_scores: dict[str, dict[str, dict[str, float]]],
    ) -> dict[str, dict[str, float]]:
        """Build surface x query-type contribution matrix.

        For each query, computes what fraction of the total surface score
        each surface contributed, then averages by query type.

        Args:
            surface_scores: {query_id: {beir_doc_id: {surface: score}}}

        Returns:
            {query_type: {surface: avg_contribution_fraction}}
        """
        # Classify queries
        query_types: dict[str, QueryType] = {
            qid: classify_query(q.text) for qid, q in self.queries.items()
        }

        # Accumulate contributions per query type
        type_contributions: dict[str, dict[str, list[float]]] = {}
        for qtype in QueryType:
            type_contributions[qtype.value] = {s: [] for s in SURFACES}

        for query_id, doc_surfaces in surface_scores.items():
            qtype = query_types.get(query_id, QueryType.UNKNOWN).value

            for _doc_id, scores in doc_surfaces.items():
                total = sum(scores.values())
                if total <= 0:
                    continue

                for surface in SURFACES:
                    fraction = scores.get(surface, 0.0) / total
                    type_contributions[qtype][surface].append(fraction)

        # Average
        matrix: dict[str, dict[str, float]] = {}
        for qtype, surface_lists in type_contributions.items():
            matrix[qtype] = {}
            for surface, fractions in surface_lists.items():
                matrix[qtype][surface] = sum(fractions) / len(fractions) if fractions else 0.0

        return matrix

    def _find_low_contribution_surfaces(
        self,
        matrix: dict[str, dict[str, float]],
        threshold: float = 0.05,
    ) -> list[str]:
        """Find surfaces contributing < threshold average across all query types."""
        low: list[str] = []
        for surface in SURFACES:
            contributions = [matrix[qtype].get(surface, 0.0) for qtype in matrix]
            avg = sum(contributions) / len(contributions) if contributions else 0.0
            if avg < threshold:
                low.append(surface)
        return low

    def _generate_weight_recommendations(
        self,
        full_scores: dict[str, float],
        ablation_scores: dict[str, dict[str, float]],
    ) -> dict[str, float]:
        """Generate weight recommendations based on ablation impact.

        Surfaces whose removal causes the largest NDCG@10 drop should
        get higher weights. This is a simple proportional allocation.
        """
        ndcg_key = "ndcg@10"
        full_ndcg = full_scores.get(ndcg_key, 0.0)

        if full_ndcg <= 0:
            return {s: 1.0 / len(SURFACES) for s in SURFACES}

        # Impact = how much NDCG@10 drops when surface is removed
        impacts: dict[str, float] = {}
        for surface, scores in ablation_scores.items():
            ablated_ndcg = scores.get(ndcg_key, 0.0)
            drop = max(0.0, full_ndcg - ablated_ndcg)
            impacts[surface] = drop

        total_impact = sum(impacts.values())
        if total_impact <= 0:
            return {s: 1.0 / len(SURFACES) for s in SURFACES}

        # Proportional weights
        weights = {s: impacts[s] / total_impact for s in SURFACES}

        # Round to 2 decimal places
        return {s: round(w, 2) for s, w in weights.items()}
