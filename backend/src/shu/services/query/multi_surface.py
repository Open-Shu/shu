"""Multi-surface search mixin for query service.

Orchestrates multiple retrieval surfaces with score fusion.
"""

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from ...core.exceptions import ShuException
from ...schemas.query import QueryResponse, QueryResult, QueryType
from ...services.retrieval.result_formatter import dedupe_contributing_chunks
from .base import measure_execution_time

logger = logging.getLogger(__name__)


def _redact(text: str) -> str:
    """Return a non-reversible fingerprint for log-safe representation of sensitive text."""
    h = hashlib.sha256(text.encode()).hexdigest()[:8]
    return f"[len={len(text)} hash={h}]"


def _build_surfaces(vector_store, weights: dict[str, float], execute_zero_weight: bool) -> list:
    """Construct retrieval surfaces, optionally skipping those with zero weight.

    In production (execute_zero_weight=False), surfaces whose configured weight
    is 0 are omitted to save DB round-trips.  Set SHU_EXECUTE_ZERO_WEIGHT_SURFACES=true
    to run all surfaces so their scores are available for benchmarking analysis.
    """
    from ..retrieval.surfaces import (
        BM25Surface,
        ChunkSummaryVectorSurface,
        ChunkVectorSurface,
        QueryMatchSurface,
        SynopsisMatchSurface,
    )

    all_surfaces = [
        ChunkVectorSurface(vector_store),
        ChunkSummaryVectorSurface(vector_store),
        QueryMatchSurface(vector_store),
        SynopsisMatchSurface(vector_store),
        BM25Surface(),
    ]
    if execute_zero_weight:
        return all_surfaces
    surfaces = [s for s in all_surfaces if weights.get(s.name, 0) > 0]
    skipped = [s.name for s in all_surfaces if s not in surfaces]
    if skipped:
        logger.info(f"Skipping zero-weight surfaces: {skipped}")
    return surfaces


class MultiSurfaceSearchMixin:
    """Mixin providing multi-surface search orchestration."""

    @measure_execution_time
    async def _multi_surface_search(  # noqa: PLR0915
        self,
        knowledge_base_id: str,
        query: str,
        limit: int = 10,
        threshold: float = 0.0,
        *,
        chunk_vector_weight: float | None = None,
        query_match_weight: float | None = None,
        synopsis_match_weight: float | None = None,
        bm25_weight: float | None = None,
        chunk_summary_weight: float | None = None,
        fusion_formula: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Perform multi-surface search across multiple retrieval strategies.

        Executes all 5 retrieval surfaces in parallel, fuses scores, and returns
        document-level results.

        Args:
            knowledge_base_id: ID of the knowledge base to search
            query: Search query
            limit: Maximum number of documents to return
            threshold: Minimum score threshold for filtering results
            chunk_vector_weight: Weight for chunk vector surface (None = use config default)
            query_match_weight: Weight for query match surface (None = use config default)
            synopsis_match_weight: Weight for synopsis match surface (None = use config default)
            bm25_weight: Weight for BM25 surface (None = use config default)
            chunk_summary_weight: Weight for chunk summary surface (None = use config default)
            user_id: Optional user attribution forwarded into the orchestrator's
                ``embed_query`` call so the resulting llm_usage row attributes
                to the originating user (SHU-718).

        Returns:
            Dictionary with search results in QueryResponse format

        """
        try:
            # Verify knowledge base exists
            knowledge_base = await self._verify_knowledge_base(knowledge_base_id)

            # Block vector search on stale or re-embedding KBs (same as similarity_search)
            if knowledge_base.embedding_status != "current":
                from ...core.exceptions import KnowledgeBaseStaleEmbeddingsError

                raise KnowledgeBaseStaleEmbeddingsError(knowledge_base_id, str(knowledge_base.embedding_status))

            logger.info(
                f"Multi-surface search: query={_redact(query)} kb_id={knowledge_base_id} "
                f"limit={limit} threshold={threshold}"
            )

            # Get dependencies
            from ...core.database import get_async_session_local
            from ...core.embedding_service import get_embedding_service
            from ...core.vector_store import get_vector_store

            vector_store = await get_vector_store()
            embedding_service = await get_embedding_service()

            # Get weights (request params override config defaults)
            settings = self.config_manager.settings
            weights = {
                "chunk_vector": (
                    chunk_vector_weight
                    if chunk_vector_weight is not None
                    else settings.multi_surface_chunk_vector_weight
                ),
                "query_match": (
                    query_match_weight if query_match_weight is not None else settings.multi_surface_query_match_weight
                ),
                "synopsis_match": (
                    synopsis_match_weight
                    if synopsis_match_weight is not None
                    else settings.multi_surface_synopsis_match_weight
                ),
                "bm25": (bm25_weight if bm25_weight is not None else settings.multi_surface_bm25_weight),
                "chunk_summary": (
                    chunk_summary_weight
                    if chunk_summary_weight is not None
                    else settings.multi_surface_chunk_summary_weight
                ),
            }
            logger.info(f"Multi-surface weights: {weights}")

            from ..retrieval import MultiSurfaceSearchService, ScoreFusionService

            surfaces = _build_surfaces(vector_store, weights, settings.execute_zero_weight_surfaces)

            # Create fusion service with configured weights and optional formula override
            fusion_kwargs: dict[str, Any] = {"weights": weights}
            if fusion_formula:
                fusion_kwargs["fusion_formula"] = fusion_formula
            fusion_service = ScoreFusionService(**fusion_kwargs)

            # Create orchestrator
            search_service = MultiSurfaceSearchService(
                surfaces=surfaces,
                embedding_service=embedding_service,
                fusion_service=fusion_service,
                vector_store=vector_store,
                surface_limit=settings.multi_surface_chunk_limit,
                timeout_ms=settings.multi_surface_timeout_ms,
            )

            # Get max_chunks_per_document from RAG config (same limit baseline uses)
            rag_config = knowledge_base.get_rag_config()
            max_chunks_per_doc = rag_config.get("max_chunks_per_document", 2)

            # Execute search (pass session factory for safe parallel execution)
            kb_uuid = UUID(knowledge_base_id)
            fused_results, all_surface_scores, formatted_docs = await search_service.search(
                query=query,
                kb_id=kb_uuid,
                limit=limit,
                threshold=threshold,
                max_chunks_per_document=max_chunks_per_doc,
                session_factory=get_async_session_local(),
                user_id=user_id,
            )

            # Convert FusedResult to QueryResult format
            query_results = []
            for result in fused_results:
                # Use first contributing chunk for content and chunk-level metadata if available
                content = ""
                chunk_id = None
                chunk_index = None
                start_char = None
                end_char = None
                if result.contributing_chunks:
                    top_chunk = result.contributing_chunks[0]
                    content = top_chunk.snippet
                    chunk_id = str(top_chunk.chunk_id)
                    chunk_index = top_chunk.chunk_index
                    start_char = top_chunk.start_char
                    end_char = top_chunk.end_char
                elif result.surface_metadata:
                    # Document-level hit: use best available preview from surface metadata
                    for meta in result.surface_metadata.values():
                        if "matched_query" in meta:
                            content = meta["matched_query"]
                            break

                query_result = QueryResult(
                    chunk_id=chunk_id,
                    document_id=str(result.document_id),
                    document_title=result.document_title,
                    content=content,
                    similarity_score=result.final_score,
                    chunk_index=chunk_index,
                    start_char=start_char,
                    end_char=end_char,
                    file_type=result.file_type,
                    source_url=result.source_url,
                    source_id=result.source_id,
                    created_at=result.created_at,
                )
                query_results.append(query_result)

            response = QueryResponse(
                results=query_results,
                total_results=len(query_results),
                query=query,
                query_type=QueryType.MULTI_SURFACE,
                execution_time=0.0,  # Will be set by decorator
                similarity_threshold=threshold,
                embedding_model=str(knowledge_base.embedding_model),
                processed_at=datetime.now(UTC),
            )

            # Add multi-surface metadata to response
            response_dict = response.model_dump()
            response_dict["multi_surface_results"] = [
                {
                    "document_id": str(r.document_id),
                    "document_title": r.document_title,
                    "final_score": r.final_score,
                    "surface_scores": r.surface_scores,
                    "surface_metadata": r.surface_metadata,
                    "contributing_chunks": dedupe_contributing_chunks(
                        r.contributing_chunks, max_chunks=max_chunks_per_doc
                    )[0],
                }
                for r in fused_results
            ]

            # Structured document context from result formatter (SHU-652)
            response_dict["formatted_results"] = [
                {
                    "document_id": doc.document_id,
                    "document_title": doc.document_title,
                    "final_score": doc.final_score,
                    "surface_scores": doc.surface_scores,
                    "synopsis": doc.synopsis,
                    "title_summary": doc.title_summary,
                    "chunks": [
                        {
                            "chunk_id": c.chunk_id,
                            "chunk_index": c.chunk_index,
                            "score": c.score,
                            "content": c.content,
                            "surfaces": c.surfaces,
                            "summary": c.summary,
                            "matched_query": c.matched_query,
                            "promoted": c.promoted,
                        }
                        for c in doc.chunks
                    ],
                }
                for doc in formatted_docs
            ]

            # Per-surface scores for all scored documents (before top-k truncation)
            response_dict["all_surface_scores"] = all_surface_scores

            return response_dict

        except ShuException:
            # Re-raise ShuException without modification (preserves KnowledgeBaseNotFoundError, etc.)
            raise
        except Exception as e:
            logger.error(f"Failed to perform multi-surface search: {e}", exc_info=True)
            raise ShuException(f"Failed to perform multi-surface search: {e!s}", "MULTI_SURFACE_SEARCH_ERROR")
