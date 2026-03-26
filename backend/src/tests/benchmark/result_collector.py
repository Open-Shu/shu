"""Search result collection for benchmark evaluation.

Runs queries through the Shu search API and collects results in a format
compatible with ranx for IR metric computation.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .beir_loader import BeirQuery

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)


@dataclass
class SearchConfig:
    """Configuration for search execution."""

    limit: int = 100
    threshold: float = 0.0
    weight_overrides: dict[str, float] | None = None


@dataclass
class CollectionStats:
    """Statistics about a result collection run."""

    query_count: int = 0
    total_results: int = 0
    queries_with_no_results: int = 0
    elapsed_seconds: float = 0.0


class ResultCollector:
    """Execute queries via the Shu API and collect results for evaluation."""

    def __init__(self, client: httpx.AsyncClient, kb_id: str, auth_headers: dict[str, str]):
        self.client = client
        self.kb_id = kb_id
        self.auth_headers = auth_headers

    async def collect_similarity_run(
        self,
        queries: dict[str, BeirQuery],
        id_map: dict[str, str],
        config: SearchConfig | None = None,
    ) -> tuple[dict[str, dict[str, float]], CollectionStats]:
        """Run all queries through similarity search (baseline).

        Aggregates chunk-level results to document-level (max score per doc).

        Args:
            queries: Dict of query_id -> BeirQuery.
            id_map: Dict of shu_doc_uuid -> beir_doc_id.
            config: Search configuration.

        Returns:
            Tuple of (ranx run dict, collection stats).
            Run dict format: {query_id: {beir_doc_id: score, ...}, ...}
        """
        cfg = config or SearchConfig()
        run_dict: dict[str, dict[str, float]] = {}
        stats = CollectionStats()
        start = time.monotonic()

        for query_id, query in queries.items():
            results = await self._run_similarity_search(query.text, cfg)
            doc_scores = self._aggregate_to_document_level(results, id_map)

            if doc_scores:
                run_dict[query_id] = doc_scores
                stats.total_results += len(doc_scores)
            else:
                stats.queries_with_no_results += 1

            stats.query_count += 1

            if stats.query_count % 50 == 0:
                logger.info("Similarity search progress: %d / %d queries", stats.query_count, len(queries))

        stats.elapsed_seconds = time.monotonic() - start
        logger.info(
            "Similarity run collected: %d queries, %d total results (%.1fs)",
            stats.query_count,
            stats.total_results,
            stats.elapsed_seconds,
        )
        return run_dict, stats

    async def collect_multi_surface_run(
        self,
        queries: dict[str, BeirQuery],
        id_map: dict[str, str],
        config: SearchConfig | None = None,
    ) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, dict[str, float]]], CollectionStats]:
        """Run all queries through multi-surface search.

        Args:
            queries: Dict of query_id -> BeirQuery.
            id_map: Dict of shu_doc_uuid -> beir_doc_id.
            config: Search configuration (including optional weight overrides).

        Returns:
            Tuple of:
                - ranx run dict: {query_id: {beir_doc_id: score, ...}, ...}
                - surface scores: {query_id: {beir_doc_id: {surface: score}}}
                - collection stats
        """
        cfg = config or SearchConfig()
        run_dict: dict[str, dict[str, float]] = {}
        surface_scores: dict[str, dict[str, dict[str, float]]] = {}
        stats = CollectionStats()
        start = time.monotonic()

        for query_id, query in queries.items():
            ms_results = await self._run_multi_surface_search(query.text, cfg)
            doc_scores, per_doc_surfaces = self._extract_multi_surface_results(ms_results, id_map)

            if doc_scores:
                run_dict[query_id] = doc_scores
                surface_scores[query_id] = per_doc_surfaces
                stats.total_results += len(doc_scores)
            else:
                stats.queries_with_no_results += 1

            stats.query_count += 1

            if stats.query_count % 50 == 0:
                logger.info("Multi-surface search progress: %d / %d queries", stats.query_count, len(queries))

        stats.elapsed_seconds = time.monotonic() - start
        logger.info(
            "Multi-surface run collected: %d queries, %d total results (%.1fs)",
            stats.query_count,
            stats.total_results,
            stats.elapsed_seconds,
        )
        return run_dict, surface_scores, stats

    async def _run_similarity_search(self, query_text: str, config: SearchConfig) -> list[dict[str, Any]]:
        """Execute a single similarity search via the API."""
        payload = {
            "query": query_text,
            "query_type": "similarity",
            "limit": config.limit,
            "similarity_threshold": config.threshold,
        }

        resp = await self.client.post(
            f"/api/v1/query/{self.kb_id}/search",
            json=payload,
            headers=self.auth_headers,
        )

        if resp.status_code != 200:
            logger.warning("Similarity search failed (status %d): %s", resp.status_code, query_text[:80])
            return []

        data = resp.json().get("data", {})
        return data.get("results", [])

    async def _run_multi_surface_search(self, query_text: str, config: SearchConfig) -> list[dict[str, Any]]:
        """Execute a single multi-surface search via the API."""
        payload: dict[str, Any] = {
            "query": query_text,
            "query_type": "multi_surface",
            "limit": config.limit,
            "similarity_threshold": config.threshold,
        }

        if config.weight_overrides:
            payload.update(config.weight_overrides)

        resp = await self.client.post(
            f"/api/v1/query/{self.kb_id}/search",
            json=payload,
            headers=self.auth_headers,
        )

        if resp.status_code != 200:
            logger.warning("Multi-surface search failed (status %d): %s", resp.status_code, query_text[:80])
            return []

        data = resp.json().get("data", {})
        return data.get("multi_surface_results", [])

    def _aggregate_to_document_level(
        self,
        chunk_results: list[dict[str, Any]],
        id_map: dict[str, str],
    ) -> dict[str, float]:
        """Aggregate chunk-level similarity results to document-level.

        Takes max similarity_score per document, then maps Shu UUIDs to BEIR IDs.

        Returns:
            Dict of {beir_doc_id: max_score}.
        """
        # Group by document, take max score
        doc_max: dict[str, float] = {}
        for result in chunk_results:
            # Use source_id directly if available (faster than UUID lookup)
            beir_id = result.get("source_id")
            if not beir_id:
                shu_doc_id = result.get("document_id", "")
                beir_id = id_map.get(shu_doc_id)

            if not beir_id:
                continue

            score = float(result.get("similarity_score", 0.0))
            if beir_id not in doc_max or score > doc_max[beir_id]:
                doc_max[beir_id] = score

        return doc_max

    def _extract_multi_surface_results(
        self,
        ms_results: list[dict[str, Any]],
        id_map: dict[str, str],
    ) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
        """Extract document scores and per-surface scores from multi-surface results.

        Returns:
            Tuple of:
                - {beir_doc_id: final_score}
                - {beir_doc_id: {surface_name: score}}
        """
        doc_scores: dict[str, float] = {}
        per_doc_surfaces: dict[str, dict[str, float]] = {}

        for result in ms_results:
            shu_doc_id = result.get("document_id", "")
            beir_id = id_map.get(shu_doc_id)

            if not beir_id:
                continue

            doc_scores[beir_id] = float(result.get("final_score", 0.0))
            per_doc_surfaces[beir_id] = result.get("surface_scores", {})

        return doc_scores, per_doc_surfaces
