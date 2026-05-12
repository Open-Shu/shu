"""Base utilities and shared functionality for query service.

This module contains the base class, decorators, and shared methods used across
all query types (similarity, keyword, hybrid, multi-surface).
"""

import functools
import re
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, select

from shu.core.logging import get_logger

from ...core.config import ConfigurationManager
from ...models.document import Document
from ...models.knowledge_base import KnowledgeBase
from ...utils.text import fold_unicode_to_ascii
from .constants import COMPREHENSIVE_STOP_WORDS

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


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


class QueryServiceBase:
    """Base class with shared utilities for query operations."""

    def __init__(self, db: "AsyncSession", config_manager: ConfigurationManager) -> None:
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
        all_terms = re.findall(r"\b[\w'\-.,]+\b", query.lower())

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
            ):  # All caps (like "ACME") - check lowercase
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
        # Fold Unicode lookalikes (non-breaking hyphens, curly quotes, etc.) to ASCII
        # before tokenization so query terms match the folded form stored in keywords.
        query = fold_unicode_to_ascii(query)
        # Extract all potential terms with original case
        # Split on word boundaries and clean up punctuation
        # Handle technical identifiers like "ACME-555,222" where comma is part of number notation
        # Pattern explanation:
        # - [A-Za-z0-9]+ : Start with alphanumeric
        # - (?:[-][A-Za-z0-9]+)* : Allow hyphens between alphanumeric parts
        # - (?:[,][0-9]+)* : Allow commas followed by numbers (for number notation like "555,222")
        # - (?:[.][0-9]+)? : Allow decimal points
        raw_terms = re.findall(r"[A-Za-z0-9']+(?:[-][A-Za-z0-9]+)*(?:[,][0-9]+)*(?:[.][0-9]+)?", query)
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
            raw_filename_terms = re.findall(r"([A-Za-z0-9_][A-Za-z0-9_\-\.]*\.(?:md|py|js))", query)
            # Deduplicate while preserving order (case-insensitive)
            seen: set[str] = set()
            filename_terms = []
            for t in raw_filename_terms:
                key = t.lower()
                if key not in seen:
                    seen.add(key)
                    filename_terms.append(t)
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
        from ...utils import KnowledgeBaseVerifier

        return await KnowledgeBaseVerifier.verify_exists(self.db, knowledge_base_id)

    async def _get_rag_config(self, knowledge_base_id: str) -> dict[str, Any]:
        """Get RAG configuration for a knowledge base.

        Args:
            knowledge_base_id: Knowledge base ID

        Returns:
            Dictionary with RAG configuration settings

        """
        try:
            from ..knowledge_base_service import KnowledgeBaseService

            kb_service = KnowledgeBaseService(self.db, self.config_manager)
            rag_config_response = await kb_service.get_rag_config(knowledge_base_id)
            return rag_config_response.model_dump()
        except Exception as e:
            logger.warning(f"Failed to get RAG config for KB {knowledge_base_id}: {e}")
            # Return default configuration using ConfigurationManager
            default_config = self.config_manager.get_rag_config_dict()
            default_config["version"] = "1.0"  # Add version for compatibility
            return default_config

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
        except Exception as e:
            logger.warning(f"Full-doc escalation failed: {e}")
            return {"enabled": False, "error": str(e)}
