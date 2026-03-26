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
from .base import measure_execution_time

logger = logging.getLogger(__name__)


def _redact(text: str) -> str:
    """Return a non-reversible fingerprint for log-safe representation of sensitive text."""
    h = hashlib.sha256(text.encode()).hexdigest()[:8]
    return f"[len={len(text)} hash={h}]"


class MultiSurfaceSearchMixin:
    """Mixin providing multi-surface search orchestration."""

    @measure_execution_time
    async def _multi_surface_search(
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

            # Create surfaces (5 active)
            from ..retrieval import MultiSurfaceSearchService, ScoreFusionService
            from ..retrieval.surfaces import (
                BM25Surface,
                ChunkSummaryVectorSurface,
                ChunkVectorSurface,
                QueryMatchSurface,
                SynopsisMatchSurface,
            )

            surfaces = [
                ChunkVectorSurface(vector_store),
                ChunkSummaryVectorSurface(vector_store),
                QueryMatchSurface(vector_store),
                SynopsisMatchSurface(vector_store),
                BM25Surface(),
            ]

            # Create fusion service with configured weights
            fusion_service = ScoreFusionService(weights=weights)

            # Create orchestrator
            search_service = MultiSurfaceSearchService(
                surfaces=surfaces,
                embedding_service=embedding_service,
                fusion_service=fusion_service,
                surface_limit=settings.multi_surface_chunk_limit,
                timeout_ms=settings.multi_surface_timeout_ms,
            )

            # Execute search (pass session factory for safe parallel execution)
            kb_uuid = UUID(knowledge_base_id)
            fused_results = await search_service.search(
                query=query,
                kb_id=kb_uuid,
                limit=limit,
                threshold=threshold,
                session_factory=get_async_session_local(),
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
                    "contributing_chunks": [
                        {
                            "chunk_id": str(c.chunk_id),
                            "chunk_index": c.chunk_index,
                            "surface": c.surface,
                            "score": c.score,
                            "snippet": c.snippet,
                            "summary": c.summary,
                        }
                        for c in r.contributing_chunks
                    ],
                }
                for r in fused_results
            ]

            return response_dict

        except ShuException:
            # Re-raise ShuException without modification (preserves KnowledgeBaseNotFoundError, etc.)
            raise
        except Exception as e:
            logger.error(f"Failed to perform multi-surface search: {e}", exc_info=True)
            raise ShuException(f"Failed to perform multi-surface search: {e!s}", "MULTI_SURFACE_SEARCH_ERROR")
