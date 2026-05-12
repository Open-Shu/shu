"""Hybrid search mixin for query service.

Combines similarity and keyword search with configurable weights.
"""

import hashlib
from datetime import UTC, datetime
from typing import Any

from shu.core.logging import get_logger

from ...core.exceptions import ShuException
from ...schemas.query import SimilaritySearchRequest
from .base import measure_execution_time

logger = get_logger(__name__)


def _redact(text: str) -> str:
    """Return a non-reversible fingerprint for log-safe representation of sensitive text."""
    h = hashlib.sha256(text.encode()).hexdigest()[:8]
    return f"[len={len(text)} hash={h}]"


class HybridSearchMixin:
    """Mixin providing hybrid (similarity + keyword) search."""

    @measure_execution_time
    async def hybrid_search(  # noqa: PLR0915, PLR0912
        self,
        knowledge_base_id: str,
        query: str,
        limit: int = 10,
        threshold: float = 0.0,
        *,
        title_weighting_enabled: bool | None = None,
        title_weight_multiplier: float | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Perform hybrid search (combination of similarity and keyword).

        This method combines results from both similarity and keyword search,
        properly handling stop word filtering by delegating to the existing
        search methods.

        Args:
            knowledge_base_id: ID of the knowledge base to search
            query: Search query
            limit: Maximum number of results to return
            threshold: Similarity threshold for filtering results
            user_id: Optional user attribution forwarded to both sub-searches
                so the resulting embedding llm_usage rows attribute to the
                originating user (SHU-718).

        Returns:
            Dictionary with search results

        """
        try:
            # Verify knowledge base exists
            knowledge_base = await self._verify_knowledge_base(knowledge_base_id)

            # Log hybrid search request
            logger.info(
                f"Hybrid search: query={_redact(query)} kb_id={knowledge_base_id} limit={limit} threshold={threshold}"
            )

            # Get similarity search results (may fail if KB has stale embeddings)
            similarity_response = None
            try:
                similarity_request = SimilaritySearchRequest(
                    query=query,
                    limit=limit,
                    threshold=threshold,
                    include_embeddings=False,
                    document_ids=None,
                    file_types=None,
                    created_after=None,
                    created_before=None,
                )
                similarity_response = await self.similarity_search(
                    knowledge_base_id, similarity_request, user_id=user_id
                )
            except ShuException as e:
                if e.error_code == "KNOWLEDGE_BASE_STALE_EMBEDDINGS":
                    logger.warning(
                        f"Hybrid search falling back to keyword-only: KB {knowledge_base_id} has stale embeddings"
                    )
                else:
                    raise

            # Get keyword search results (this handles stop word filtering correctly)
            keyword_response = await self.keyword_search(
                knowledge_base_id,
                query,
                limit,
                title_weighting_enabled=title_weighting_enabled,
                title_weight_multiplier=title_weight_multiplier,
                user_id=user_id,
            )

            # Log keyword search results for debugging
            logger.info(
                f"Hybrid search keyword results: total={keyword_response.get('total_results', 0)}, "
                f"top_doc_ids={[r.get('document_id', 'unknown') for r in keyword_response.get('results', [])[:3]]}"
            )

            # If similarity search was skipped (stale KB), return keyword-only results
            if similarity_response is None:
                from ...schemas.query import QueryResponse, QueryType

                response = QueryResponse(
                    results=keyword_response["results"],
                    total_results=keyword_response["total_results"],
                    query=query,
                    query_type=QueryType.HYBRID,
                    execution_time=0.0,
                    similarity_threshold=threshold,
                    embedding_model=str(knowledge_base.embedding_model),
                    processed_at=datetime.now(UTC),
                )
                return response.model_dump()

            # If keyword search returned empty results due to stop words, return only similarity results
            if keyword_response["total_results"] == 0:
                logger.info(
                    f"Keyword search returned no results for query {_redact(query)}, returning only similarity results"
                )
                # Create a new response with hybrid query type but similarity results
                from ...schemas.query import QueryResponse, QueryType

                response = QueryResponse(
                    results=similarity_response["results"],
                    total_results=similarity_response["total_results"],
                    query=query,
                    query_type=QueryType.HYBRID,
                    execution_time=0.0,  # Will be set by decorator
                    similarity_threshold=threshold,
                    embedding_model=str(knowledge_base.embedding_model),
                    processed_at=datetime.now(UTC),
                )
                return response.model_dump()

            # Combine and rank results from both searches
            combined_results = {}
            similarity_weight = self.config_manager.get_hybrid_similarity_weight()
            keyword_weight = self.config_manager.get_hybrid_keyword_weight()

            # Add similarity results
            for chunk in similarity_response["results"]:
                chunk_id = chunk.get("chunk_id") or chunk.get("id")
                if chunk_id is None:
                    continue
                sim_score = float(chunk.get("similarity_score", 0.0))
                combined_results[chunk_id] = {
                    "chunk": chunk,
                    "similarity_score": sim_score,
                    "keyword_score": 0.0,
                    "combined_score": sim_score * similarity_weight,
                }

            # Add keyword results
            for chunk in keyword_response["results"]:
                chunk_id = chunk.get("chunk_id") or chunk.get("id")
                if chunk_id is None:
                    continue
                keyword_score = float(chunk.get("similarity_score", 0.8))
                if chunk_id in combined_results:
                    combined_results[chunk_id]["keyword_score"] = keyword_score
                    combined_results[chunk_id]["combined_score"] = (
                        combined_results[chunk_id]["similarity_score"] * similarity_weight
                        + keyword_score * keyword_weight
                    )
                else:
                    combined_results[chunk_id] = {
                        "chunk": chunk,
                        "similarity_score": 0.0,
                        "keyword_score": keyword_score,
                        "combined_score": keyword_score * keyword_weight,
                    }

            # Get RAG configuration for chunk limits
            rag_config = await self._get_rag_config(knowledge_base_id)
            max_chunks_per_doc = rag_config.get("max_chunks_per_document", 2)

            # Sort by combined score and apply document de-duplication
            # Group by document and keep top chunks per document
            document_results = {}
            for result in combined_results.values():
                chunk = result["chunk"]
                doc_id = chunk.document_id if hasattr(chunk, "document_id") else chunk.get("document_id")
                if doc_id is None:
                    continue
                if doc_id not in document_results:
                    document_results[doc_id] = []
                document_results[doc_id].append(result)

            # Sort chunks within each document by combined score
            for _, doc_results in document_results.items():
                doc_results.sort(key=lambda x: x["combined_score"], reverse=True)

            # Flatten results with per-document cap, then re-sort globally
            sorted_results = []
            for _, doc_results in document_results.items():
                sorted_results.extend(doc_results[:max_chunks_per_doc])

            sorted_results.sort(key=lambda x: x["combined_score"], reverse=True)
            sorted_results = sorted_results[:limit]

            # Convert to QueryResponse format
            from ...schemas.query import QueryResponse, QueryResult, QueryType

            def _get_chunk_attr(obj: Any, attr: str, default: Any = None) -> Any:
                """Safely get attribute from object or dict, with chunk_id/id mapping."""
                if hasattr(obj, attr):
                    return getattr(obj, attr)
                if isinstance(obj, dict):
                    if attr == "id" and "chunk_id" in obj:
                        return obj["chunk_id"]
                    return obj.get(attr, default)
                return default

            query_results = []
            for result in sorted_results:
                chunk = result["chunk"]
                query_result = QueryResult(
                    chunk_id=_get_chunk_attr(chunk, "id"),
                    document_id=_get_chunk_attr(chunk, "document_id"),
                    document_title=_get_chunk_attr(chunk, "document_title") or "Unknown Document",
                    content=_get_chunk_attr(chunk, "content"),
                    similarity_score=result["combined_score"],
                    chunk_index=_get_chunk_attr(chunk, "chunk_index"),
                    start_char=_get_chunk_attr(chunk, "start_char"),
                    end_char=_get_chunk_attr(chunk, "end_char"),
                    file_type=_get_chunk_attr(chunk, "file_type") or "txt",
                    source_url=_get_chunk_attr(chunk, "source_url"),
                    source_id=_get_chunk_attr(chunk, "source_id"),
                    created_at=_get_chunk_attr(chunk, "created_at"),
                )
                query_results.append(query_result)

            response = QueryResponse(
                results=query_results,
                total_results=len(query_results),
                query=query,
                query_type=QueryType.HYBRID,
                execution_time=0.0,  # Will be set by decorator
                similarity_threshold=threshold,
                embedding_model=str(knowledge_base.embedding_model),
                processed_at=datetime.now(UTC),
            )
            return response.model_dump()
        except ShuException:
            raise
        except Exception as e:
            logger.error(f"Failed to perform hybrid search: {e}", exc_info=True)
            raise ShuException(f"Failed to perform hybrid search: {e!s}", "HYBRID_SEARCH_ERROR")
