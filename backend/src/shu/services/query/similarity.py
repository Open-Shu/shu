"""Similarity search mixin for query service.

Provides vector-based semantic search over document chunks.
"""

from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from shu.core.logging import get_logger

from ...core.exceptions import ShuException
from .base import measure_execution_time

if TYPE_CHECKING:
    from ...schemas.query import SimilaritySearchRequest

logger = get_logger(__name__)


class SimilaritySearchMixin:
    """Mixin providing vector similarity search."""

    @measure_execution_time
    async def similarity_search(
        self,
        knowledge_base_id: str,
        request: "SimilaritySearchRequest",
        *,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Perform vector similarity search on document chunks.

        Args:
            knowledge_base_id: ID of the knowledge base to search
            request: Similarity search request
            user_id: Optional user attribution for the embedding llm_usage row.
                Threaded down to ``embedding_service.embed_query`` so the row
                lands with the originating user. See SHU-718.

        Returns:
            Dictionary with search results

        """
        try:
            # Verify knowledge base exists
            knowledge_base = await self._verify_knowledge_base(knowledge_base_id)

            # Block vector search on stale or re-embedding KBs
            if knowledge_base.embedding_status != "current":
                from ...core.exceptions import KnowledgeBaseStaleEmbeddingsError

                raise KnowledgeBaseStaleEmbeddingsError(knowledge_base_id, knowledge_base.embedding_status)

            # Extract parameters from request, resolving None to config defaults
            query = request.query
            limit = request.limit if request.limit is not None else self.config_manager.get_rag_max_chunks()
            threshold = request.threshold

            # Preprocess query using unified preprocessing
            processed = self.preprocess_query(query)

            # Log the preprocessing results for debugging (only for direct similarity search calls)
            logger.debug(
                "Similarity search preprocessing: query_len=%d -> processed_len=%d -> terms=%d",
                len(query),
                len(processed["similarity_query"]),
                len(processed["keyword_terms"]),
            )

            # Generate embedding for the processed query
            from ...core.embedding_service import get_embedding_service

            embedding_service = await get_embedding_service()
            query_embedding = await embedding_service.embed_query(processed["similarity_query"], user_id=user_id)

            # Perform vector similarity search via VectorStore
            from ...core.vector_store import get_vector_store

            vector_store = await get_vector_store()
            search_results = await vector_store.search(
                "chunks",
                query_vector=query_embedding,
                db=self.db,
                limit=limit,
                threshold=threshold,
                filters={"knowledge_base_id": knowledge_base_id},
                extra_where=("(chunk_metadata->>'chunk_type' != 'title' " "OR chunk_metadata->>'chunk_type' IS NULL)"),
            )

            if not search_results:
                from ...schemas.query import SimilaritySearchResponse

                return SimilaritySearchResponse(
                    results=[],
                    total_results=0,
                    query=query,
                    threshold=threshold,
                    execution_time=0.0,
                    embedding_model=str(knowledge_base.embedding_model),
                ).model_dump()

            # Build score lookup from vector results
            score_map = {r.id: r.score for r in search_results}
            chunk_ids = list(score_map.keys())

            # Load full chunk + document metadata for matched chunks
            metadata_query = text("""
                SELECT
                    dc.id,
                    dc.document_id,
                    dc.knowledge_base_id,
                    dc.chunk_index,
                    dc.content,
                    dc.char_count,
                    dc.word_count,
                    dc.token_count,
                    dc.start_char,
                    dc.end_char,
                    dc.embedding_model,
                    dc.embedding_created_at,
                    dc.created_at,
                    d.title as document_title,
                    d.source_id,
                    d.source_url,
                    d.file_type,
                    d.source_type,
                    (SELECT COUNT(*) FROM document_chunks dc2
                     WHERE dc2.document_id = dc.document_id
                     AND dc2.knowledge_base_id = dc.knowledge_base_id
                     AND (dc2.chunk_metadata->>'chunk_type' != 'title'
                          OR dc2.chunk_metadata->>'chunk_type' IS NULL)
                    ) as total_content_chunks
                FROM document_chunks dc
                JOIN documents d ON dc.document_id = d.id
                WHERE dc.id = ANY(:chunk_ids)
            """)
            result = await self.db.execute(metadata_query, {"chunk_ids": chunk_ids})
            chunks = result.fetchall()

            # Convert results to DocumentChunkWithScore objects
            # De-duplicate documents while preserving top-k chunks per document

            from ...schemas.document import DocumentChunkWithScore

            # Group chunks by document and keep top-k per document
            document_chunks = {}
            for chunk in chunks:
                doc_id = chunk.document_id
                if doc_id not in document_chunks:
                    document_chunks[doc_id] = []
                document_chunks[doc_id].append(chunk)

            # Get RAG configuration for chunk limits
            rag_config = await self._get_rag_config(knowledge_base_id)
            max_chunks_per_doc = rag_config.get("max_chunks_per_document", 2)

            # Flatten results, maintaining order by similarity score
            results = []
            for _, doc_chunks in document_chunks.items():
                # Sort chunks within each document by similarity score (descending)
                doc_chunks.sort(key=lambda x: score_map.get(x.id, 0.0), reverse=True)
                # Add up to max_chunks_per_doc chunks for this document
                chunks_to_add = doc_chunks[:max_chunks_per_doc]
                for chunk in chunks_to_add:
                    chunk_data = {
                        "id": chunk.id,
                        "document_id": chunk.document_id,
                        "knowledge_base_id": chunk.knowledge_base_id,
                        "chunk_index": chunk.chunk_index,
                        "content": chunk.content,
                        "char_count": chunk.char_count,
                        "word_count": chunk.word_count,
                        "token_count": chunk.token_count,
                        "start_char": chunk.start_char,
                        "end_char": chunk.end_char,
                        "has_embedding": True,
                        "embedding_model": chunk.embedding_model,
                        "embedding_created_at": chunk.embedding_created_at,
                        "created_at": chunk.created_at,
                        "similarity_score": score_map.get(chunk.id, 0.0),
                        "document_title": chunk.document_title or "Unknown Document",
                        "source_id": chunk.source_id,
                        "source_url": chunk.source_url,
                        "file_type": chunk.file_type,
                        "source_type": chunk.source_type,
                        "total_chunks": getattr(chunk, "total_content_chunks", 0),
                    }

                    chunk_obj = DocumentChunkWithScore(**chunk_data)
                    results.append(chunk_obj)

            # Sort final results by score descending (chunks may be interleaved after grouping)
            results.sort(key=lambda x: x.similarity_score, reverse=True)

            from ...schemas.query import SimilaritySearchResponse

            response = SimilaritySearchResponse(
                results=results,
                total_results=len(results),
                query=query,
                threshold=threshold,
                execution_time=0.0,  # Will be set by decorator
                embedding_model=str(knowledge_base.embedding_model),
            )
            return response.model_dump()

        except ShuException:
            # Re-raise ShuException without modification
            raise
        except Exception as e:
            logger.error(f"Failed to perform similarity search: {e}", exc_info=True)
            raise ShuException(f"Failed to perform similarity search: {e!s}", "SIMILARITY_SEARCH_ERROR")
