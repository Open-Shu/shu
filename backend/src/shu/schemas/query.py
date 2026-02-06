"""Pydantic schemas for query operations.

This module defines the request/response schemas for vector similarity
search and query operations.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..core.config import get_settings_instance
from .document import DocumentChunkWithScore

settings = get_settings_instance()


class QueryType(str, Enum):
    """Query type options."""

    SIMILARITY = "similarity"
    KEYWORD = "keyword"
    HYBRID = "hybrid"


class RagRewriteMode(str, Enum):
    """RAG query preparation strategies."""

    NO_RAG = "no_rag"
    RAW_QUERY = "raw_query"
    DISTILL_CONTEXT = "distill_context"
    REWRITE_ENHANCED = "rewrite_enhanced"


class QueryRequest(BaseModel):
    """Unified request model for document queries.

    Supports all search types and provides backward compatibility with
    the deprecated SimilaritySearchRequest format.
    """

    query: str = Field(..., description="Query text to search for", max_length=settings.max_query_length)
    query_type: str = Field("similarity", description="Query type: similarity, keyword, or hybrid")
    limit: int = Field(10, ge=1, le=100, description="Maximum number of results to return")
    similarity_threshold: float | None = Field(None, ge=0.0, le=1.0, description="Minimum similarity score threshold")
    # Backward compatibility alias for SimilaritySearchRequest
    threshold: float | None = Field(
        None, ge=0.0, le=1.0, description="Alias for similarity_threshold (backward compatibility)"
    )
    include_metadata: bool = Field(True, description="Include document metadata in results")
    # Additional backward compatibility fields
    include_embeddings: bool = Field(
        False, description="Include embeddings in response (for similarity search compatibility)"
    )
    rag_rewrite_mode: RagRewriteMode = Field(
        default=RagRewriteMode.RAW_QUERY,
        description="Strategy for preparing the retrieval query before execution",
    )

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        """Validate query string."""
        if not v.strip():
            raise ValueError("Query cannot be empty")
        return v.strip()

    @field_validator("query_type")
    @classmethod
    def validate_query_type(cls, v: str) -> str:
        """Validate query type."""
        valid_types = ["similarity", "keyword", "hybrid"]
        if v not in valid_types:
            raise ValueError(f"Query type must be one of: {valid_types}")
        return v

    def model_post_init(self, __context) -> None:
        """Post-initialization to handle backward compatibility."""
        # If threshold is provided but similarity_threshold is not, use threshold
        if self.threshold is not None and self.similarity_threshold is None:
            self.similarity_threshold = self.threshold
        # If similarity_threshold is provided but threshold is not, sync them
        elif self.similarity_threshold is not None and self.threshold is None:
            self.threshold = self.similarity_threshold


class QueryResult(BaseModel):
    """Schema for individual query results."""

    chunk_id: str = Field(..., description="Document chunk ID")
    document_id: str = Field(..., description="Document ID")
    document_title: str = Field(..., description="Document title")
    content: str = Field(..., description="Chunk content")
    similarity_score: float = Field(..., description="Similarity score")
    chunk_index: int = Field(..., description="Chunk position in document")
    start_char: int | None = Field(None, description="Start position in document")
    end_char: int | None = Field(None, description="End position in document")

    # Document metadata
    file_type: str = Field(..., description="Document file type")
    source_url: str | None = Field(None, description="Source URL")
    source_id: str | None = Field(None, description="Original source ID")
    created_at: datetime = Field(..., description="Document creation timestamp")

    class Config:
        """Pydantic configuration."""

        from_attributes = True


class QueryResponse(BaseModel):
    """Schema for query responses."""

    results: list[QueryResult] = Field(..., description="Query results")
    total_results: int = Field(..., description="Total number of results")
    query: str = Field(..., description="Original query")
    query_type: QueryType = Field(..., description="Query type used")
    execution_time: float = Field(..., description="Query execution time in seconds")
    similarity_threshold: float = Field(..., description="Similarity threshold applied")

    # Query metadata
    embedding_model: str | None = Field(None, description="Embedding model used")
    processed_at: datetime = Field(..., description="Query processing timestamp")


class QueryWithContext(BaseModel):
    """Schema for queries with conversation context."""

    query: str = Field(..., min_length=1, description="Current query")
    context: list[str] | None = Field(None, description="Previous conversation context")
    system_prompt: str | None = Field(None, description="System prompt for context")
    query_type: QueryType = Field(QueryType.SIMILARITY, description="Type of query")
    limit: int = Field(10, ge=1, le=100, description="Number of results to return")
    similarity_threshold: float = Field(0.0, ge=0.0, le=1.0, description="Minimum similarity threshold")


class QueryStats(BaseModel):
    """Schema for query statistics."""

    total_queries: int = Field(..., description="Total number of queries")
    successful_queries: int = Field(..., description="Number of successful queries")
    failed_queries: int = Field(..., description="Number of failed queries")
    average_execution_time: float = Field(..., description="Average execution time")
    average_results_per_query: float = Field(..., description="Average results per query")
    query_type_breakdown: dict[str, int] = Field(..., description="Breakdown by query type")
    most_common_queries: list[str] = Field(..., description="Most common queries")
    recent_queries: list[str] = Field(..., description="Recent queries")


class QueryHistory(BaseModel):
    """Schema for query history."""

    id: str = Field(..., description="Query ID")
    query: str = Field(..., description="Query string")
    query_type: QueryType = Field(..., description="Query type")
    knowledge_base_id: str = Field(..., description="Knowledge base ID")
    results_count: int = Field(..., description="Number of results returned")
    execution_time: float = Field(..., description="Execution time in seconds")
    similarity_threshold: float = Field(..., description="Similarity threshold")
    created_at: datetime = Field(..., description="Query timestamp")


class QueryHistoryList(BaseModel):
    """Schema for listing query history."""

    items: list[QueryHistory] = Field(..., description="Query history items")
    total: int = Field(..., description="Total number of queries")
    page: int = Field(1, description="Current page number")
    size: int = Field(10, description="Items per page")
    pages: int = Field(..., description="Total number of pages")


class SimilaritySearchRequest(BaseModel):
    """Schema for similarity search requests."""

    query: str = Field(..., min_length=1, description="Search query")
    limit: int = Field(10, ge=1, le=100, description="Number of results to return")
    threshold: float = Field(0.0, ge=0.0, le=1.0, description="Minimum similarity threshold")
    include_embeddings: bool = Field(False, description="Include embeddings in response")

    # Filtering options
    document_ids: list[str] | None = Field(None, description="Filter by document IDs")
    file_types: list[str] | None = Field(None, description="Filter by file types")
    created_after: datetime | None = Field(None, description="Filter by creation date")
    created_before: datetime | None = Field(None, description="Filter by creation date")


class SimilaritySearchResponse(BaseModel):
    """Schema for similarity search responses."""

    results: list[DocumentChunkWithScore] = Field(..., description="Search results")
    total_results: int = Field(..., description="Total number of results")
    query: str = Field(..., description="Original query")
    threshold: float = Field(..., description="Similarity threshold applied")
    execution_time: float = Field(..., description="Search execution time in seconds")
    embedding_model: str = Field(..., description="Embedding model used")


class RecommendationRequest(BaseModel):
    """Schema for document recommendation requests."""

    document_id: str = Field(..., description="Reference document ID")
    limit: int = Field(10, ge=1, le=100, description="Number of recommendations")
    threshold: float = Field(0.5, ge=0.0, le=1.0, description="Minimum similarity threshold")
    exclude_same_document: bool = Field(True, description="Exclude chunks from same document")


class RecommendationResponse(BaseModel):
    """Schema for document recommendation responses."""

    recommendations: list[DocumentChunkWithScore] = Field(..., description="Recommended chunks")
    reference_document_id: str = Field(..., description="Reference document ID")
    reference_document_title: str = Field(..., description="Reference document title")
    total_recommendations: int = Field(..., description="Total number of recommendations")
    execution_time: float = Field(..., description="Recommendation execution time in seconds")


class QueryAnalytics(BaseModel):
    """Schema for query analytics."""

    date: datetime = Field(..., description="Analytics date")
    total_queries: int = Field(..., description="Total queries for the date")
    unique_queries: int = Field(..., description="Unique queries for the date")
    average_execution_time: float = Field(..., description="Average execution time")
    average_results_per_query: float = Field(..., description="Average results per query")
    success_rate: float = Field(..., description="Query success rate")
    most_common_queries: list[str] = Field(..., description="Most common queries")
    knowledge_base_usage: dict[str, int] = Field(..., description="Usage by knowledge base")


class QueryAnalyticsList(BaseModel):
    """Schema for listing query analytics."""

    items: list[QueryAnalytics] = Field(..., description="Analytics items")
    total: int = Field(..., description="Total number of analytics records")
    date_range: dict[str, datetime] = Field(..., description="Date range for analytics")
    summary: dict[str, Any] = Field(..., description="Summary statistics")
