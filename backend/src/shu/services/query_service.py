"""Query service for Shu RAG Backend.

Handles document queries, similarity search, and hybrid search operations.
The QueryService class is composed from mixins in the query/ package, each
providing a distinct search type. This module owns the dispatcher method
(query_documents) that routes to the appropriate search implementation.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import ConfigurationManager
from ..core.exceptions import ShuException
from ..schemas.query import QueryRequest, QueryResponse, QueryResult, QueryType, SimilaritySearchRequest
from .query.base import QueryServiceBase
from .query.hybrid import HybridSearchMixin
from .query.keyword import KeywordSearchMixin
from .query.multi_surface import MultiSurfaceSearchMixin
from .query.similarity import SimilaritySearchMixin

# Re-export for backward compatibility (used by rag_query_processing.py, rag_query_rewrite.py)
from .query_constants import COMPREHENSIVE_STOP_WORDS, TITLE_MATCH_STOP_WORDS  # noqa: F401

logger = logging.getLogger(__name__)


class QueryService(
    SimilaritySearchMixin,
    KeywordSearchMixin,
    HybridSearchMixin,
    MultiSurfaceSearchMixin,
    QueryServiceBase,
):
    """Service for querying documents and performing search operations.

    Composed from mixins:
    - QueryServiceBase: shared utilities, document ops, preprocessing
    - SimilaritySearchMixin: vector similarity search
    - KeywordSearchMixin: keyword search with title weighting
    - HybridSearchMixin: combined similarity + keyword search
    - MultiSurfaceSearchMixin: multi-surface search orchestration
    """

    def __init__(self, db: AsyncSession, config_manager: ConfigurationManager) -> None:
        super().__init__(db, config_manager)

    async def query_documents(self, knowledge_base_id: str, request: "QueryRequest") -> dict[str, Any]:
        """Unified query method supporting all search types.

        This is the primary entry point for all document queries, consolidating
        similarity, keyword, and hybrid search functionality. It also handles
        legacy SimilaritySearchRequest payloads for backward compatibility.

        Args:
            knowledge_base_id: ID of the knowledge base to search
            request: Query request (supports backward compatibility fields)

        Returns:
            Dictionary with search results and RAG configuration
            # Note: try/except around the entire method exists below to map errors to ShuException

        """
        try:
            # Verify knowledge base exists
            knowledge_base = await self._verify_knowledge_base(knowledge_base_id)

            # Get RAG configuration for this knowledge base
            rag_config = await self._get_rag_config(knowledge_base_id)

            # Extract parameters from request, resolving None to config defaults
            query = request.query
            search_type = request.query_type  # Already a string
            limit = request.limit if request.limit is not None else self.config_manager.get_rag_max_chunks()

            logger.info(f"query_documents: search_type={search_type}, query_len={len(query or '')}, limit={limit}")

            # Perform search based on type
            if search_type == "similarity":
                # Create SimilaritySearchRequest from QueryRequest
                similarity_request = SimilaritySearchRequest(
                    query=query,
                    limit=limit,
                    threshold=request.similarity_threshold or 0.0,
                    include_embeddings=getattr(request, "include_embeddings", False),
                    document_ids=getattr(request, "document_ids", None),
                    file_types=getattr(request, "file_types", None),
                    created_after=getattr(request, "created_after", None),
                    created_before=getattr(request, "created_before", None),
                )
                similarity_response = await self.similarity_search(knowledge_base_id, similarity_request)

                # Convert SimilaritySearchResponse to QueryResponse format
                query_results = []
                for chunk in similarity_response["results"]:
                    query_result = QueryResult(
                        chunk_id=chunk.get("chunk_id") or chunk.get("id"),
                        document_id=chunk.get("document_id"),
                        document_title=chunk.get("document_title") or "Unknown Document",
                        content=chunk.get("content"),
                        similarity_score=chunk.get("similarity_score"),
                        chunk_index=chunk.get("chunk_index"),
                        start_char=chunk.get("start_char"),
                        end_char=chunk.get("end_char"),
                        file_type=chunk.get("file_type") or "txt",
                        source_url=chunk.get("source_url"),
                        source_id=chunk.get("source_id"),
                        created_at=chunk.get("created_at"),
                    )
                    query_results.append(query_result)

                query_response = QueryResponse(
                    results=query_results,
                    total_results=similarity_response["total_results"],
                    query=similarity_response["query"],
                    query_type=QueryType.SIMILARITY,
                    execution_time=similarity_response["execution_time"],
                    similarity_threshold=similarity_response["threshold"],
                    embedding_model=similarity_response["embedding_model"],
                    processed_at=datetime.now(UTC),
                )
            elif search_type == "keyword":
                query_response = await self.keyword_search(
                    knowledge_base_id,
                    query,
                    limit,
                    title_weighting_enabled=request.title_weighting_enabled,
                    title_weight_multiplier=request.title_weight_multiplier,
                )
            elif search_type == "hybrid":
                query_response = await self.hybrid_search(
                    knowledge_base_id,
                    query,
                    limit,
                    request.similarity_threshold or 0.0,
                    title_weighting_enabled=request.title_weighting_enabled,
                    title_weight_multiplier=request.title_weight_multiplier,
                )
            elif search_type == "multi_surface":
                query_response = await self._multi_surface_search(
                    knowledge_base_id,
                    query,
                    limit,
                    request.similarity_threshold or 0.0,
                    chunk_vector_weight=request.chunk_vector_weight,
                    query_match_weight=request.query_match_weight,
                    synopsis_match_weight=request.synopsis_match_weight,
                    keyword_match_weight=request.keyword_match_weight,
                    chunk_summary_weight=request.chunk_summary_weight,
                )
            else:
                raise ShuException(f"Unsupported search type: {search_type}", "UNSUPPORTED_SEARCH_TYPE")

            # Attach full-document escalation when configured
            # Normalize results to list[dict] for escalation
            if hasattr(query_response, "results"):
                norm_results = [r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in query_response.results]
            else:
                norm_results = list(query_response.get("results", []))

            escalation = await self._maybe_escalate_full_documents(
                knowledge_base=knowledge_base,
                rag_config=rag_config,
                query=query,
                results=norm_results,
            )

            # Return response with both search results, RAG configuration, and escalation
            # Handle both object and dict responses
            if hasattr(query_response, "results"):
                # Object response
                return {
                    "results": query_response.results,
                    "total_results": query_response.total_results,
                    "query": query_response.query,
                    "query_type": query_response.query_type,
                    "execution_time": query_response.execution_time,
                    "similarity_threshold": query_response.similarity_threshold,
                    "embedding_model": query_response.embedding_model,
                    "processed_at": query_response.processed_at,
                    "rag_config": rag_config,
                    "escalation": escalation,
                }
            # Dict response
            response_dict = dict(query_response)
            response_dict["rag_config"] = rag_config
            response_dict["escalation"] = escalation
            return response_dict
        except ShuException:
            # Re-raise ShuException without modification
            raise
        except Exception as e:
            logger.error(f"Failed to query documents: {e}", exc_info=True)
            raise ShuException(f"Failed to query documents: {e!s}", "QUERY_ERROR")
