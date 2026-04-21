"""Keyword search mixin for query service.

Provides term-based matching with title weighting and document-level scoring.
"""

import hashlib
import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from ...core.exceptions import ShuException
from .base import measure_execution_time
from .constants import TITLE_MATCH_STOP_WORDS

logger = logging.getLogger(__name__)


def _redact(text_val: str) -> str:
    """Return a non-reversible fingerprint for log-safe representation of sensitive text."""
    h = hashlib.sha256(text_val.encode()).hexdigest()[:8]
    return f"[len={len(text_val)} hash={h}]"


class KeywordSearchMixin:
    """Mixin providing keyword-based search with title weighting."""

    # TODO: Refactor this function. It's too complex (number of branches and statements).
    @measure_execution_time
    async def keyword_search(  # noqa: PLR0912, PLR0915
        self,
        knowledge_base_id: str,
        query: str,
        limit: int = 10,
        *,
        title_weighting_enabled: bool | None = None,
        title_weight_multiplier: float | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Perform keyword search on document chunks with improved term extraction.

        ``user_id`` is threaded into the title-match precompute ``embed_query``
        and forwarded on into ``_get_title_match_chunks`` (whose own fallback
        ``embed_query`` call picks it up if reached) so any resulting
        llm_usage row attributes to the originating user (SHU-718).
        """
        try:
            # Verify knowledge base exists
            knowledge_base = await self._verify_knowledge_base(knowledge_base_id)

            # Preprocess query using unified preprocessing
            processed = self.preprocess_query(query)

            # Log the preprocessing results for debugging (only for direct keyword search calls)
            logger.debug(
                f"Keyword search preprocessing: query={_redact(query)} -> "
                f"keyword_terms={len(processed['keyword_terms'])} "
                f"filename_terms={len(processed.get('filename_terms', []))}"
            )

            # If all terms were filtered out as stop words AND there are no filename-like tokens, return empty results
            if not processed["keyword_terms"] and not processed.get("filename_terms"):
                logger.info(
                    f"All terms in query {_redact(query)} were filtered as stop words (and no filename terms found), returning empty results"
                )
                from ...schemas.query import QueryResponse, QueryType

                response = QueryResponse(
                    results=[],
                    total_results=0,
                    query=query,
                    query_type=QueryType.KEYWORD,
                    execution_time=0.0,
                    similarity_threshold=0.0,
                    embedding_model=str(knowledge_base.embedding_model),
                    processed_at=datetime.now(UTC),
                )
                return response.model_dump()

            params = {"kb_id": knowledge_base_id, "limit": limit}

            # Get title weighting configuration (request params override KB config)
            kb_config = knowledge_base.get_rag_config()
            effective_title_weighting_enabled = (
                title_weighting_enabled
                if title_weighting_enabled is not None
                else self.config_manager.get_title_weighting_enabled(kb_config=kb_config)
            )
            effective_title_weight_multiplier = (
                title_weight_multiplier
                if title_weight_multiplier is not None
                else self.config_manager.get_title_weight_multiplier(kb_config=kb_config)
            )

            logger.info(
                f"Keyword search title weighting: enabled={effective_title_weighting_enabled}, "
                f"multiplier={effective_title_weight_multiplier}, "
                f"from_request={title_weighting_enabled is not None}"
            )

            # Build title match conditions for enhanced scoring (whole word matches only)
            title_match_conditions = []
            title_params = {}

            # Filter out stop words and short terms for title matching
            meaningful_terms = [
                term
                for term in processed["keyword_terms"]
                if len(term) >= 3 and term.lower() not in TITLE_MATCH_STOP_WORDS
            ]

            for i, term in enumerate(meaningful_terms):
                title_params[f"title_pattern{i}"] = f"\\m{re.escape(term)}\\M"
                title_params[f"title_norm_pattern{i}"] = f"\\m{re.escape(term)}\\M"
                title_params[f"title_like{i}"] = f"%{term}%"
                title_match_conditions.append(
                    f"(d.title ~* :title_pattern{i} OR REGEXP_REPLACE(d.title, '[._-]', ' ', 'g') ~* :title_norm_pattern{i} OR d.title ILIKE :title_like{i})"
                )

            # Also allow literal filename matches like ModernChat.js / foo.py
            filename_terms = processed.get("filename_terms", [])
            for j, fname in enumerate(filename_terms):
                title_params[f"filename_like{j}"] = f"%{fname}%"
                title_params[f"filename_full{j}"] = fname
                title_match_conditions.append(
                    f"(d.title ILIKE :filename_like{j} OR LOWER(d.title) = LOWER(:filename_full{j}))"
                )

            # Add title params to main params
            params.update(title_params)

            title_match_sql = " OR ".join(title_match_conditions) if title_match_conditions else "FALSE"

            # Check if title weighting is enabled and we have title matches
            title_boosted_chunks = []
            if effective_title_weighting_enabled:
                # First, find documents with title matches
                title_match_query = text(f"""
                    SELECT document_id, document_title, title_score
                    FROM (
                        SELECT DISTINCT d.id as document_id, d.title as document_title,
                            CASE
                                WHEN d.title ~* :exact_pattern THEN 10.0
                                WHEN ({title_match_sql}) THEN 8.0
                                ELSE 0.0
                            END as title_score
                        FROM documents d
                        WHERE d.knowledge_base_id = :kb_id
                        AND ({title_match_sql})
                    ) title_matches
                    WHERE title_score > 0
                    ORDER BY title_score DESC
                    LIMIT :max_title_matches
                """)  # nosec # difficult to turn this into sqlalchemy query format, and injection is not possible here  # noqa: S608

                params["exact_pattern"] = f"\\m{re.escape(query)}\\M"
                params["max_title_matches"] = 10
                title_result = await self.db.execute(title_match_query, params)
                title_matches = title_result.fetchall()

                if title_matches:
                    # Use new title-match chunk selection for title-matched documents
                    title_boosted_chunks = []
                    max_chunks_per_doc = 3

                    # Precompute query embedding once for all title-match chunk lookups
                    from ...core.embedding_service import get_embedding_service

                    embedding_service = await get_embedding_service()
                    precomputed_query_embedding = await embedding_service.embed_query(query, user_id=user_id)

                    for title_match in title_matches:
                        doc_chunks = await self._get_title_match_chunks(
                            document_id=title_match.document_id,
                            query=query,
                            max_chunks=max_chunks_per_doc,
                            knowledge_base_id=knowledge_base_id,
                            query_embedding=precomputed_query_embedding,
                            user_id=user_id,
                        )

                        # Convert to the expected format and apply title boost
                        for chunk_data in doc_chunks:
                            title_boost = (float(title_match.title_score) / 10.0) * effective_title_weight_multiplier
                            boosted_score = chunk_data["similarity_score"] + title_boost

                            # Create a chunk-like object for compatibility
                            chunk_obj = type(
                                "Chunk",
                                (),
                                {
                                    "id": chunk_data["chunk_id"],
                                    "document_id": chunk_data["document_id"],
                                    "knowledge_base_id": knowledge_base_id,
                                    "chunk_index": chunk_data["chunk_index"],
                                    "content": chunk_data["content"],
                                    "char_count": len(chunk_data["content"]),
                                    "word_count": len(chunk_data["content"].split()),
                                    "token_count": len(chunk_data["content"].split()),
                                    "start_char": chunk_data["start_char"],
                                    "end_char": chunk_data["end_char"],
                                    "embedding_model": None,
                                    "embedding_created_at": None,
                                    "created_at": chunk_data["created_at"],
                                    "document_title": chunk_data["document_title"],
                                    "source_id": chunk_data["source_id"],
                                    "source_url": chunk_data["source_url"],
                                    "file_type": chunk_data["file_type"],
                                    "source_type": None,
                                    "chunk_metadata": None,
                                    "keyword_score": min(10.0, boosted_score),
                                },
                            )()
                            title_boosted_chunks.append(chunk_obj)

            # Always run regular content search (title weighting merges, doesn't replace)
            content_chunks = []
            # Build parameterized query with safe parameter binding (SECURITY FIX)
            # Create individual parameters for each term to avoid SQL injection
            params = {"kb_id": knowledge_base_id, "limit": limit}
            where_conditions = []

            for i, term in enumerate(processed["keyword_terms"]):
                # Content pattern matching - treat _, ., and - as separators
                content_param = f"content_pattern_{i}"
                params[content_param] = f"(^|[^A-Za-z0-9]){re.escape(term)}([^A-Za-z0-9]|$)"
                where_conditions.append(f"dc.content ~* :{content_param}")

                # Title pattern matching for meaningful terms
                if len(term) >= 3 and term.lower() not in TITLE_MATCH_STOP_WORDS:
                    title_param = f"title_pattern_{i}"
                    title_norm_param = f"title_norm_pattern_{i}"
                    title_like_param = f"title_like_{i}"
                    params[title_param] = f"\\m{re.escape(term)}\\M"
                    params[title_norm_param] = f"\\m{re.escape(term)}\\M"
                    params[title_like_param] = f"%{term}%"
                    where_conditions.append(
                        f"(d.title ~* :{title_param} OR REGEXP_REPLACE(d.title, '[._-]', ' ', 'g') ~* :{title_norm_param} OR d.title ILIKE :{title_like_param})"
                    )

            # Include literal filename matches (e.g., ModernChat.js, foo.py)
            for j, fname in enumerate(processed.get("filename_terms", [])):
                p = f"file_like_{j}"
                params[p] = f"%{fname}%"
                where_conditions.append(f"d.title ILIKE :{p}")

            if where_conditions:
                # Build the WHERE clause safely - all parameters are pre-defined
                where_clause = " OR ".join(where_conditions)

                # Build exact pattern parameter
                params["exact_pattern"] = f"\\m{re.escape(query)}\\M"

                # Execute parameterized query (SECURE - all user input is parameterized)
                keyword_query = text(f"""
                    SELECT
                        dc.id, dc.document_id, dc.knowledge_base_id, dc.chunk_index,
                        dc.content, dc.char_count, dc.word_count, dc.token_count,
                        dc.start_char, dc.end_char, dc.embedding_model, dc.embedding_created_at,
                        dc.created_at, d.title as document_title, d.source_id, d.source_url,
                        d.file_type, d.source_type, dc.chunk_metadata,
                        CASE
                            WHEN dc.content ~* :exact_pattern THEN 1.0
                            WHEN ({where_clause}) THEN 0.8
                            ELSE 0.4
                        END as keyword_score
                    FROM document_chunks dc
                    JOIN documents d ON dc.document_id = d.id
                    WHERE dc.knowledge_base_id = :kb_id
                    AND ({where_clause})
                    ORDER BY keyword_score DESC, dc.chunk_index
                    LIMIT :limit
                """)  # nosec # difficult to turn this into sqlalchemy query format, and injection is not
                # possible here

                result = await self.db.execute(keyword_query, params)
                content_chunks = result.fetchall()

            # Merge title-boosted chunks with content chunks, dedup by chunk id
            if effective_title_weighting_enabled and title_boosted_chunks:
                seen_ids = {c.id for c in title_boosted_chunks}
                for chunk in content_chunks:
                    if chunk.id not in seen_ids:
                        seen_ids.add(chunk.id)
                        title_boosted_chunks.append(chunk)
                title_boosted_chunks.sort(key=lambda x: x.keyword_score, reverse=True)
                chunks = title_boosted_chunks[:limit]
            else:
                chunks = content_chunks

            if not chunks:
                # Return empty results if no matches found
                from ...schemas.query import QueryResponse, QueryType

                response = QueryResponse(
                    results=[],
                    total_results=0,
                    query=query,
                    query_type=QueryType.KEYWORD,
                    execution_time=0.0,
                    similarity_threshold=0.0,
                    embedding_model=str(knowledge_base.embedding_model),
                    processed_at=datetime.now(UTC),
                )
                return response.model_dump()

            # Convert results to QueryResult format
            from ...schemas.query import QueryResponse, QueryResult, QueryType

            query_results = []
            for chunk in chunks:
                query_result = QueryResult(
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    document_title=chunk.document_title or "Unknown Document",
                    content=chunk.content,
                    similarity_score=chunk.keyword_score,
                    chunk_index=chunk.chunk_index,
                    start_char=chunk.start_char,
                    end_char=chunk.end_char,
                    file_type=chunk.file_type or "txt",
                    source_url=chunk.source_url,
                    source_id=chunk.source_id,
                    created_at=chunk.created_at,
                )
                query_results.append(query_result)

            response = QueryResponse(
                results=query_results,
                total_results=len(query_results),
                query=query,
                query_type=QueryType.KEYWORD,
                execution_time=0.0,  # Will be set by decorator
                similarity_threshold=0.0,
                embedding_model=str(knowledge_base.embedding_model),
                processed_at=datetime.now(UTC),
            )
            return response.model_dump()
        except Exception as e:
            logger.error(f"Failed to perform keyword search: {e}", exc_info=True)
            raise ShuException(f"Failed to perform keyword search: {e!s}", "KEYWORD_SEARCH_ERROR")

    async def _get_title_match_chunks(
        self,
        document_id: str,
        query: str,
        max_chunks: int,
        knowledge_base_id: str,
        query_embedding: list[float] | None = None,
        *,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """For title-matched documents, find the most relevant chunks within that document.
        Uses the original query to find semantically and keyword relevant chunks.
        Always returns the top N chunks regardless of absolute scores (trusting title match).
        """
        try:
            # Get all chunks from this specific document (excluding title chunks)
            chunks_query = text("""
                SELECT
                    dc.id, dc.document_id, dc.knowledge_base_id, dc.chunk_index,
                    dc.content, dc.char_count, dc.word_count, dc.token_count,
                    dc.start_char, dc.end_char, dc.embedding_model, dc.embedding_created_at,
                    dc.created_at, d.title as document_title, d.source_id, d.source_url,
                    d.file_type, d.source_type, dc.chunk_metadata, dc.embedding,
                    (SELECT COUNT(*) FROM document_chunks dc2
                     WHERE dc2.document_id = dc.document_id
                     AND dc2.knowledge_base_id = dc.knowledge_base_id
                     AND (dc2.chunk_metadata->>'chunk_type' != 'title' OR dc2.chunk_metadata->>'chunk_type' IS NULL)
                    ) as total_content_chunks
                FROM document_chunks dc
                JOIN documents d ON dc.document_id = d.id
                WHERE dc.document_id = :doc_id
                AND dc.knowledge_base_id = :kb_id
                AND dc.embedding IS NOT NULL
                AND (dc.chunk_metadata->>'chunk_type' != 'title' OR dc.chunk_metadata->>'chunk_type' IS NULL)
                ORDER BY dc.chunk_index
            """)

            result = await self.db.execute(chunks_query, {"doc_id": document_id, "kb_id": knowledge_base_id})
            chunks = result.fetchall()

            if not chunks:
                return []

            # Score each chunk against the original query using existing logic
            scored_chunks = []

            # Get query embedding for similarity scoring (reuse precomputed if available)
            from scipy.spatial.distance import cosine

            if query_embedding is None:
                from ...core.embedding_service import get_embedding_service

                embedding_service = await get_embedding_service()
                query_embedding = await embedding_service.embed_query(query, user_id=user_id)

            # Preprocess query and get weights once (loop-invariant)
            processed = self.preprocess_query(query)
            keyword_terms = processed["keyword_terms"]
            similarity_weight = self.config_manager.get_hybrid_similarity_weight()
            keyword_weight = self.config_manager.get_hybrid_keyword_weight()

            for chunk in chunks:
                # Calculate similarity score
                chunk_embedding = chunk.embedding
                if isinstance(chunk_embedding, str):
                    import json

                    chunk_embedding = json.loads(chunk_embedding)

                similarity_score = float(1 - cosine(query_embedding, chunk_embedding))
                similarity_score = max(0, similarity_score)  # Ensure non-negative

                keyword_score = self._calculate_keyword_score(chunk.content, keyword_terms)
                combined_score = similarity_score * similarity_weight + keyword_score * keyword_weight

                scored_chunks.append((chunk, combined_score))

            # Sort by relevance and return top N (always return something for title matches)
            scored_chunks.sort(key=lambda x: x[1], reverse=True)
            top_chunks = scored_chunks[:max_chunks]

            # Convert to QueryResult format
            results = []
            total_chunks = chunks[0].total_content_chunks if chunks else 0
            for chunk, score in top_chunks:
                result_dict = {
                    "chunk_id": chunk.id,
                    "document_id": chunk.document_id,
                    "document_title": chunk.document_title or "Unknown Document",
                    "content": chunk.content,
                    "similarity_score": score,
                    "chunk_index": chunk.chunk_index,
                    "start_char": chunk.start_char,
                    "end_char": chunk.end_char,
                    "file_type": chunk.file_type or "txt",
                    "source_url": chunk.source_url,
                    "source_id": chunk.source_id,
                    "created_at": chunk.created_at,
                    "total_chunks": total_chunks,
                }
                results.append(result_dict)

            return results

        except Exception as e:
            logger.error(f"Failed to get title match chunks for document {document_id}: {e}")
            return []

    def _calculate_keyword_score(self, content: str, keyword_terms: list[str]) -> float:
        """Calculate keyword match score for a chunk."""
        if not keyword_terms:
            return 0.0

        content_lower = content.lower()
        matches = 0
        total_terms = len(keyword_terms)

        for term in keyword_terms:
            if term.lower() in content_lower:
                matches += 1

        return matches / total_terms if total_terms > 0 else 0.0
