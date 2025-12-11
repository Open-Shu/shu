"""
Pydantic schemas for knowledge base operations.

This module defines the request/response schemas for knowledge base
configuration and management endpoints.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum


class KnowledgeBaseStatus(str, Enum):
    """Knowledge base status options."""
    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"


class RAGConfig(BaseModel):
    """Schema for RAG configuration settings."""

    include_references: bool = Field(
        default=True,
        description="Include references section in response"
    )
    # Full Document Escalation
    fetch_full_documents: bool = Field(
        default=False,
        description="When enabled, escalate to fetching full documents for top results"
    )
    full_doc_max_docs: int = Field(
        default=1,
        ge=1,
        le=5,
        description="Maximum number of top documents to escalate to full text"
    )
    full_doc_token_cap: int = Field(
        default=8000,
        ge=1000,
        le=200000,
        description="Maximum tokens to include per escalated document (estimated)"
    )
    reference_format: str = Field(
        default="markdown",
        description="Reference format: 'markdown' or 'text'"
    )
    context_format: str = Field(
        default="detailed",
        description="Context format: 'detailed' or 'simple'"
    )
    prompt_template: str = Field(
        default="custom",
        description="Prompt template type: 'academic', 'business', 'technical', or 'custom'"
    )
    search_threshold: float = Field(
        default=0.7,
        ge=0.1,
        le=1.0,
        description="Minimum similarity score for content to be considered relevant (0.1-1.0)"
    )
    max_results: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of relevant chunks to retrieve (1-50)"
    )
    chunk_overlap_ratio: float = Field(
        default=0.2,
        ge=0.0,
        le=0.5,
        description="Ratio of chunk overlap for better context continuity (0.0-0.5)"
    )
    search_type: str = Field(
        default="hybrid",
        description="Search type: 'similarity', 'keyword', or 'hybrid'"
    )

    # Title Search Configuration
    title_weighting_enabled: bool = Field(
        default=True,
        description="Enable title weighting in search results"
    )
    title_weight_multiplier: float = Field(
        default=3.0,
        ge=1.0,
        le=10.0,
        description="Multiplier for title match scores (1.0-10.0)"
    )
    title_chunk_enabled: bool = Field(
        default=True,
        description="Create dedicated title chunks for better title matching"
    )
    max_chunks_per_document: int = Field(
        default=4,
        ge=1,
        le=10,
        description="Maximum number of chunks to return per document (1-10)"
    )
    minimum_query_words: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Minimum number of words required in query to trigger RAG processing (1-20)"
    )

    @field_validator("reference_format")
    @classmethod
    def validate_reference_format(cls, v):
        """Validate reference format."""
        if v not in ["markdown", "text"]:
            raise ValueError("reference_format must be 'markdown' or 'text'")
        return v
    
    @field_validator("context_format")
    @classmethod
    def validate_context_format(cls, v):
        """Validate context format."""
        if v not in ["detailed", "simple"]:
            raise ValueError("context_format must be 'detailed' or 'simple'")
        return v
    
    @field_validator("prompt_template")
    @classmethod
    def validate_prompt_template(cls, v):
        """Validate prompt template."""
        if v not in ["academic", "business", "technical", "custom"]:
            raise ValueError("prompt_template must be 'academic', 'business', 'technical', or 'custom'")
        return v

    @field_validator("search_type")
    @classmethod
    def validate_search_type(cls, v):
        """Validate search type."""
        if v not in ["similarity", "keyword", "hybrid"]:
            raise ValueError("search_type must be 'similarity', 'keyword', or 'hybrid'")
        return v


class RAGConfigResponse(BaseModel):
    """Response schema for RAG configuration endpoint."""

    include_references: bool = Field(True, description="Include references section")
    reference_format: str = Field("markdown", description="Reference format")
    context_format: str = Field("detailed", description="Context format")
    prompt_template: str = Field("custom", description="Prompt template type")
    search_threshold: float = Field(0.7, description="Minimum similarity score for relevance")
    max_results: int = Field(10, description="Maximum number of chunks to retrieve")
    chunk_overlap_ratio: float = Field(0.2, description="Chunk overlap ratio for context continuity")
    search_type: str = Field("hybrid", description="Search type: similarity, keyword, or hybrid")

    # Title Search Configuration
    title_weighting_enabled: bool = Field(True, description="Enable title weighting in search results")
    title_weight_multiplier: float = Field(3.0, description="Multiplier for title match scores")
    title_chunk_enabled: bool = Field(True, description="Create dedicated title chunks for better title matching")

    # Document Chunk Configuration
    max_chunks_per_document: int = Field(4, description="Maximum number of chunks to return per document")

    # Query Processing Configuration
    minimum_query_words: int = Field(3, description="Minimum query words for RAG processing")

    # Full Document Escalation
    fetch_full_documents: bool = Field(False, description="When enabled, escalate to fetching full documents for top results")
    full_doc_max_docs: int = Field(1, description="Maximum number of top documents to escalate to full text")
    full_doc_token_cap: int = Field(8000, description="Maximum tokens to include per escalated document (estimated)")

    version: str = Field("1.0", description="Configuration version")


class KnowledgeBaseBase(BaseModel):
    """Base schema for knowledge base with common fields."""

    name: str = Field(..., min_length=1, max_length=255, description="Knowledge base name")
    description: Optional[str] = Field(None, description="Knowledge base description")
    sync_enabled: bool = Field(True, description="Whether sync is enabled")
    embedding_model: str = Field("sentence-transformers/all-MiniLM-L6-v2", description="Embedding model to use")
    chunk_size: int = Field(1000, ge=100, le=5000, description="Text chunk size")
    chunk_overlap: int = Field(200, ge=0, le=1000, description="Chunk overlap size")
    
    @field_validator("chunk_overlap")
    @classmethod
    def validate_chunk_overlap(cls, v, info):
        """Validate that chunk overlap is less than chunk size."""
        chunk_size = info.data.get("chunk_size", 1000)
        if v >= chunk_size:
            raise ValueError("chunk_overlap must be less than chunk_size")
        return v
    



class KnowledgeBaseCreate(KnowledgeBaseBase):
    """Schema for creating a new knowledge base."""
    pass


class KnowledgeBaseUpdate(BaseModel):
    """Schema for updating an existing knowledge base."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    sync_enabled: Optional[bool] = None
    embedding_model: Optional[str] = None
    chunk_size: Optional[int] = Field(None, ge=100, le=5000)
    chunk_overlap: Optional[int] = Field(None, ge=0, le=1000)
    
    @field_validator("chunk_overlap")
    @classmethod
    def validate_chunk_overlap(cls, v, info):
        """Validate that chunk overlap is less than chunk size."""
        chunk_size = info.data.get("chunk_size")
        if v is not None and chunk_size is not None and v >= chunk_size:
            raise ValueError("chunk_overlap must be less than chunk_size")
        return v


class KnowledgeBaseResponse(KnowledgeBaseBase):
    """Schema for knowledge base responses."""
    
    id: str = Field(..., description="Knowledge base ID")
    status: KnowledgeBaseStatus = Field(..., description="Knowledge base status")
    document_count: int = Field(0, description="Number of documents")
    total_chunks: int = Field(0, description="Total number of chunks")
    last_sync_at: Optional[datetime] = Field(None, description="Last sync timestamp")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    
    class Config:
        from_attributes = True


class KnowledgeBaseList(BaseModel):
    """Schema for listing knowledge bases."""
    
    items: List[KnowledgeBaseResponse] = Field(..., description="List of knowledge bases")
    total: int = Field(..., description="Total number of knowledge bases")
    page: int = Field(1, description="Current page number")
    size: int = Field(10, description="Items per page")
    pages: int = Field(..., description="Total number of pages")


class KnowledgeBaseSummary(BaseModel):
    """Schema for knowledge base summary information."""
    
    id: str = Field(..., description="Knowledge base ID")
    name: str = Field(..., description="Knowledge base name")
    description: Optional[str] = Field(None, description="Knowledge base description")
    source_types: List[str] = Field(..., description="Source types used in this knowledge base")
    status: KnowledgeBaseStatus = Field(..., description="Status")
    document_count: int = Field(0, description="Number of documents")
    total_chunks: int = Field(0, description="Total number of chunks")
    last_sync_at: Optional[datetime] = Field(None, description="Last sync timestamp")
    
    class Config:
        from_attributes = True


class KnowledgeBaseStats(BaseModel):
    """Schema for knowledge base statistics."""
    
    total_knowledge_bases: int = Field(..., description="Total number of knowledge bases")
    active_knowledge_bases: int = Field(..., description="Number of active knowledge bases")
    total_documents: int = Field(..., description="Total number of documents across all KBs")
    total_chunks: int = Field(..., description="Total number of chunks across all KBs")
    sync_enabled_count: int = Field(..., description="Number of KBs with sync enabled")
    source_type_breakdown: Dict[str, int] = Field(..., description="Breakdown by source type")
    status_breakdown: Dict[str, int] = Field(..., description="Breakdown by status")