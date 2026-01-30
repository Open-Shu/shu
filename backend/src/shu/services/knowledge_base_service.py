"""Knowledge Base Service for Shu RAG Backend.

This module provides business logic for managing knowledge bases,
including CRUD operations, statistics, and configuration management.
"""

from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from ..core.exceptions import (
    KnowledgeBaseAlreadyExistsError,
    KnowledgeBaseNotFoundError,
    ShuException,
    ValidationError,
)
from ..core.logging import get_logger
from ..models.document import Document, DocumentChunk
from ..models.knowledge_base import KnowledgeBase
from ..schemas.knowledge_base import RAGConfig, RAGConfigResponse

logger = get_logger(__name__)


class KnowledgeBaseService:
    """Service for managing knowledge bases."""

    def __init__(self, db: AsyncSession, config_manager=None):
        self.db = db
        # Use dependency injection for ConfigurationManager
        if config_manager is None:
            from ..core.config import get_config_manager

            config_manager = get_config_manager()
        self._config_manager = config_manager

    @property
    def DEFAULT_RAG_CONFIG(self) -> dict[str, Any]:
        """Get default RAG configuration from ConfigurationManager."""
        return self._config_manager.get_rag_config_dict()

    # Default templates for different use cases
    DEFAULT_TEMPLATES = {
        "academic": {
            "include_references": True,
            "reference_format": "markdown",
            "context_format": "detailed",
            "prompt_template": "academic",
        },
        "business": {
            "include_references": True,
            "reference_format": "markdown",
            "context_format": "detailed",
            "prompt_template": "business",
        },
        "technical": {
            "include_references": True,
            "reference_format": "markdown",
            "context_format": "detailed",
            "prompt_template": "technical",
        },
        "custom": {
            "include_references": True,
            "reference_format": "markdown",
            "context_format": "detailed",
            "prompt_template": "custom",
        },
    }

    async def get_default_templates(self) -> dict[str, dict[str, Any]]:
        """Get default templates for different use cases.

        Returns:
            Dictionary of default templates

        """
        return self.DEFAULT_TEMPLATES.copy()

    async def get_rag_config(self, kb_id: str) -> RAGConfigResponse:
        """Get RAG configuration for a knowledge base.

        Args:
            kb_id: Knowledge base ID

        Returns:
            RAGConfigResponse with configuration settings

        """
        try:
            knowledge_base = await self.get_knowledge_base(kb_id)
            if not knowledge_base:
                raise KnowledgeBaseNotFoundError(kb_id)

            # Get RAG configuration from the knowledge base model
            rag_config = knowledge_base.get_rag_config()
            logger.debug(f"Retrieved RAG config for KB {kb_id}")

            return RAGConfigResponse(
                include_references=rag_config["include_references"],
                reference_format=rag_config["reference_format"],
                context_format=rag_config["context_format"],
                prompt_template=rag_config["prompt_template"],
                search_threshold=rag_config["search_threshold"],
                max_results=rag_config["max_results"],
                max_chunks_per_document=rag_config["max_chunks_per_document"],
                chunk_overlap_ratio=rag_config["chunk_overlap_ratio"],
                search_type=rag_config["search_type"],
                title_weighting_enabled=rag_config["title_weighting_enabled"],
                title_weight_multiplier=rag_config["title_weight_multiplier"],
                title_chunk_enabled=rag_config["title_chunk_enabled"],
                minimum_query_words=rag_config["minimum_query_words"],
                fetch_full_documents=rag_config["fetch_full_documents"],
                full_doc_max_docs=rag_config["full_doc_max_docs"],
                full_doc_token_cap=rag_config["full_doc_token_cap"],
                version=rag_config["version"],
            )

        except Exception as e:
            logger.error(f"Failed to get RAG config for knowledge base '{kb_id}': {e}", exc_info=True)
            raise ShuException(f"Failed to get RAG configuration: {e!s}", "RAG_CONFIG_GET_ERROR")

    async def update_rag_config(self, kb_id: str, rag_config: RAGConfig) -> RAGConfigResponse:
        """Update RAG configuration for a knowledge base.

        Args:
            kb_id: Knowledge base ID
            rag_config: New RAG configuration

        Returns:
            Updated RAGConfigResponse

        """
        try:
            knowledge_base = await self.get_knowledge_base(kb_id)
            if not knowledge_base:
                raise KnowledgeBaseNotFoundError(kb_id)

            # Update RAG configuration in the knowledge base model
            config_dict = {
                "include_references": rag_config.include_references,
                "reference_format": rag_config.reference_format,
                "context_format": rag_config.context_format,
                "prompt_template": rag_config.prompt_template,
                "search_threshold": rag_config.search_threshold,
                "max_results": rag_config.max_results,
                "chunk_overlap_ratio": rag_config.chunk_overlap_ratio,
                "search_type": rag_config.search_type,
                "title_weighting_enabled": rag_config.title_weighting_enabled,
                "title_weight_multiplier": rag_config.title_weight_multiplier,
                "title_chunk_enabled": rag_config.title_chunk_enabled,
                # Full Document Escalation
                "fetch_full_documents": rag_config.fetch_full_documents,
                "full_doc_max_docs": rag_config.full_doc_max_docs,
                "full_doc_token_cap": rag_config.full_doc_token_cap,
                "version": "1.0",
            }

            knowledge_base.update_rag_config(config_dict)

            # Commit the changes to the database
            await self.db.commit()
            await self.db.refresh(knowledge_base)

            # Clear any cached RAG configuration for this knowledge base
            from ..core.cache import get_config_cache

            cache = get_config_cache()
            if hasattr(cache, "_rag_configs") and kb_id in cache._rag_configs:
                del cache._rag_configs[kb_id]
                logger.debug(f"Cleared cached RAG config for KB {kb_id}")

            logger.info(f"Successfully updated RAG config for KB {kb_id}")

            # Get the actual saved configuration from the database (with ConfigurationManager defaults)
            # This ensures we return the real saved values, not just the input values
            saved_config = knowledge_base.get_rag_config()

            return RAGConfigResponse(
                include_references=saved_config["include_references"],
                reference_format=saved_config["reference_format"],
                context_format=saved_config["context_format"],
                prompt_template=saved_config["prompt_template"],
                search_threshold=saved_config["search_threshold"],
                max_results=saved_config["max_results"],
                max_chunks_per_document=saved_config["max_chunks_per_document"],
                chunk_overlap_ratio=saved_config["chunk_overlap_ratio"],
                search_type=saved_config["search_type"],
                title_weighting_enabled=saved_config["title_weighting_enabled"],
                title_weight_multiplier=saved_config["title_weight_multiplier"],
                title_chunk_enabled=saved_config["title_chunk_enabled"],
                minimum_query_words=saved_config["minimum_query_words"],
                fetch_full_documents=saved_config["fetch_full_documents"],
                full_doc_max_docs=saved_config["full_doc_max_docs"],
                full_doc_token_cap=saved_config["full_doc_token_cap"],
                version=saved_config["version"],
            )

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to update RAG config for knowledge base '{kb_id}': {e}", exc_info=True)
            raise ShuException(f"Failed to update RAG configuration: {e!s}", "RAG_CONFIG_UPDATE_ERROR")

    async def get_knowledge_base_stats(self, kb_id: str) -> dict[str, Any]:
        """Get statistics for a specific knowledge base.

        Args:
            kb_id: Knowledge base ID

        Returns:
            Dictionary with document and chunk counts

        """
        try:
            # Get document count
            doc_count_result = await self.db.execute(
                select(func.count(Document.id)).where(Document.knowledge_base_id == kb_id)
            )
            document_count = doc_count_result.scalar() or 0

            # Get total chunk count
            chunk_count_result = await self.db.execute(
                select(func.count(DocumentChunk.id))
                .join(Document, DocumentChunk.document_id == Document.id)
                .where(Document.knowledge_base_id == kb_id)
            )
            total_chunks = chunk_count_result.scalar() or 0

            return {"document_count": document_count, "total_chunks": total_chunks}

        except Exception as e:
            logger.error(f"Failed to get stats for knowledge base '{kb_id}': {e}", exc_info=True)
            return {"document_count": 0, "total_chunks": 0}

    async def list_knowledge_bases(
        self, limit: int = 50, offset: int = 0, search: str | None = None
    ) -> tuple[list[KnowledgeBase], int]:
        """List knowledge bases with optional filtering and pagination.

        Args:
            limit: Maximum number of knowledge bases to return
            offset: Number of knowledge bases to skip
            search: Optional search term for filtering by name

        Returns:
            Tuple of (knowledge_bases, total_count)

        """
        try:
            query = select(KnowledgeBase)

            if search:
                query = query.where(KnowledgeBase.name.ilike(f"%{search}%"))

            # Get total count
            count_result = await self.db.execute(select(func.count()).select_from(query.subquery()))
            total_count = count_result.scalar() or 0

            # Apply pagination and get knowledge bases
            query = query.offset(offset).limit(limit)
            result = await self.db.execute(query.order_by(KnowledgeBase.status, KnowledgeBase.name))
            knowledge_bases = list(result.scalars().all())

            return knowledge_bases, int(total_count)

        except Exception as e:
            logger.error(f"Failed to list knowledge bases: {e}", exc_info=True)
            raise ShuException(f"Failed to list knowledge bases: {e!s}", "KNOWLEDGE_BASE_LIST_ERROR")

    async def get_knowledge_base(self, kb_id: str) -> KnowledgeBase | None:
        """Get a knowledge base by ID.

        Args:
            kb_id: Knowledge base ID

        Returns:
            KnowledgeBase or None if not found

        """
        try:
            from ..utils import KnowledgeBaseVerifier

            return await KnowledgeBaseVerifier.get_optional(self.db, kb_id)

        except Exception as e:
            logger.error(f"Failed to get knowledge base '{kb_id}': {e}", exc_info=True)
            raise ShuException(f"Failed to get knowledge base: {e!s}", "KNOWLEDGE_BASE_GET_ERROR")

    async def create_knowledge_base(self, kb_data, owner_id: str | None = None) -> KnowledgeBase:
        """Create a new knowledge base.

        Args:
            kb_data: Knowledge base data (KnowledgeBaseCreate Pydantic model)
            owner_id: ID of the user who will own this knowledge base

        Returns:
            Created KnowledgeBase

        """
        try:
            # Check if knowledge base with same name already exists
            existing_result = await self.db.execute(select(KnowledgeBase).where(KnowledgeBase.name == kb_data.name))
            if existing_result.scalar_one_or_none():
                raise KnowledgeBaseAlreadyExistsError(kb_data.name)

            # Create new knowledge base from Pydantic model
            kb_dict = kb_data.model_dump()
            if owner_id:
                kb_dict["owner_id"] = owner_id
            knowledge_base = KnowledgeBase(**kb_dict)
            self.db.add(knowledge_base)
            await self.db.commit()
            await self.db.refresh(knowledge_base)

            logger.info(f"Created knowledge base: {knowledge_base.name}")
            return knowledge_base

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to create knowledge base: {e}", exc_info=True)
            raise ShuException(f"Failed to create knowledge base: {e!s}", "KNOWLEDGE_BASE_CREATE_ERROR")

    async def update_knowledge_base(self, kb_id: str, update_data) -> KnowledgeBase:
        """Update an existing knowledge base.

        Args:
            kb_id: Knowledge base ID
            update_data: Update data (KnowledgeBaseUpdate Pydantic model)

        Returns:
            Updated KnowledgeBase

        """
        try:
            knowledge_base = await self.get_knowledge_base(kb_id)
            if not knowledge_base:
                raise KnowledgeBaseNotFoundError(kb_id)

            # Update fields from Pydantic model
            update_dict = update_data.model_dump(exclude_unset=True)
            for field, value in update_dict.items():
                if hasattr(knowledge_base, field):
                    setattr(knowledge_base, field, value)

            await self.db.commit()
            await self.db.refresh(knowledge_base)

            logger.info(f"Updated knowledge base: {knowledge_base.name}")
            return knowledge_base

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to update knowledge base '{kb_id}': {e}", exc_info=True)
            raise ShuException(f"Failed to update knowledge base: {e!s}", "KNOWLEDGE_BASE_UPDATE_ERROR")

    async def delete_knowledge_base(self, kb_id: str) -> None:
        """Delete a knowledge base.

        Args:
            kb_id: Knowledge base ID

        """
        try:
            knowledge_base = await self.get_knowledge_base(kb_id)
            if not knowledge_base:
                raise KnowledgeBaseNotFoundError(kb_id)

            # Check if knowledge base has documents (this would need to be implemented based on your document model)
            # For now, we'll just delete the knowledge base
            await self.db.delete(knowledge_base)
            await self.db.commit()

            logger.info(f"Deleted knowledge base: {knowledge_base.name}")

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to delete knowledge base '{kb_id}': {e}", exc_info=True)
            raise ShuException(f"Failed to delete knowledge base: {e!s}", "KNOWLEDGE_BASE_DELETE_ERROR")

    async def get_overall_knowledge_base_stats(self) -> dict[str, Any]:
        """Get overall statistics for all knowledge bases.

        Returns:
            Dictionary with overall statistics

        """
        try:
            # Get total knowledge bases
            total_result = await self.db.execute(select(func.count(KnowledgeBase.id)))
            total_kbs = total_result.scalar() or 0

            # Get active knowledge bases
            active_result = await self.db.execute(
                select(func.count(KnowledgeBase.id)).where(KnowledgeBase.status == "active")
            )
            active_kbs = active_result.scalar() or 0

            # Get sync enabled count
            sync_enabled_result = await self.db.execute(
                select(func.count(KnowledgeBase.id)).where(KnowledgeBase.sync_enabled == True)
            )
            sync_enabled_count = sync_enabled_result.scalar() or 0

            # Mock other statistics for now
            stats = {
                "total_knowledge_bases": total_kbs,
                "active_knowledge_bases": active_kbs,
                "total_documents": 0,  # Would need to count from documents table
                "total_chunks": 0,  # Would need to count from chunks table
                "sync_enabled_count": sync_enabled_count,
                "source_type_breakdown": {},  # Would need to analyze documents
                "status_breakdown": {"active": active_kbs, "inactive": total_kbs - active_kbs},
            }

            return stats

        except Exception as e:
            logger.error(f"Failed to get knowledge base statistics: {e}", exc_info=True)
            raise ShuException(f"Failed to get knowledge base statistics: {e!s}", "KNOWLEDGE_BASE_STATS_ERROR")

    async def enable_sync(self, kb_id: str) -> KnowledgeBase:
        """Enable sync for a knowledge base.

        Args:
            kb_id: Knowledge base ID

        Returns:
            Updated KnowledgeBase

        """
        try:
            knowledge_base = await self.get_knowledge_base(kb_id)
            if not knowledge_base:
                raise KnowledgeBaseNotFoundError(kb_id)

            knowledge_base.sync_enabled = True  # type: ignore[attr-defined]
            await self.db.commit()
            await self.db.refresh(knowledge_base)

            logger.info(f"Enabled sync for knowledge base: {knowledge_base.name}")
            return knowledge_base

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to enable sync for knowledge base '{kb_id}': {e}", exc_info=True)
            raise ShuException(f"Failed to enable sync: {e!s}", "KNOWLEDGE_BASE_SYNC_ENABLE_ERROR")

    async def disable_sync(self, kb_id: str) -> KnowledgeBase:
        """Disable sync for a knowledge base.

        Args:
            kb_id: Knowledge base ID

        Returns:
            Updated KnowledgeBase

        """
        try:
            knowledge_base = await self.get_knowledge_base(kb_id)
            if not knowledge_base:
                raise KnowledgeBaseNotFoundError(kb_id)

            knowledge_base.sync_enabled = False  # type: ignore[attr-defined]
            await self.db.commit()
            await self.db.refresh(knowledge_base)

            logger.info(f"Disabled sync for knowledge base: {knowledge_base.name}")
            return knowledge_base

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to disable sync for knowledge base '{kb_id}': {e}", exc_info=True)
            raise ShuException(f"Failed to disable sync: {e!s}", "KNOWLEDGE_BASE_SYNC_DISABLE_ERROR")

    async def get_documents(
        self,
        kb_id: str,
        limit: int = 50,
        offset: int = 0,
        search_query: str = None,
        filter_by: str = "all",
    ) -> tuple[list[Document], int]:
        """Get documents for a knowledge base with pagination.

        Args:
            kb_id: Knowledge base ID
            limit: Maximum number of documents to return
            offset: Number of documents to skip

        Returns:
            Tuple of (documents, total_count)

        """
        try:
            document_filter_condition = self.get_document_filter_condition(kb_id, search_query, filter_by)

            # Get total count
            count_query = select(func.count(Document.id)).where(document_filter_condition)
            total_result = await self.db.execute(count_query)
            total = total_result.scalar() or 0

            # Get documents with pagination (exclude content field)
            query = (
                select(Document)
                .options(defer(Document.content))
                .where(document_filter_condition)
                .order_by(Document.created_at.desc())
                .offset(offset)
                .limit(limit)
            )

            result = await self.db.execute(query)
            documents = list(result.scalars().all())

            logger.debug(f"Retrieved {len(documents)} documents for KB {kb_id}")
            return documents, int(total)

        except Exception as e:
            logger.error(f"Failed to get documents for knowledge base {kb_id}: {e}")
            raise ShuException(f"Failed to get documents: {e!s}", "DOCUMENT_LIST_ERROR")

    def get_document_filter_condition(self, kb_id, search_query, filter_by):
        conditions = [Document.knowledge_base_id == kb_id]

        # Apply the search on the document title and content (case-insensitive)
        if search_query:
            conditions.append(
                or_(
                    Document.title.ilike(f"%{search_query}%"),
                    Document.content.ilike(f"%{search_query}%"),
                )
            )

        # Apply filter_by options
        if filter_by and filter_by != "all":
            if filter_by == "ocr":
                conditions.append(Document.extraction_method == "ocr")
            elif filter_by == "text":
                conditions.append(Document.extraction_method == "text")
            elif filter_by == "high-confidence":
                # Extraction confidence stored as 0..1
                conditions.append(Document.extraction_confidence >= 0.8)
            elif filter_by == "low-confidence":
                conditions.append(Document.extraction_confidence < 0.6)
            else:
                # Unknown filter: no-op (fallback to 'all')
                pass

        return and_(*conditions)

    async def get_document(self, kb_id: str, document_id: str) -> Document | None:
        """Get a specific document from a knowledge base.

        Args:
            kb_id: Knowledge base ID
            document_id: Document ID

        Returns:
            Document instance or None if not found

        """
        try:
            query = select(Document).where(Document.knowledge_base_id == kb_id, Document.id == document_id)

            result = await self.db.execute(query)
            document = result.scalar_one_or_none()

            if document:
                logger.debug(f"Retrieved document {document_id} from KB {kb_id}")
            else:
                logger.debug(f"Document {document_id} not found in KB {kb_id}")

            return document

        except Exception as e:
            logger.error(f"Failed to get document {document_id} from KB {kb_id}: {e}")
            raise ShuException(f"Failed to get document: {e!s}")

    async def get_knowledge_base_summary(self, kb_id: str):
        """Build a high-level summary for a knowledge base including distinct source types
        and aggregate document/chunk counts.
        """
        try:
            kb = await self.get_knowledge_base(kb_id)
            if not kb:
                raise KnowledgeBaseNotFoundError(kb_id)

            # Aggregate stats
            stats = await self.get_knowledge_base_stats(kb_id)

            # Distinct source types for this KB
            result = await self.db.execute(
                select(Document.source_type).where(Document.knowledge_base_id == kb_id).distinct()
            )
            source_types = [row[0] for row in result.fetchall() if row[0] is not None]

            # Return a dict that matches KnowledgeBaseSummary fields
            return {
                "id": kb.id,
                "name": kb.name,
                "description": kb.description,
                "source_types": source_types,
                "status": kb.status,
                "document_count": stats["document_count"],
                "total_chunks": stats["total_chunks"],
                "last_sync_at": kb.last_sync_at,
            }
        except Exception as e:
            logger.error(f"Failed to build knowledge base summary for '{kb_id}': {e}", exc_info=True)
            raise ShuException(f"Failed to get knowledge base summary: {e!s}", "KNOWLEDGE_BASE_SUMMARY_ERROR")

    async def get_knowledge_base_source_types(self, kb_id: str) -> list[str]:
        """Return distinct source types present in this knowledge base's documents."""
        try:
            # Ensure KB exists
            kb = await self.get_knowledge_base(kb_id)
            if not kb:
                raise KnowledgeBaseNotFoundError(kb_id)

            result = await self.db.execute(
                select(Document.source_type).where(Document.knowledge_base_id == kb_id).distinct()
            )
            return [row[0] for row in result.fetchall() if row[0] is not None]
        except Exception as e:
            logger.error(f"Failed to get source types for KB '{kb_id}': {e}", exc_info=True)
            raise ShuException(f"Failed to get source types: {e!s}", "KNOWLEDGE_BASE_SOURCE_TYPES_ERROR")

    async def validate_knowledge_base_config(self, kb_id: str) -> dict[str, Any]:
        """Validate the knowledge base configuration and return errors/warnings.
        This performs lightweight schema/range checks; it does not perform I/O validation.
        """
        try:
            kb = await self.get_knowledge_base(kb_id)
            if not kb:
                raise KnowledgeBaseNotFoundError(kb_id)

            cfg = kb.get_rag_config()
            errors: list[str] = []
            warnings: list[str] = []

            # Range validations
            thr = cfg.get("search_threshold")
            if thr is None or not (0 <= float(thr) <= 1):
                errors.append("search_threshold must be between 0 and 1")

            max_results = cfg.get("max_results")
            if max_results is None or int(max_results) <= 0:
                errors.append("max_results must be a positive integer")

            overlap = cfg.get("chunk_overlap_ratio")
            if overlap is None or not (0 <= float(overlap) < 1):
                errors.append("chunk_overlap_ratio must be between 0 (inclusive) and 1 (exclusive)")

            twm = cfg.get("title_weight_multiplier")
            if twm is None or float(twm) <= 0:
                errors.append("title_weight_multiplier must be > 0")

            # Template warning (non-fatal)
            tmpl = cfg.get("prompt_template")
            try:
                defaults = await self.get_default_templates()
                if tmpl not in defaults:
                    warnings.append(f"Unknown prompt_template '{tmpl}' - using as custom")
            except Exception:
                # If defaults lookup fails, do not hard-fail validation
                warnings.append("Could not verify prompt_template against defaults")

            is_valid = len(errors) == 0
            return {"is_valid": is_valid, "errors": errors, "warnings": warnings}
        except ShuException:
            raise
        except Exception as e:
            logger.error(f"Failed to validate KB config for '{kb_id}': {e}", exc_info=True)
            raise ShuException(f"Failed to validate configuration: {e!s}", "KNOWLEDGE_BASE_VALIDATE_ERROR")

    async def set_knowledge_base_status(self, kb_id: str, new_status: dict[str, Any]) -> KnowledgeBase:
        """Update the status field on a knowledge base with basic validation."""
        try:
            kb = await self.get_knowledge_base(kb_id)
            if not kb:
                raise KnowledgeBaseNotFoundError(kb_id)

            allowed = {"active", "inactive", "error"}
            # Allow either a dict with 'status' key or a raw string
            status_value = new_status.get("status") if isinstance(new_status, dict) else new_status
            if status_value not in allowed:
                raise ValidationError(f"Invalid status '{status_value}'. Allowed: {sorted(allowed)}")

            kb.status = status_value
            await self.db.commit()
            await self.db.refresh(kb)
            return kb
        except ShuException:
            await self.db.rollback()
            raise
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to set status for KB '{kb_id}': {e}", exc_info=True)
            raise ShuException(f"Failed to set knowledge base status: {e!s}", "KNOWLEDGE_BASE_SET_STATUS_ERROR")
