"""Pydantic schemas for document operations.

This module defines the request/response schemas for document
and document chunk operations.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ProcessingStatus(str, Enum):
    """Document processing status options."""

    PENDING = "pending"
    EXTRACTING = "extracting"
    EMBEDDING = "embedding"
    PROFILING = "profiling"
    PROCESSED = "processed"
    ERROR = "error"


class DocumentBase(BaseModel):
    """Base schema for document with common fields."""

    title: str = Field(..., description="Document title")
    file_type: str = Field(..., description="File type (pdf, docx, etc.)")
    source_type: str = Field(..., description="Source type (google_drive, filesystem, etc.)")
    source_id: str = Field(..., description="Original source ID")
    source_url: str | None = Field(None, description="Source URL or path")
    file_size: int | None = Field(None, description="File size in bytes")
    mime_type: str | None = Field(None, description="MIME type")


class DocumentCreate(DocumentBase):
    """Schema for creating a new document."""

    knowledge_base_id: str = Field(..., description="Knowledge base ID")
    content: str = Field(..., description="Document content")
    content_hash: str | None = Field(None, description="SHA256 hash of content for fast comparison")
    source_hash: str | None = Field(None, description="Hash of original source content (md5Checksum, etag, etc.)")
    source_metadata: str | None = Field(None, description="Source metadata as JSON")
    source_modified_at: datetime | None = Field(None, description="Source modification time")

    # Extraction metadata for OCR verification and tracking
    extraction_method: str | None = Field(None, description="Text extraction method (ocr, text, pdfplumber, etc.)")
    extraction_engine: str | None = Field(None, description="Extraction engine used (paddleocr, tesseract, etc.)")
    extraction_confidence: float | None = Field(None, description="Average confidence score from extraction")
    extraction_duration: float | None = Field(None, description="Extraction time in seconds")
    extraction_metadata: dict[str, Any] | None = Field(None, description="Detailed extraction information")


class DocumentUpdate(BaseModel):
    """Schema for updating an existing document."""

    title: str | None = None
    content: str | None = None
    source_metadata: str | None = None
    source_modified_at: datetime | None = None
    processing_status: ProcessingStatus | None = None
    processing_error: str | None = None


class DocumentResponse(BaseModel):
    """Schema for document responses."""

    id: str = Field(..., description="Document ID")
    knowledge_base_id: str = Field(..., description="Knowledge base ID")
    source_type: str = Field(..., description="Source type")
    source_id: str = Field(..., description="Original source ID")
    title: str = Field(..., description="Document title")
    file_type: str = Field(..., description="File type")
    file_size: int | None = Field(None, description="File size in bytes")
    mime_type: str | None = Field(None, description="MIME type")
    source_url: str | None = Field(None, description="Source URL or path")
    source_modified_at: datetime | None = Field(None, description="Source modification time")
    source_metadata: str | None = Field(None, description="Source metadata as JSON")
    processing_status: str = Field(..., description="Processing status")
    processing_error: str | None = Field(None, description="Processing error message")
    processed_at: datetime | None = Field(None, description="Processing completion time")
    word_count: int | None = Field(None, description="Number of words")
    character_count: int | None = Field(None, description="Number of characters")
    chunk_count: int = Field(0, description="Number of chunks")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    # Extraction metadata for OCR verification and tracking
    extraction_method: str | None = Field(None, description="Text extraction method (ocr, text, pdfplumber, etc.)")
    extraction_engine: str | None = Field(None, description="Extraction engine used (paddleocr, tesseract, etc.)")
    extraction_confidence: float | None = Field(None, description="Average confidence score from extraction")
    extraction_duration: float | None = Field(None, description="Extraction time in seconds")
    extraction_metadata: dict[str, Any] | None = Field(None, description="Detailed extraction information")

    class Config:
        from_attributes = True


class DocumentDetailResponse(DocumentResponse):
    """Schema for detailed document responses including content."""

    content: str = Field(..., description="Document content")
    source_metadata: str | None = Field(None, description="Source metadata as JSON")


class DocumentList(BaseModel):
    """Schema for listing documents."""

    items: list[DocumentResponse] = Field(..., description="List of documents")
    total: int = Field(..., description="Total number of documents")
    page: int = Field(1, description="Current page number")
    size: int = Field(10, description="Items per page")
    pages: int = Field(..., description="Total number of pages")


class DocumentSummary(BaseModel):
    """Schema for document summary information."""

    id: str = Field(..., description="Document ID")
    title: str = Field(..., description="Document title")
    file_type: str = Field(..., description="File type")
    processing_status: ProcessingStatus = Field(..., description="Processing status")
    chunk_count: int = Field(0, description="Number of chunks")
    word_count: int | None = Field(None, description="Number of words")
    created_at: datetime = Field(..., description="Creation timestamp")

    class Config:
        from_attributes = True


class DocumentChunkBase(BaseModel):
    """Base schema for document chunk with common fields."""

    chunk_index: int = Field(..., description="Chunk position within document")
    content: str = Field(..., description="Chunk content")
    char_count: int = Field(..., description="Character count")
    word_count: int | None = Field(None, description="Word count")
    token_count: int | None = Field(None, description="Token count")
    start_char: int | None = Field(None, description="Start position in document")
    end_char: int | None = Field(None, description="End position in document")


class DocumentChunkCreate(DocumentChunkBase):
    """Schema for creating a new document chunk."""

    document_id: str = Field(..., description="Document ID")
    knowledge_base_id: str = Field(..., description="Knowledge base ID")


class DocumentChunkResponse(DocumentChunkBase):
    """Schema for document chunk responses."""

    id: str = Field(..., description="Chunk ID")
    document_id: str = Field(..., description="Document ID")
    knowledge_base_id: str = Field(..., description="Knowledge base ID")
    has_embedding: bool = Field(False, description="Whether chunk has embedding")
    embedding_model: str | None = Field(None, description="Embedding model used")
    embedding_created_at: datetime | None = Field(None, description="Embedding creation time")
    created_at: datetime = Field(..., description="Creation timestamp")

    class Config:
        from_attributes = True


class DocumentChunkWithScore(DocumentChunkResponse):
    """Schema for document chunk with similarity score."""

    similarity_score: float = Field(..., description="Similarity score (0.0 to 1.0)")
    document_title: str | None = Field(None, description="Parent document title")
    source_id: str | None = Field(None, description="Original source ID")
    source_url: str | None = Field(None, description="Source URL or path")
    file_type: str | None = Field(None, description="Document file type")
    source_type: str | None = Field(None, description="Document source type")


class DocumentChunkList(BaseModel):
    """Schema for listing document chunks."""

    items: list[DocumentChunkResponse] = Field(..., description="List of document chunks")
    total: int = Field(..., description="Total number of chunks")
    page: int = Field(1, description="Current page number")
    size: int = Field(10, description="Items per page")
    pages: int = Field(..., description="Total number of pages")


class DocumentStats(BaseModel):
    """Schema for document statistics."""

    total_documents: int = Field(..., description="Total number of documents")
    processed_documents: int = Field(..., description="Number of processed documents")
    pending_documents: int = Field(..., description="Number of pending documents")
    error_documents: int = Field(..., description="Number of documents with errors")
    total_chunks: int = Field(..., description="Total number of chunks")
    total_words: int = Field(..., description="Total word count")
    total_characters: int = Field(..., description="Total character count")
    file_type_breakdown: dict = Field(..., description="Breakdown by file type")
    source_type_breakdown: dict = Field(..., description="Breakdown by source type")
    processing_status_breakdown: dict = Field(..., description="Breakdown by processing status")
    average_chunks_per_document: float = Field(..., description="Average chunks per document")
    average_words_per_document: float = Field(..., description="Average words per document")


class DocumentSearchRequest(BaseModel):
    """Schema for document search requests."""

    query: str = Field(..., min_length=1, description="Search query")
    knowledge_base_id: str | None = Field(None, description="Filter by knowledge base")
    file_types: list[str] | None = Field(None, description="Filter by file types")
    source_types: list[str] | None = Field(None, description="Filter by source types")
    processing_status: ProcessingStatus | None = Field(None, description="Filter by status")
    created_after: datetime | None = Field(None, description="Filter by creation date")
    created_before: datetime | None = Field(None, description="Filter by creation date")
    limit: int = Field(10, ge=1, le=100, description="Number of results to return")


class DocumentSearchResponse(BaseModel):
    """Schema for document search responses."""

    items: list[DocumentResponse] = Field(..., description="Matching documents")
    total: int = Field(..., description="Total number of matches")
    query: str = Field(..., description="Search query")
    execution_time: float = Field(..., description="Search execution time in seconds")
