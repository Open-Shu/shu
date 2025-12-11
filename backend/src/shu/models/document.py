"""
Document and DocumentChunk models for Shu RAG Backend.

This module defines the Document and DocumentChunk models which store
document metadata, content, and vector embeddings.
"""

from sqlalchemy import Column, String, Integer, Text, DateTime, ForeignKey, Float, JSON
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import ARRAY
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    # Fallback for development without pgvector
    Vector = lambda dim: Text

from .base import BaseModel


class Document(BaseModel):
    """
    Document metadata and content.
    
    Represents a single document that has been processed and stored
    in a knowledge base.
    """
    
    __tablename__ = "documents"
    
    # Foreign key to knowledge base
    knowledge_base_id = Column(String, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Source type label (e.g., "plugin:gmail", "filesystem", etc.)
    source_type = Column(String(50), nullable=False, index=True)

    # Document identification
    source_id = Column(String(500), nullable=False, index=True)  # Original document ID from source
    title = Column(String(500), nullable=False)
    
    # File information
    file_type = Column(String(50), nullable=False)  # 'pdf', 'docx', 'txt', etc.
    file_size = Column(Integer, nullable=True)  # Size in bytes
    mime_type = Column(String(100), nullable=True)
    
    # Document content
    content = Column(Text, nullable=False)  # Full text content
    content_hash = Column(String(64), nullable=True, index=True)  # SHA256 hash of content for fast comparison
    source_hash = Column(String(64), nullable=True, index=True)  # Hash of original source content (md5Checksum, etag, etc.)
    
    # Processing information
    processing_status = Column(String(50), default="pending", nullable=False)  # 'pending', 'processed', 'error'
    processing_error = Column(Text, nullable=True)
    
    # Extraction metadata (for OCR verification and tracking)
    extraction_method = Column(String(50), nullable=True)  # 'ocr', 'text', 'pdfplumber', 'pymupdf', etc.
    extraction_engine = Column(String(50), nullable=True)  # 'paddleocr', 'tesseract', 'easyocr', etc.
    extraction_confidence = Column(Float, nullable=True)  # Average confidence score
    extraction_duration = Column(Float, nullable=True)  # Extraction time in seconds
    extraction_metadata = Column(JSON, nullable=True)  # Detailed extraction info (pages, errors, etc.)
    
    # Source metadata
    source_url = Column(String(1000), nullable=True)  # Original URL or path
    source_modified_at = Column(TIMESTAMP(timezone=True), nullable=True)
    source_metadata = Column(Text, nullable=True)  # JSON string of source-specific metadata

    # Processing timestamps
    processed_at = Column(TIMESTAMP(timezone=True), nullable=True)
    
    # Content statistics
    word_count = Column(Integer, nullable=True)
    character_count = Column(Integer, nullable=True)
    chunk_count = Column(Integer, default=0, nullable=False)
    
    # Relationships
    knowledge_base = relationship("KnowledgeBase", back_populates="documents")
    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Document(id={self.id}, title='{self.title}', kb_id='{self.knowledge_base_id}')>"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary with computed fields."""
        base_dict = super().to_dict()
        base_dict.update({
            "chunk_count": self.chunk_count,
            "processing_status": self.processing_status,
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
            "source_modified_at": self.source_modified_at.isoformat() if self.source_modified_at else None,
        })
        return base_dict
    
    @property
    def is_processed(self) -> bool:
        """Check if document has been processed successfully."""
        return self.processing_status == "processed"
    
    @property
    def has_error(self) -> bool:
        """Check if document processing had an error."""
        return self.processing_status == "error"
    
    def mark_processed(self) -> None:
        """Mark document as processed successfully."""
        self.processing_status = "processed"
        self.processed_at = datetime.now(timezone.utc)
        self.processing_error = None
    
    def mark_error(self, error_message: str) -> None:
        """Mark document as having a processing error."""
        self.processing_status = "error"
        self.processing_error = error_message
        self.processed_at = datetime.now(timezone.utc)
    
    def update_content_stats(self, word_count: int, character_count: int, chunk_count: int) -> None:
        """Update content statistics."""
        self.word_count = word_count
        self.character_count = character_count
        self.chunk_count = chunk_count


class DocumentChunk(BaseModel):
    """
    Document chunk with vector embedding.
    
    Represents a processed chunk of a document with its vector embedding
    for similarity search.
    """
    
    __tablename__ = "document_chunks"
    
    # Foreign keys
    document_id = Column(String, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    knowledge_base_id = Column(String, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Chunk information
    chunk_index = Column(Integer, nullable=False)  # Position within the document
    content = Column(Text, nullable=False)  # Chunk text content
    
    # Vector embedding (384 dimensions for all-MiniLM-L6-v2)
    embedding = Column(Vector(384), nullable=True)
    
    # Chunk metadata
    char_count = Column(Integer, nullable=False)
    word_count = Column(Integer, nullable=True)
    token_count = Column(Integer, nullable=True)
    
    # Position information
    start_char = Column(Integer, nullable=True)  # Start position in original document
    end_char = Column(Integer, nullable=True)    # End position in original document
    
    # Similarity search metadata
    embedding_model = Column(String(100), nullable=True)  # Model used for embedding
    embedding_created_at = Column(TIMESTAMP(timezone=True), nullable=True)
    
    # Chunk metadata
    chunk_metadata = Column(JSON, nullable=True)  # Flexible metadata storage for chunk-specific data
    
    # Relationships
    document = relationship("Document", back_populates="chunks")
    knowledge_base = relationship("KnowledgeBase")
    
    def __repr__(self) -> str:
        return f"<DocumentChunk(id={self.id}, doc_id='{self.document_id}', chunk_index={self.chunk_index})>"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary with computed fields."""
        base_dict = super().to_dict()
        base_dict.update({
            "char_count": self.char_count,
            "word_count": self.word_count,
            "token_count": self.token_count,
            "chunk_index": self.chunk_index,
            "has_embedding": self.embedding is not None,
            "embedding_created_at": self.embedding_created_at.isoformat() if self.embedding_created_at else None,
        })
        # Don't include the actual embedding vector in the dict (too large)
        return base_dict
    
    @property
    def has_embedding(self) -> bool:
        """Check if chunk has an embedding."""
        return self.embedding is not None
    
    def set_embedding(self, embedding: List[float], model_name: str) -> None:
        """Set the embedding vector for this chunk."""
        self.embedding = embedding
        self.embedding_model = model_name
        self.embedding_created_at = datetime.now(timezone.utc)
    
    def get_preview(self, max_chars: int = 100) -> str:
        """Get a preview of the chunk content."""
        if len(self.content) <= max_chars:
            return self.content
        return self.content[:max_chars] + "..."
    
    def calculate_similarity_score(self, query_embedding: List[float]) -> float:
        """Calculate cosine similarity with query embedding."""
        if not self.embedding:
            return 0.0
        
        # This would typically be done at the database level with pgvector
        # For now, return a placeholder
        return 0.0
    
    @classmethod
    def create_from_text(
        cls,
        document_id: str,
        knowledge_base_id: str,
        chunk_index: int,
        content: str,
        start_char: Optional[int] = None,
        end_char: Optional[int] = None
    ) -> "DocumentChunk":
        """Create a document chunk from text content."""
        chunk = cls(
            document_id=document_id,
            knowledge_base_id=knowledge_base_id,
            chunk_index=chunk_index,
            content=content,
            char_count=len(content),
            word_count=len(content.split()) if content else 0,
            start_char=start_char,
            end_char=end_char,
        )
        return chunk 