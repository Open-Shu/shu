"""Knowledge Base model for Shu RAG Backend.

This module defines the KnowledgeBase model which stores configuration
and metadata for each knowledge base in the system.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Column, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import relationship

from .base import BaseModel


class KnowledgeBase(BaseModel):
    """Knowledge Base configuration and metadata.

    Each knowledge base represents a separate collection of documents
    with its own configuration, sync settings, and processing parameters.
    """

    __tablename__ = "knowledge_bases"

    # Basic information
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)

    # Sync configuration
    sync_enabled = Column(Boolean, default=True, nullable=False)
    last_sync_at = Column(TIMESTAMP(timezone=True), nullable=True)

    # Processing configuration
    embedding_model = Column(String(100), default="sentence-transformers/all-MiniLM-L6-v2", nullable=False)
    chunk_size = Column(Integer, default=1000, nullable=False)
    chunk_overlap = Column(Integer, default=200, nullable=False)

    # RAG Configuration - New persistent storage for RAG settings
    rag_include_references = Column(Boolean, default=True, nullable=False)
    rag_reference_format = Column(String(20), default="markdown", nullable=False)  # 'markdown' or 'text'
    rag_context_format = Column(String(20), default="detailed", nullable=False)  # 'detailed' or 'simple'
    rag_prompt_template = Column(
        String(20), default="custom", nullable=False
    )  # 'academic', 'business', 'technical', 'custom'
    rag_search_threshold = Column(JSON, default=0.7, nullable=False)  # Float stored as JSON for precision
    rag_max_results = Column(Integer, default=10, nullable=False)
    rag_chunk_overlap_ratio = Column(JSON, default=0.2, nullable=False)  # Float stored as JSON for precision
    rag_search_type = Column(String(20), default="hybrid", nullable=False)  # 'similarity', 'keyword', 'hybrid'
    rag_config_version = Column(String(10), default="1.0", nullable=False)

    # Title Search Configuration
    rag_title_weighting_enabled = Column(Boolean, default=True, nullable=False)
    rag_title_weight_multiplier = Column(JSON, default=3.0, nullable=False)  # Float stored as JSON for precision
    rag_title_chunk_enabled = Column(Boolean, default=True, nullable=False)

    # Document Chunk Configuration
    rag_max_chunks_per_document = Column(Integer, default=4, nullable=False)

    # Full Document Escalation Configuration
    # Make these nullable so centralized defaults can apply when unset
    rag_fetch_full_documents = Column(Boolean, nullable=True)
    rag_full_doc_max_docs = Column(Integer, nullable=True)
    rag_full_doc_token_cap = Column(Integer, nullable=True)

    # Query Processing Configuration
    rag_minimum_query_words = Column(Integer, default=3, nullable=False)

    # Status and metadata
    status = Column(String(50), default="active", nullable=False)  # 'active', 'inactive', 'error'
    document_count = Column(Integer, default=0, nullable=False)
    total_chunks = Column(Integer, default=0, nullable=False)

    # RBAC - Knowledge Base ownership
    owner_id = Column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )  # NULL for system/shared KBs

    # Relationships
    documents = relationship("Document", back_populates="knowledge_base", cascade="all, delete-orphan")
    # DEPRECATED: prompts relationship removed - using generalized prompt system instead
    model_configurations = relationship(
        "ModelConfiguration",
        secondary="model_configuration_knowledge_bases",
        back_populates="knowledge_bases",
    )

    # RBAC relationships
    owner = relationship("User", foreign_keys=[owner_id], back_populates="owned_knowledge_bases")
    permissions = relationship("KnowledgeBasePermission", back_populates="knowledge_base", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<KnowledgeBase(id={self.id}, name='{self.name}')>"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary with computed fields."""
        base_dict = super().to_dict()
        base_dict.update(
            {
                "document_count": self.document_count,
                "total_chunks": self.total_chunks,
                "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
            }
        )
        return base_dict

    @property
    def is_active(self) -> bool:
        """Check if knowledge base is active."""
        return self.status == "active"

    def update_document_stats(self, document_count: int, total_chunks: int) -> None:
        """Update document statistics."""
        self.document_count = document_count
        self.total_chunks = total_chunks

    def mark_sync_completed(self) -> None:
        """Mark the last sync time as now."""
        self.last_sync_at = datetime.now(UTC)

    def get_source_types(self) -> set:
        """Get all source types used by documents in this knowledge base."""
        return {doc.source_type for doc in self.documents}

    def get_document_count_by_source_type(self) -> dict[str, int]:
        """Get document counts grouped by source type."""
        source_counts = {}
        for doc in self.documents:
            source_counts[doc.source_type] = source_counts.get(doc.source_type, 0) + 1
        return source_counts

    # NOTE: KB prompts are now managed at the model configuration level
    # Use ModelConfigurationService.assign_kb_prompt() for KB-specific prompts
    # This allows the same KB to have different prompts for different model configurations

    def get_rag_config(self) -> dict[str, Any]:
        """Get RAG configuration as dictionary with proper defaults.

        Uses ConfigurationManager to provide consistent defaults when
        database values are NULL or missing.
        """
        from ..core.config import get_config_manager

        config_manager = get_config_manager()

        # Build KB config dict from database values (may contain None values)
        kb_config = {
            "include_references": self.rag_include_references,
            "reference_format": self.rag_reference_format,
            "context_format": self.rag_context_format,
            "prompt_template": self.rag_prompt_template,
            "search_threshold": float(self.rag_search_threshold)
            if isinstance(self.rag_search_threshold, (int, float, str))
            else self.rag_search_threshold,
            "max_results": self.rag_max_results,
            "chunk_overlap_ratio": float(self.rag_chunk_overlap_ratio)
            if isinstance(self.rag_chunk_overlap_ratio, (int, float, str))
            else self.rag_chunk_overlap_ratio,
            "search_type": self.rag_search_type,
            "title_weighting_enabled": self.rag_title_weighting_enabled,
            "title_weight_multiplier": float(self.rag_title_weight_multiplier)
            if isinstance(self.rag_title_weight_multiplier, (int, float, str))
            else self.rag_title_weight_multiplier,
            "title_chunk_enabled": self.rag_title_chunk_enabled,
            "max_chunks_per_document": self.rag_max_chunks_per_document,
            "minimum_query_words": self.rag_minimum_query_words,
            # Full document escalation fields from DB
            "fetch_full_documents": self.rag_fetch_full_documents,
            "full_doc_max_docs": self.rag_full_doc_max_docs,
            "full_doc_token_cap": self.rag_full_doc_token_cap,
        }

        # Use ConfigurationManager to resolve final values with proper defaults
        return {
            "include_references": config_manager.get_rag_include_references(kb_config=kb_config),
            "reference_format": config_manager.get_rag_reference_format(kb_config=kb_config),
            "context_format": config_manager.get_rag_context_format(kb_config=kb_config),
            "prompt_template": kb_config["prompt_template"] or config_manager.settings.rag_prompt_template_default,
            "search_threshold": config_manager.get_rag_search_threshold(kb_config=kb_config),
            "max_results": config_manager.get_rag_max_results(kb_config=kb_config),
            "chunk_overlap_ratio": config_manager.get_rag_chunk_overlap_ratio(kb_config=kb_config),
            "search_type": config_manager.get_rag_search_type(kb_config=kb_config),
            "title_weighting_enabled": config_manager.get_title_weighting_enabled(kb_config=kb_config),
            "title_weight_multiplier": config_manager.get_title_weight_multiplier(kb_config=kb_config),
            "title_chunk_enabled": config_manager.get_title_chunk_enabled(kb_config=kb_config),
            "max_chunks_per_document": config_manager.get_max_chunks_per_document(kb_config=kb_config),
            "minimum_query_words": config_manager.get_rag_minimum_query_words(kb_config=kb_config),
            "fetch_full_documents": config_manager.get_full_document_enabled(kb_config=kb_config),
            "full_doc_max_docs": config_manager.get_full_document_max_docs(kb_config=kb_config),
            "full_doc_token_cap": config_manager.get_full_document_token_cap(kb_config=kb_config),
            "version": self.rag_config_version or "1.0",
        }

    def update_rag_config(self, config: dict[str, Any]) -> None:
        """Update RAG configuration from dictionary."""
        if "include_references" in config:
            self.rag_include_references = config["include_references"]
        if "reference_format" in config:
            self.rag_reference_format = config["reference_format"]
        if "context_format" in config:
            self.rag_context_format = config["context_format"]
        if "prompt_template" in config:
            self.rag_prompt_template = config["prompt_template"]
        if "search_threshold" in config:
            self.rag_search_threshold = config["search_threshold"]
        if "max_results" in config:
            self.rag_max_results = config["max_results"]
        if "chunk_overlap_ratio" in config:
            self.rag_chunk_overlap_ratio = config["chunk_overlap_ratio"]
        if "search_type" in config:
            self.rag_search_type = config["search_type"]
        if "version" in config:
            self.rag_config_version = config["version"]
        if "title_weighting_enabled" in config:
            self.rag_title_weighting_enabled = config["title_weighting_enabled"]
        if "title_weight_multiplier" in config:
            self.rag_title_weight_multiplier = config["title_weight_multiplier"]
        if "title_chunk_enabled" in config:
            self.rag_title_chunk_enabled = config["title_chunk_enabled"]
        if "minimum_query_words" in config:
            self.rag_minimum_query_words = config["minimum_query_words"]
        if "fetch_full_documents" in config:
            self.rag_fetch_full_documents = config["fetch_full_documents"]
        if "full_doc_max_docs" in config:
            self.rag_full_doc_max_docs = config["full_doc_max_docs"]
        if "full_doc_token_cap" in config:
            self.rag_full_doc_token_cap = config["full_doc_token_cap"]
