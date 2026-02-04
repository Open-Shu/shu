"""Query service for Shu RAG Backend.

Handles document queries, similarity search, and hybrid search operations.
"""

import functools
import logging
import re
import time
from datetime import UTC
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..core.config import ConfigurationManager
from ..core.exceptions import ShuException
from ..models.document import Document
from ..models.knowledge_base import KnowledgeBase
from ..schemas.query import QueryRequest, SimilaritySearchRequest

logger = logging.getLogger(__name__)

# Comprehensive stop word set for all search types
COMPREHENSIVE_STOP_WORDS = {
    # Articles
    "a",
    "an",
    "the",
    # Pronouns
    "i",
    "you",
    "he",
    "she",
    "it",
    "we",
    "they",
    "me",
    "him",
    "her",
    "us",
    "them",
    "my",
    "your",
    "his",
    "its",
    "our",
    "their",
    "mine",
    "yours",
    "hers",
    "ours",
    "theirs",
    "myself",
    "yourself",
    "himself",
    "herself",
    "itself",
    "ourselves",
    "yourselves",
    "themselves",
    "this",
    "that",
    "these",
    "those",
    # Question Words
    "what",
    "when",
    "where",
    "who",
    "whom",
    "whose",
    "which",
    "why",
    "how",
    # Prepositions
    "in",
    "on",
    "at",
    "to",
    "for",
    "of",
    "with",
    "by",
    "from",
    "up",
    "about",
    "into",
    "through",
    "during",
    "before",
    "after",
    "above",
    "below",
    "between",
    "among",
    "within",
    "without",
    "against",
    "toward",
    "towards",
    "upon",
    "under",
    "over",
    "across",
    "along",
    "around",
    "behind",
    "beneath",
    "beside",
    "beyond",
    "inside",
    "outside",
    "near",
    "off",
    "out",
    # Conjunctions
    "and",
    "or",
    "but",
    "nor",
    "yet",
    "so",
    "because",
    "although",
    "unless",
    "while",
    "whereas",
    "whether",
    "if",
    "then",
    "else",
    "though",
    "even",  # Auxiliary Verbs
    "am",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "must",
    "can",
    "shall",
    "ought",
    "need",
    "dare",
    "used",
    # Common Verbs (basic forms)
    "get",
    "got",
    "getting",
    "go",
    "went",
    "gone",
    "going",
    "come",
    "came",
    "coming",
    "make",
    "made",
    "making",
    "take",
    "took",
    "taken",
    "taking",
    "see",
    "saw",
    "seen",
    "know",
    "knew",
    "known",
    "think",
    "thought",
    "say",
    "said",
    "tell",
    "told",
    "want",
    "wanted",
    "like",
    "liked",
    "look",
    "looked",
    "find",
    "found",
    # Common Adjectives
    "good",
    "bad",
    "big",
    "small",
    "new",
    "old",
    "high",
    "low",
    "long",
    "short",
    "first",
    "last",
    "next",
    "previous",
    "current",
    "same",
    "different",
    "other",
    "many",
    "much",
    "few",
    "little",
    "more",
    "most",
    "less",
    "least",
    # Common Adverbs
    "very",
    "really",
    "quite",
    "rather",
    "too",
    "as",
    "just",
    "only",
    "still",
    "already",
    "again",
    "ever",
    "never",
    "always",
    "sometimes",
    "often",
    "usually",
    "rarely",
    "seldom",
    "now",
    "here",
    "there",
    # Numbers and Quantifiers
    "one",
    "two",
    "three",
    "second",
    "third",
    "all",
    "some",
    "any",
    "none",
    "each",
    "every",
    "both",
    "either",
    "neither",
    "several",
    "various",  # Time Words
    "today",
    "yesterday",
    "tomorrow",
    "since",
    "until",
    "ago",
    "later",
    "earlier",
    "recently",
    # Place Words
    "everywhere",
    "somewhere",
    "nowhere",
    "anywhere",
    "home",
    "away",
    "abroad",
    "upstairs",
    "downstairs",
    # Other Common Words
    "yes",
    "no",
    "not",
    "n't",
    "also",
    "well",
    "right",
    "wrong",
    "true",
    "false",
    "real",
    "actual",
    "possible",
    "impossible",
    "important",
    "necessary",
    "available",
    "ready",
    "sure",
    "certain",
    "likely",
    "probably",
    "maybe",
    "perhaps",
    "possibly",
    "definitely",
    "certainly",
    # Greetings and Social Words
    "hi",
    "hello",
    "hey",
    "bye",
    "goodbye",
    "thanks",
    "thank",
    "please",
    "sorry",
    "ok",
    "okay",
    "cool",
    "nice",
    "great",
    "awesome",
    "perfect",
    "excellent",
    "fine",
    "alright",
    "yep",
    "yeah",
    "nope",
    "wow",
    "oh",
    "ah",
    "hmm",
    # Contractions
    "don't",
    "doesn't",
    "didn't",
    "won't",
    "wouldn't",
    "couldn't",
    "shouldn't",
    "can't",
    "isn't",
    "aren't",
    "wasn't",
    "weren't",
    "hasn't",
    "haven't",
    "hadn't",
    "mustn't",
    "shan't",
    "let's",
    "that's",
    "it's",
    "he's",
    "she's",
    "we're",
    "they're",
    "i'm",
    "you're",
    "i'll",
    "you'll",
    "he'll",
    "she'll",
    "we'll",
    "they'll",
    "i've",
    "you've",
    "we've",
    "they've",
    "i'd",
    "you'd",
    "he'd",
    "she'd",
    "we'd",
    "they'd",
}


def measure_execution_time(func):
    """Measure execution time of async methods decorator.

    This decorator automatically measures the execution time of the decorated method
    and adds it to the response if the response is a dictionary or has an execution_time attribute.
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any):
        start_time = time.time()
        result = await func(*args, **kwargs)
        execution_time = time.time() - start_time

        # Add execution time to result if it's a dictionary
        if isinstance(result, dict):
            result["execution_time"] = execution_time
        # For Pydantic models, try to set execution_time attribute
        elif hasattr(result, "execution_time"):
            result.execution_time = execution_time

        return result

    return wrapper


class QueryService:
    """Service for querying documents and performing search operations."""

    def __init__(self, db: AsyncSession, config_manager: ConfigurationManager) -> None:
        self.db = db
        self.config_manager = config_manager

    def extract_key_terms(self, query: str, stop_words: set) -> list:
        """Extract meaningful terms from query, filtering out stop words.

        Args:
            query: Original user query
            stop_words: Set of stop words to filter out

        Returns:
            List of meaningful terms

        """
        # Extract all potential terms (words, numbers, codes with hyphens/commas)
        all_terms = re.findall(r"\b[\w\-.,]+\b", query.lower())

        # Filter and prioritize terms
        key_terms = []
        for term in all_terms:
            # Skip stop words
            if term in stop_words:
                continue

            # Prioritize technical patterns (case-insensitive matching)
            if (
                re.match(r"^[a-z]{2,}", term)
                or re.match(r"^[a-z]+\d+", term)
                or re.match(r"^\d+[a-z]+", term)
                or re.match(r"^[a-z]+-[a-z0-9]+", term)
                or len(term) > 3
            ):  # All caps (like "MXB") - check lowercase
                key_terms.append(term)

        return key_terms if key_terms else [query]  # Fallback to original query

    def extract_key_terms_preserve_case(self, query: str, stop_words: set) -> list:
        """Extract meaningful terms from query, preserving original case for technical terms.

        Args:
            query: Original user query
            stop_words: Set of stop words to filter out

        Returns:
            List of meaningful terms with preserved case

        """
        # Extract all potential terms with original case
        # Split on word boundaries and clean up punctuation
        # Handle technical identifiers like "MXB-22,510" where comma is part of number notation
        # Pattern explanation:
        # - [A-Za-z0-9]+ : Start with alphanumeric
        # - (?:[-][A-Za-z0-9]+)* : Allow hyphens between alphanumeric parts
        # - (?:[,][0-9]+)* : Allow commas followed by numbers (for number notation like "22,510")
        # - (?:[.][0-9]+)? : Allow decimal points
        raw_terms = re.findall(r"[A-Za-z0-9]+(?:[-][A-Za-z0-9]+)*(?:[,][0-9]+)*(?:[.][0-9]+)?", query)
        all_terms = []
        for term in raw_terms:
            # Clean up leading/trailing punctuation but preserve internal punctuation
            cleaned = re.sub(r"^[^\w]+|[^\w]+$", "", term)
            if cleaned:
                all_terms.append(cleaned)

        # Filter and prioritize terms with enhanced technical term handling
        key_terms = []
        for term in all_terms:
            # Skip stop words (case-insensitive check)
            if term.lower() in stop_words:
                continue

            # Prioritize technical patterns with preserved case
            if (
                re.match(r"^[A-Z]{2,}", term)
                or re.match(r"^[A-Za-z]+\d+", term)
                or re.match(r"^\d+[A-Za-z]+", term)
                or re.match(r"^[A-Za-z]+-[A-Za-z0-9]+", term)
                or len(term) >= 3
                or (len(term) == 2 and term.isupper())
            ):  # All caps (like "ASCII", "NASA")
                key_terms.append(term)

        # If no key terms found, try to extract meaningful parts from the original query
        if not key_terms:
            # Split the original query and include all non-stop-word parts
            fallback_terms = []
            for word in query.split():
                clean_word = re.sub(r"[^\w\s-]", "", word).strip()
                if clean_word and clean_word.lower() not in stop_words and len(clean_word) >= 2:
                    fallback_terms.append(clean_word)
            # If all terms are stop words, return empty list instead of original query
            return fallback_terms

        return key_terms

    def preprocess_query(self, query: str) -> dict:
        """Preprocess query using the same comprehensive stop word set for all search types.

        Args:
            query: Original user query

        Returns:
            dict with processed query and extracted terms

        """
        # Use the same comprehensive stop word set for all search types
        # Use the case-preserving method for better technical term handling
        key_terms = self.extract_key_terms_preserve_case(query, COMPREHENSIVE_STOP_WORDS)

        # Extract any filename-like tokens (e.g., ModernChat.js, foo.py, README.md) preserving case
        # Keep scope tight to the extensions we actively support in KB title matching
        try:
            filename_terms = re.findall(r"([A-Za-z0-9_][A-Za-z0-9_\-\.]*\.(?:md|py|js))", query)
            # Deduplicate while preserving order
            seen = set()
            filename_terms = [t for t in filename_terms if not (t.lower() in seen or seen.add(t.lower()))]
        except Exception:
            filename_terms = []

        # For similarity search, use the original query to preserve semantic context
        # Stop-word removal hurts similarity search by losing semantic information
        similarity_query = query

        return {
            "original_query": query,
            "similarity_query": similarity_query,
            "keyword_terms": key_terms,
            "filename_terms": filename_terms,
            "all_terms": key_terms,
        }

    async def _verify_knowledge_base(self, knowledge_base_id: str) -> KnowledgeBase:
        """Verify knowledge base exists and return it.

        Args:
            knowledge_base_id: ID of the knowledge base to verify

        Returns:
            KnowledgeBase instance if found

        Raises:
            KnowledgeBaseNotFoundError: If knowledge base doesn't exist

        """
        from ..utils import KnowledgeBaseVerifier

        return await KnowledgeBaseVerifier.verify_exists(self.db, knowledge_base_id)

    async def _get_rag_config(self, knowledge_base_id: str) -> dict[str, Any]:
        """Get RAG configuration for a knowledge base.

        Args:
            knowledge_base_id: Knowledge base ID

        Returns:
            Dictionary with RAG configuration settings

        """
        try:
            from ..services.knowledge_base_service import KnowledgeBaseService

            kb_service = KnowledgeBaseService(self.db, self.config_manager)
            rag_config_response = await kb_service.get_rag_config(knowledge_base_id)
            return rag_config_response.model_dump()
        except Exception as e:
            logger.warning(f"Failed to get RAG config for KB {knowledge_base_id}: {e}")
            # Return default configuration using ConfigurationManager
            default_config = self.config_manager.get_rag_config_dict()
            default_config["version"] = "1.0"  # Add version for compatibility
            return default_config

    @measure_execution_time
    async def get_document_details(
        self, knowledge_base_id: str, document_id: str, include_chunks: bool = False
    ) -> Document | None:
        """Get detailed information about a specific document.

        Args:
            knowledge_base_id: ID of the knowledge base
            document_id: ID of the document to retrieve
            include_chunks: Whether to include document chunks

        Returns:
            Document object with optional chunks

        """
        try:
            # Verify knowledge base exists
            await self._verify_knowledge_base(knowledge_base_id)

            logger.info(
                "Getting document details",
                extra={
                    "kb_id": knowledge_base_id,
                    "document_id": document_id,
                    "include_chunks": include_chunks,
                },
            )

            # Build query to get document
            query = select(Document).where(
                and_(Document.id == document_id, Document.knowledge_base_id == knowledge_base_id)
            )

            # Include chunks if requested
            if include_chunks:
                query = query.options(selectinload(Document.chunks))

            result = await self.db.execute(query)
            document = result.scalar_one_or_none()

            if not document:
                logger.warning(
                    "Document not found",
                    extra={"kb_id": knowledge_base_id, "document_id": document_id},
                )
                return None

            logger.info(
                "Retrieved document details",
                extra={
                    "kb_id": knowledge_base_id,
                    "document_id": document_id,
                    "title": document.title,
                    "chunks_included": include_chunks,
                    "chunk_count": len(document.chunks) if include_chunks and document.chunks else 0,
                },
            )

            return document

        except Exception as e:
            logger.error(
                "Failed to get document details",
                extra={"kb_id": knowledge_base_id, "document_id": document_id, "error": str(e)},
            )
            raise

    @measure_execution_time
    async def list_documents(
        self,
        knowledge_base_id: str,
        limit: int = 50,
        offset: int = 0,
        source_type: str | None = None,
        file_type: str | None = None,
    ) -> dict[str, Any]:
        """List documents in a knowledge base with optional filtering.

        Args:
            knowledge_base_id: ID of the knowledge base
            limit: Maximum number of documents to return
            offset: Number of documents to skip for pagination
            source_type: Optional filter by source type
            file_type: Optional filter by file type

        Returns:
            Dictionary with documents and metadata

        Raises:
            KnowledgeBaseNotFoundError: If knowledge base doesn't exist

        """
        try:
            # Verify knowledge base exists
            await self._verify_knowledge_base(knowledge_base_id)

            # Build query
            query = select(Document).where(Document.knowledge_base_id == knowledge_base_id)

            # Apply filters
            if source_type:
                query = query.where(Document.source_type == source_type)

            if file_type:
                query = query.where(Document.file_type == file_type)

            # Get total count
            count_result = await self.db.execute(select(func.count()).select_from(query.subquery()))
            total_count = count_result.scalar()

            # Apply pagination and get documents
            query = query.offset(offset).limit(limit)
            result = await self.db.execute(query)
            documents = result.scalars().all()

            logger.info(
                "Listed documents",
                extra={
                    "kb_id": knowledge_base_id,
                    "total": total_count,
                    "returned": len(documents),
                    "limit": limit,
                    "offset": offset,
                },
            )

            # Return structured response with execution time (added by decorator)
            return {
                "documents": documents,
                "total_count": total_count,
                "limit": limit,
                "offset": offset,
                "source_type": source_type,
                "file_type": file_type,
            }

        except ShuException:
            # Re-raise ShuException without modification
            raise
        except Exception as e:
            logger.error(f"Failed to list documents: {e}", exc_info=True)
            raise ShuException(f"Failed to list documents: {e!s}", "DOCUMENT_LIST_ERROR")

    @measure_execution_time
    async def similarity_search(self, knowledge_base_id: str, request: "SimilaritySearchRequest") -> dict[str, Any]:
        """Perform vector similarity search on document chunks.

        Args:
            knowledge_base_id: ID of the knowledge base to search
            request: Similarity search request

        Returns:
            Dictionary with search results

        """
        try:
            # Verify knowledge base exists
            knowledge_base = await self._verify_knowledge_base(knowledge_base_id)

            # Extract parameters from request
            query = request.query
            limit = request.limit
            threshold = request.threshold

            # Preprocess query using unified preprocessing
            processed = self.preprocess_query(query)

            # Log the preprocessing results for debugging (only for direct similarity search calls)
            logger.debug(
                f"Similarity search preprocessing: original='{query[:100]}...' -> processed='{processed['similarity_query'][:100]}...' -> terms={len(processed['keyword_terms'])} terms"
            )

            # Generate embedding for the processed query using the knowledge base's embedding model
            from ..services.rag_processing_service import RAGProcessingService

            # Use the knowledge base's embedding model to ensure consistency
            rag_service = RAGProcessingService.get_instance(embedding_model=str(knowledge_base.embedding_model))
            query_embedding = rag_service.model.encode([processed["similarity_query"]])[0].tolist()

            # Perform vector similarity search using pgvector
            from pgvector.sqlalchemy import Vector
            from sqlalchemy import bindparam, text

            # Use cosine distance for similarity (pgvector's <-> operator)
            # Lower distance = higher similarity
            # Note: Cosine distance ranges from 0 (identical) to 2 (opposite)
            # We convert to similarity score: 1 - distance (so 1 = identical, -1 = opposite)
            # Return multiple chunks per document for better context coverage
            similarity_query = text("""
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
                    GREATEST(0, 1 - (dc.embedding <=> :query_embedding)) as similarity_score,
                    (SELECT COUNT(*) FROM document_chunks dc2
                     WHERE dc2.document_id = dc.document_id
                     AND dc2.knowledge_base_id = dc.knowledge_base_id
                     AND (dc2.chunk_metadata->>'chunk_type' != 'title' OR dc2.chunk_metadata->>'chunk_type' IS NULL)
                    ) as total_content_chunks
                FROM document_chunks dc
                JOIN documents d ON dc.document_id = d.id
                WHERE dc.knowledge_base_id = :kb_id
                AND dc.embedding IS NOT NULL
                AND (dc.chunk_metadata->>'chunk_type' != 'title' OR dc.chunk_metadata->>'chunk_type' IS NULL)
                AND 1 - (dc.embedding <=> :query_embedding) >= :threshold
                ORDER BY dc.embedding <=> :query_embedding
                LIMIT :limit
            """)

            # Bind the query_embedding as a Vector type
            similarity_query = similarity_query.bindparams(bindparam("query_embedding", type_=Vector(384)))

            result = await self.db.execute(
                similarity_query,
                {
                    "kb_id": knowledge_base_id,
                    "query_embedding": query_embedding,
                    "threshold": threshold,
                    "limit": limit,
                },
            )

            chunks = result.fetchall()

            if not chunks:
                # Return empty results if no matches found
                from ..schemas.query import SimilaritySearchResponse

                return SimilaritySearchResponse(
                    results=[],
                    total_results=0,
                    query=query,
                    threshold=threshold,
                    execution_time=0.0,
                    embedding_model=str(knowledge_base.embedding_model),
                ).model_dump()

            # Convert results to DocumentChunkWithScore objects
            # De-duplicate documents while preserving top-k chunks per document

            from ..schemas.document import DocumentChunkWithScore

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
            # Document deduplication: Limit chunks per document based on configuration
            # This prevents the same document from appearing multiple times while allowing multiple relevant chunks
            for _, doc_chunks in document_chunks.items():
                # Sort chunks within each document by similarity score (descending)
                doc_chunks.sort(key=lambda x: float(x.similarity_score), reverse=True)
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
                        "has_embedding": True,  # Since we filtered for embedding IS NOT NULL
                        "embedding_model": chunk.embedding_model,
                        "embedding_created_at": chunk.embedding_created_at,
                        "created_at": chunk.created_at,
                        "similarity_score": float(chunk.similarity_score),
                        "document_title": chunk.document_title or "Unknown Document",
                        "source_id": chunk.source_id,
                        "source_url": chunk.source_url,
                        "file_type": chunk.file_type,
                        "source_type": chunk.source_type,
                        "total_chunks": getattr(chunk, "total_content_chunks", 0),
                    }

                    # Create proper DocumentChunkWithScore object
                    chunk_obj = DocumentChunkWithScore(**chunk_data)
                    results.append(chunk_obj)

            from ..schemas.query import SimilaritySearchResponse

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

            # Extract parameters from request
            query = request.query
            search_type = request.query_type  # Already a string
            limit = request.limit

            logger.info(f"query_documents: search_type={search_type}, query='{(query or '')[:50]}...', limit={limit}")

            # Perform search based on type
            if search_type == "similarity":
                # Create SimilaritySearchRequest from QueryRequest
                similarity_request = SimilaritySearchRequest(
                    query=query,
                    limit=limit,
                    threshold=request.similarity_threshold or 0.0,
                    include_embeddings=getattr(request, "include_embeddings", False),
                    document_ids=None,
                    file_types=None,
                    created_after=None,
                    created_before=None,
                )
                similarity_response = await self.similarity_search(knowledge_base_id, similarity_request)

                # Convert SimilaritySearchResponse to QueryResponse format
                from datetime import datetime

                from ..schemas.query import QueryResponse, QueryResult, QueryType

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
                query_response = await self.keyword_search(knowledge_base_id, query, limit)
            elif search_type == "hybrid":
                query_response = await self.hybrid_search(
                    knowledge_base_id, query, limit, request.similarity_threshold or 0.0
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

    async def _maybe_escalate_full_documents(
        self,
        knowledge_base: KnowledgeBase,
        rag_config: dict[str, Any],
        query: str,
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """If configured, escalate top documents to full text with token cap enforcement.
        Returns an escalation dict suitable to embed in API response.
        """
        try:
            fetch_full = rag_config.get("fetch_full_documents", False)
            if not fetch_full:
                return {"enabled": False}

            max_docs = int(rag_config.get("full_doc_max_docs", 1))
            token_cap = int(rag_config.get("full_doc_token_cap", 8000))

            # Deduplicate in original order by document_id
            doc_ids: list[str] = []
            for r in results:
                doc_id = r.get("document_id")
                if doc_id and doc_id not in doc_ids:
                    doc_ids.append(doc_id)
                if len(doc_ids) >= max_docs:
                    break

            if not doc_ids:
                return {"enabled": False}

            # Fetch full documents
            from sqlalchemy import select

            docs_result = await self.db.execute(
                select(Document).where(and_(Document.knowledge_base_id == knowledge_base.id, Document.id.in_(doc_ids)))
            )
            docs = list(docs_result.scalars().all())
            doc_map = {d.id: d for d in docs}

            escalated_docs: list[dict[str, Any]] = []
            total_tokens = 0
            for did in doc_ids:
                d = doc_map.get(did)
                if not d:
                    continue
                content = d.content or ""
                # Estimate tokens using words; we document this limitation
                est_tokens = d.word_count if d.word_count is not None else len(content.split())

                if est_tokens <= token_cap:
                    escalated_docs.append(
                        {
                            "document_id": d.id,
                            "title": d.title,
                            "token_count_estimated": int(est_tokens),
                            "token_cap": token_cap,
                            "content": content,
                            "segments": None,
                            "token_cap_enforced": False,
                        }
                    )
                    total_tokens += int(est_tokens)
                else:
                    # Segment by simple word-slices
                    words = content.split()
                    allowed = max(token_cap, 0)
                    segment_words = words[:allowed]
                    segment_text = " ".join(segment_words)
                    escalated_docs.append(
                        {
                            "document_id": d.id,
                            "title": d.title,
                            "token_count_estimated": int(est_tokens),
                            "token_cap": token_cap,
                            "content": None,
                            "segments": [segment_text],
                            "token_cap_enforced": True,
                        }
                    )
                    total_tokens += token_cap

            return {
                "enabled": True,
                "reason": "kb_config.fetch_full_documents",
                "max_docs": max_docs,
                "token_cap": token_cap,
                "avg_tokens_escalated": (total_tokens / max(len(escalated_docs), 1)),
                "docs": escalated_docs,
            }
        # TODO: Evaluate if this needs to actvually be here.
        # except ShuException:
        #     # Re-raise shu exception without modification
        #     raise
        except Exception as e:
            logger.warning(f"Full-doc escalation failed: {e}")
            return {"enabled": False, "error": str(e)}

    # TODO: Refactor this function. It's too complex (number of branches and statements).
    @measure_execution_time
    async def keyword_search(self, knowledge_base_id: str, query: str, limit: int = 10) -> dict[str, Any]:  # noqa: PLR0912, PLR0915
        """Perform keyword search on document chunks with improved term extraction."""
        try:
            # Verify knowledge base exists
            knowledge_base = await self._verify_knowledge_base(knowledge_base_id)

            # Preprocess query using unified preprocessing
            processed = self.preprocess_query(query)

            # Log the preprocessing results for debugging (only for direct keyword search calls)
            logger.debug(
                f"Keyword search preprocessing: original='{query[:100]}...' -> processed='{processed['similarity_query'][:100]}...' -> terms={len(processed['keyword_terms'])} terms"
            )

            # If all terms were filtered out as stop words AND there are no filename-like tokens, return empty results
            if not processed["keyword_terms"] and not processed.get("filename_terms"):
                logger.info(
                    f"All terms in query '{query}' were filtered as stop words (and no filename terms found), returning empty results"
                )
                from datetime import datetime

                from ..schemas.query import QueryResponse, QueryType

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

            # Build SQL with extracted terms
            where_clauses = []
            params = {"kb_id": knowledge_base_id, "limit": limit}

            for i, term in enumerate(processed["keyword_terms"]):
                # Treat underscores, dots, and hyphens as separators in code/text; match term as a token
                content_pattern = f"(^|[^A-Za-z0-9]){re.escape(term)}([^A-Za-z0-9]|$)"
                params[f"pattern{i}"] = content_pattern
                where_clauses.append(f"dc.content ~* :pattern{i}")

                # Only add title matching for meaningful terms (3+ chars, not stop words)
                if len(term) >= 3 and term.lower() not in {
                    "the",
                    "and",
                    "for",
                    "are",
                    "but",
                    "not",
                    "you",
                    "all",
                    "can",
                    "had",
                    "her",
                    "was",
                    "one",
                    "our",
                    "out",
                    "day",
                    "get",
                    "has",
                    "him",
                    "his",
                    "how",
                    "its",
                    "may",
                    "new",
                    "now",
                    "old",
                    "see",
                    "two",
                    "who",
                    "boy",
                    "did",
                    "she",
                    "use",
                    "way",
                    "what",
                    "when",
                    "with",
                    "have",
                    "this",
                    "will",
                    "your",
                    "from",
                    "they",
                    "know",
                    "want",
                    "been",
                    "good",
                    "much",
                    "some",
                    "time",
                    "very",
                    "come",
                    "here",
                    "just",
                    "like",
                    "long",
                    "make",
                    "many",
                    "over",
                    "such",
                    "take",
                    "than",
                    "them",
                    "well",
                    "were",
                }:
                    params[f"title_pattern{i}"] = f"\\m{re.escape(term)}\\M"
                    params[f"title_norm_pattern{i}"] = f"\\m{re.escape(term)}\\M"
                    params[f"title_like{i}"] = f"%{term}%"
                    where_clauses.append(
                        f"(d.title ~* :title_pattern{i} OR REGEXP_REPLACE(d.title, '[._-]', ' ', 'g') ~* :title_norm_pattern{i} OR d.title ILIKE :title_like{i})"
                    )

            # Note: where_clauses used for building SQLAlchemy conditions below

            # Get title weighting configuration
            kb_config = knowledge_base.get_rag_config()
            title_weighting_enabled = self.config_manager.get_title_weighting_enabled(kb_config=kb_config)
            title_weight_multiplier = self.config_manager.get_title_weight_multiplier(kb_config=kb_config)

            logger.info(
                f"Keyword search title weighting: enabled={title_weighting_enabled}, multiplier={title_weight_multiplier}, kb_config_title_weighting={kb_config.get('title_weighting_enabled')}"
            )

            # Build title match conditions for enhanced scoring (whole word matches only)
            title_match_conditions = []
            title_params = {}

            # Filter out stop words and short terms for title matching
            meaningful_terms = [
                term
                for term in processed["keyword_terms"]
                if len(term) >= 3
                and term.lower()
                not in {
                    "the",
                    "and",
                    "for",
                    "are",
                    "but",
                    "not",
                    "you",
                    "all",
                    "can",
                    "had",
                    "her",
                    "was",
                    "one",
                    "our",
                    "out",
                    "day",
                    "get",
                    "has",
                    "him",
                    "his",
                    "how",
                    "its",
                    "may",
                    "new",
                    "now",
                    "old",
                    "see",
                    "two",
                    "who",
                    "boy",
                    "did",
                    "she",
                    "use",
                    "way",
                    "what",
                    "when",
                    "with",
                    "have",
                    "this",
                    "will",
                    "your",
                    "from",
                    "they",
                    "know",
                    "want",
                    "been",
                    "good",
                    "much",
                    "some",
                    "time",
                    "very",
                    "come",
                    "here",
                    "just",
                    "like",
                    "long",
                    "make",
                    "many",
                    "over",
                    "such",
                    "take",
                    "than",
                    "them",
                    "well",
                    "were",
                }
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
            if title_weighting_enabled:
                # First, find documents with title matches
                from sqlalchemy import text

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
                """)  # nosec # difficult to turn this into sqlalchemy query format, and injection is not possible here  # noqa: S608

                params["exact_pattern"] = f"\\m{re.escape(query)}\\M"
                title_result = await self.db.execute(title_match_query, params)
                title_matches = title_result.fetchall()

                if title_matches:
                    # Use new title-match chunk selection for title-matched documents
                    all_chunks = []
                    max_chunks_per_doc = 3

                    for title_match in title_matches:
                        doc_chunks = await self._get_title_match_chunks(
                            document_id=title_match.document_id,
                            query=query,
                            max_chunks=max_chunks_per_doc,
                            knowledge_base_id=knowledge_base_id,
                            knowledge_base=knowledge_base,
                        )

                        # Convert to the expected format and apply title boost
                        for chunk_data in doc_chunks:
                            title_boost = (float(title_match.title_score) / 10.0) * title_weight_multiplier
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
                            all_chunks.append(chunk_obj)

                    # Sort by boosted score and limit
                    all_chunks.sort(key=lambda x: x.keyword_score, reverse=True)
                    chunks = all_chunks[:limit]
                else:
                    # No title matches, fall back to regular content search
                    chunks = []
            else:
                # Title weighting disabled, use regular content search
                chunks = []

            # If no title matches or title weighting disabled, do regular content search
            if not chunks:
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
                    if len(term) >= 3 and term.lower() not in {
                        "the",
                        "and",
                        "for",
                        "are",
                        "but",
                        "not",
                        "you",
                        "all",
                        "can",
                        "had",
                        "her",
                        "was",
                        "one",
                        "our",
                        "out",
                        "day",
                        "get",
                        "has",
                        "him",
                        "his",
                        "how",
                        "its",
                        "may",
                        "new",
                        "now",
                        "old",
                        "see",
                        "two",
                        "who",
                        "boy",
                        "did",
                        "she",
                        "use",
                        "way",
                        "what",
                        "when",
                        "with",
                        "have",
                        "this",
                        "will",
                        "your",
                        "from",
                        "they",
                        "know",
                        "want",
                        "been",
                        "good",
                        "much",
                        "some",
                        "time",
                        "very",
                        "come",
                        "here",
                        "just",
                        "like",
                        "long",
                        "make",
                        "many",
                        "over",
                        "such",
                        "take",
                        "than",
                        "them",
                        "well",
                        "were",
                    }:
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

                # Build the WHERE clause safely - all parameters are pre-defined
                where_clause = " OR ".join(where_conditions)

                # Build exact pattern parameter
                params["exact_pattern"] = f"\\m{re.escape(query)}\\M"

                # Execute parameterized query (SECURE - all user input is parameterized)
                from sqlalchemy import text

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
                chunks = result.fetchall()

            if not chunks:
                # Return empty results if no matches found
                from datetime import datetime

                from ..schemas.query import QueryResponse, QueryType

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
            from datetime import datetime

            from ..schemas.query import QueryResponse, QueryResult, QueryType

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
        knowledge_base: KnowledgeBase,
    ) -> list[dict[str, Any]]:
        """For title-matched documents, find the most relevant chunks within that document.
        Uses the original query to find semantically and keyword relevant chunks.
        Always returns the top N chunks regardless of absolute scores (trusting title match).
        """
        try:
            # Get all chunks from this specific document (excluding title chunks)
            from sqlalchemy import text

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

            # Get query embedding for similarity scoring using the knowledge base's embedding model
            from scipy.spatial.distance import cosine

            from ..services.rag_processing_service import RAGProcessingService

            rag_service = RAGProcessingService.get_instance(embedding_model=str(knowledge_base.embedding_model))
            query_embedding = rag_service.model.encode([query])[0]

            for chunk in chunks:
                # Calculate similarity score
                chunk_embedding = chunk.embedding
                if isinstance(chunk_embedding, str):
                    import json

                    chunk_embedding = json.loads(chunk_embedding)

                similarity_score = float(1 - cosine(query_embedding, chunk_embedding))
                similarity_score = max(0, similarity_score)  # Ensure non-negative

                # Calculate keyword score using existing preprocessing
                processed = self.preprocess_query(query)
                keyword_score = self._calculate_keyword_score(chunk.content, processed["keyword_terms"])

                # Combine scores (same weights as hybrid search)
                combined_score = similarity_score * 0.7 + keyword_score * 0.3

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

    # TODO: Refactor this function. It's too complex (number of branches and statements).
    @measure_execution_time
    async def title_search(self, knowledge_base_id: str, query: str, limit: int = 10) -> dict[str, Any]:  # noqa: PLR0915
        """Perform dedicated title search with highest priority scoring.
        This method specifically searches document titles and gives them maximum weight.
        For title-matched documents, finds the most relevant chunks within each document.
        """
        try:
            # Verify knowledge base exists
            knowledge_base = await self._verify_knowledge_base(knowledge_base_id)

            # Preprocess query using unified preprocessing
            processed = self.preprocess_query(query)

            # Log the preprocessing results for debugging
            logger.info(
                f"Title search preprocessing: original='{query}' -> processed='{processed['similarity_query']}' -> terms={processed['keyword_terms']}"
            )

            # Build SQLAlchemy conditions for title-focused search (whole word matches only)

            # Filter out stop words and short terms for title matching
            meaningful_terms = [
                term
                for term in processed["keyword_terms"]
                if len(term) >= 3
                and term.lower()
                not in {
                    "the",
                    "and",
                    "for",
                    "are",
                    "but",
                    "not",
                    "you",
                    "all",
                    "can",
                    "had",
                    "her",
                    "was",
                    "one",
                    "our",
                    "out",
                    "day",
                    "get",
                    "has",
                    "him",
                    "his",
                    "how",
                    "its",
                    "may",
                    "new",
                    "now",
                    "old",
                    "see",
                    "two",
                    "who",
                    "boy",
                    "did",
                    "she",
                    "use",
                    "way",
                    "what",
                    "when",
                    "with",
                    "have",
                    "this",
                    "will",
                    "your",
                    "from",
                    "they",
                    "know",
                    "want",
                    "been",
                    "good",
                    "much",
                    "some",
                    "time",
                    "very",
                    "come",
                    "here",
                    "just",
                    "like",
                    "long",
                    "make",
                    "many",
                    "over",
                    "such",
                    "take",
                    "than",
                    "them",
                    "well",
                    "were",
                }
            ]

            # Build parameterized query for title search (SECURITY FIX)
            # Create individual parameters for each term to avoid SQL injection
            params = {"kb_id": knowledge_base_id, "limit": limit}
            where_conditions = []

            for i, term in enumerate(meaningful_terms):
                # Each term gets its own parameter for safe binding
                title_param = f"title_pattern_{i}"
                params[title_param] = f"\\m{re.escape(term)}\\M"
                where_conditions.append(f"d.title ~* :{title_param}")

            # Include literal filename matches (e.g., ModernChat.js, foo.py)
            for j, fname in enumerate(processed.get("filename_terms", [])):
                p = f"fname_like_{j}"
                params[p] = f"%{fname}%"
                where_conditions.append(f"d.title ILIKE :{p}")

            # Build the WHERE clause safely - all parameters are pre-defined
            if where_conditions:
                where_clause = " OR ".join(where_conditions)
            elif len(query.strip()) >= 3:
                # Fallback for exact query match if no meaningful terms
                params["fallback_pattern"] = f"\\m{re.escape(query.strip())}\\M"
                where_clause = "d.title ~* :fallback_pattern"
            else:
                # No valid search terms, return empty results
                from datetime import datetime

                from ..schemas.query import QueryResponse, QueryType

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

            # Build exact pattern parameter
            params["exact_pattern"] = f"\\m{re.escape(query.strip())}\\M"

            # Execute parameterized query (SECURE - all user input is parameterized)
            from sqlalchemy import text

            title_query = text(f"""
                SELECT
                    dc.id, dc.document_id, dc.knowledge_base_id, dc.chunk_index,
                    dc.content, dc.char_count, dc.word_count, dc.token_count,
                    dc.start_char, dc.end_char, dc.embedding_model, dc.embedding_created_at,
                    dc.created_at, d.title as document_title, d.source_id, d.source_url,
                    d.file_type, d.source_type, dc.chunk_metadata,
                    CASE
                        WHEN d.title ~* :exact_pattern THEN 10.0  -- Highest score for exact title matches
                        WHEN ({where_clause}) THEN 8.0  -- High score for partial title matches
                        WHEN (dc.chunk_metadata->>'chunk_type' = 'title') THEN 6.0  -- High score for title chunks
                        ELSE 1.0  -- Lower score for content matches
                    END as title_score
                FROM document_chunks dc
                JOIN documents d ON dc.document_id = d.id
                WHERE dc.knowledge_base_id = :kb_id
                AND ({where_clause})
                ORDER BY title_score DESC, d.title, dc.chunk_index
                LIMIT :limit
            """)  # nosec # difficult to turn this into sqlalchemy query format, and injection is not
            # possible here

            result = await self.db.execute(title_query, params)
            chunks = result.fetchall()

            if not chunks:
                # Return empty results if no matches found
                from datetime import datetime

                from ..schemas.query import QueryResponse, QueryType

                response = QueryResponse(
                    results=[],
                    total_results=0,
                    query=query,
                    query_type=QueryType.KEYWORD,  # Use KEYWORD type for title search
                    execution_time=0.0,
                    similarity_threshold=0.0,
                    embedding_model=str(knowledge_base.embedding_model),
                    processed_at=datetime.now(UTC),
                )
                return response.model_dump()

            # For title-matched documents, get the most relevant chunks within each document
            from datetime import datetime

            from ..schemas.query import QueryResponse, QueryResult, QueryType

            # Group results by document to get best chunks per document
            documents_found = {}
            for chunk in chunks:
                doc_id = chunk.document_id
                if doc_id not in documents_found:
                    documents_found[doc_id] = {
                        "title_score": float(chunk.title_score),
                        "document_title": chunk.document_title or "Unknown Document",
                    }
                else:
                    # Keep the highest title score for this document
                    documents_found[doc_id]["title_score"] = max(
                        documents_found[doc_id]["title_score"], float(chunk.title_score)
                    )

            # Get the best chunks for each title-matched document
            query_results = []
            max_chunks_per_doc = 3  # Get top 3 chunks per title-matched document

            for doc_id, doc_info in documents_found.items():
                # Get relevant chunks for this document
                doc_chunks = await self._get_title_match_chunks(
                    document_id=doc_id,
                    query=query,
                    max_chunks=max_chunks_per_doc,
                    knowledge_base_id=knowledge_base_id,
                    knowledge_base=knowledge_base,
                )

                # Convert to QueryResult objects with title boost
                for chunk_data in doc_chunks:
                    # Boost the score based on title match quality
                    title_boost = (doc_info["title_score"] / 10.0) * 0.3  # Normalize and apply boost
                    boosted_score = chunk_data["similarity_score"] + title_boost

                    query_result = QueryResult(
                        chunk_id=chunk_data["chunk_id"],
                        document_id=chunk_data["document_id"],
                        document_title=chunk_data["document_title"],
                        content=chunk_data["content"],
                        similarity_score=min(1.0, boosted_score),  # Cap at 1.0
                        chunk_index=chunk_data["chunk_index"],
                        start_char=chunk_data["start_char"],
                        end_char=chunk_data["end_char"],
                        file_type=chunk_data["file_type"],
                        source_url=chunk_data["source_url"],
                        source_id=chunk_data["source_id"],
                        created_at=chunk_data["created_at"],
                    )
                    query_results.append(query_result)

            # Sort by boosted score and limit results
            query_results.sort(key=lambda x: x.similarity_score, reverse=True)
            query_results = query_results[:limit]

            response = QueryResponse(
                results=query_results,
                total_results=len(query_results),
                query=query,
                query_type=QueryType.KEYWORD,  # Use KEYWORD type for title search
                execution_time=0.0,  # Will be set by decorator
                similarity_threshold=0.0,
                embedding_model=str(knowledge_base.embedding_model),
                processed_at=datetime.now(UTC),
            )
            return response.model_dump()
        except Exception as e:
            logger.error(f"Failed to perform title search: {e}", exc_info=True)
            raise ShuException(f"Failed to perform title search: {e!s}", "TITLE_SEARCH_ERROR")

    @measure_execution_time
    async def hybrid_search(
        self, knowledge_base_id: str, query: str, limit: int = 10, threshold: float = 0.0
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

        Returns:
            Dictionary with search results

        """
        try:
            # Verify knowledge base exists
            knowledge_base = await self._verify_knowledge_base(knowledge_base_id)

            # Log hybrid search request
            logger.info(
                f"Hybrid search: query='{query[:100]}...' kb_id={knowledge_base_id} limit={limit} threshold={threshold}"
            )

            # Get similarity search results
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
            similarity_response = await self.similarity_search(knowledge_base_id, similarity_request)

            # Get keyword search results (this handles stop word filtering correctly)
            keyword_response = await self.keyword_search(knowledge_base_id, query, limit)

            # Log keyword search results for debugging
            logger.info(
                f"Hybrid search keyword results: total={keyword_response.get('total_results', 0)}, top_docs={[r.get('document_title', 'unknown')[:50] for r in keyword_response.get('results', [])[:3]]}"
            )

            # If keyword search returned empty results due to stop words, return only similarity results
            if keyword_response["total_results"] == 0:
                logger.info(
                    f"Keyword search returned no results for query '{query}', returning only similarity results"
                )
                # Create a new response with hybrid query type but similarity results
                from datetime import datetime

                from ..schemas.query import QueryResponse, QueryType

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

            # Add similarity results
            for chunk in similarity_response["results"]:
                # Handle both chunk_id and id fields for compatibility
                chunk_id = chunk.get("chunk_id") or chunk.get("id")
                combined_results[chunk_id] = {
                    "chunk": chunk,
                    "similarity_score": float(chunk.get("similarity_score", 0.0)),
                    "keyword_score": 0.0,
                    "combined_score": float(chunk.get("similarity_score", 0.0))
                    * self.config_manager.get_hybrid_similarity_weight(),
                }

            # Add keyword results
            for chunk in keyword_response["results"]:
                # Handle both chunk_id and id fields for compatibility
                chunk_id = chunk.get("chunk_id") or chunk.get("id")
                if chunk_id in combined_results:
                    # Update existing result with keyword score
                    keyword_score = float(
                        chunk.get("similarity_score", 0.8)
                    )  # Use similarity_score from keyword search
                    combined_results[chunk_id]["keyword_score"] = keyword_score
                    combined_results[chunk_id]["combined_score"] = (
                        combined_results[chunk_id]["similarity_score"]
                        * self.config_manager.get_hybrid_similarity_weight()
                        + keyword_score * self.config_manager.get_hybrid_keyword_weight()
                    )
                else:
                    # Create new result from keyword search
                    keyword_score = float(
                        chunk.get("similarity_score", 0.8)
                    )  # Use similarity_score from keyword search
                    combined_results[chunk_id] = {
                        "chunk": chunk,
                        "similarity_score": 0.0,
                        "keyword_score": keyword_score,
                        "combined_score": keyword_score * self.config_manager.get_hybrid_keyword_weight(),
                    }

            # Get RAG configuration for chunk limits
            rag_config = await self._get_rag_config(knowledge_base_id)
            max_chunks_per_doc = rag_config.get("max_chunks_per_document", 2)

            # Sort by combined score and apply document de-duplication
            # Group by document and keep top chunks per document
            document_results = {}
            for result in combined_results.values():
                chunk = result["chunk"]
                # Handle both object and dictionary chunk formats
                doc_id = chunk.document_id if hasattr(chunk, "document_id") else chunk["document_id"]
                if doc_id not in document_results:
                    document_results[doc_id] = []
                document_results[doc_id].append(result)

            # Sort chunks within each document by combined score
            for _, doc_results in document_results.items():
                doc_results.sort(key=lambda x: x["combined_score"], reverse=True)

            # Flatten results, maintaining order by combined score
            sorted_results = []
            # Document deduplication: Limit chunks per document based on configuration
            # This prevents the same document from appearing multiple times while allowing multiple relevant chunks
            for _, doc_results in document_results.items():
                # Add up to max_chunks_per_doc results for this document
                results_to_add = doc_results[:max_chunks_per_doc]
                sorted_results.extend(results_to_add)

            # Apply limit
            sorted_results = sorted_results[:limit]

            # Convert to QueryResponse format
            from datetime import datetime

            from ..schemas.query import QueryResponse, QueryResult, QueryType

            query_results = []
            for result in sorted_results:
                chunk = result["chunk"]

                # Handle both object and dictionary chunk formats
                def get_attr(obj, attr):
                    if hasattr(obj, attr):
                        return getattr(obj, attr)
                    if attr == "id" and "chunk_id" in obj:
                        return obj["chunk_id"]  # Handle chunk_id vs id mapping
                    return obj[attr]

                query_result = QueryResult(
                    chunk_id=get_attr(chunk, "id"),
                    document_id=get_attr(chunk, "document_id"),
                    document_title=get_attr(chunk, "document_title") or "Unknown Document",
                    content=get_attr(chunk, "content"),
                    similarity_score=result["combined_score"],
                    chunk_index=get_attr(chunk, "chunk_index"),
                    start_char=get_attr(chunk, "start_char"),
                    end_char=get_attr(chunk, "end_char"),
                    file_type=get_attr(chunk, "file_type") or "txt",
                    source_url=get_attr(chunk, "source_url"),
                    source_id=get_attr(chunk, "source_id"),
                    created_at=get_attr(chunk, "created_at"),
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
        except Exception as e:
            logger.error(f"Failed to perform hybrid search: {e}", exc_info=True)
            raise ShuException(f"Failed to perform hybrid search: {e!s}", "HYBRID_SEARCH_ERROR")
