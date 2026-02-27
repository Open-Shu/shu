"""Document and DocumentChunk models for Shu RAG Backend.

This module defines the Document and DocumentChunk models which store
document metadata, content, and vector embeddings.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any, ClassVar

from sqlalchemy import JSON, Column, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import relationship
from typing_extensions import TypedDict

try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    # Fallback for development without pgvector
    def Vector(dim):  # noqa: N802
        return Text


from .base import BaseModel


# Type definitions for capability manifest structure (SHU-342)
class CapabilityManifest(TypedDict, total=False):
    """Structure for document capability manifest JSONB field.

    All fields are optional (total=False) since manifests may be partial.
    The answers_questions_about field should contain SPECIFIC, DISTINGUISHING details.
    """

    answers_questions_about: list[str]  # SPECIFIC topics with named entities, dates, figures
    provides_information_type: list[str]  # e.g., "facts", "opinions", "decisions"
    authority_level: str  # e.g., "primary", "secondary", "commentary"
    completeness: str  # e.g., "complete", "partial", "reference"
    question_domains: list[str]  # e.g., "who", "what", "when", "why", "how"


# Type definitions for relational context structure (SHU-355)
class RelationalContext(TypedDict, total=False):
    """Structure for document relational context JSONB field.

    Denormalized summary of participants and projects for query-time access.
    """

    participant_count: int
    primary_participants: list[str]  # Top entity names
    project_associations: list[str]  # Top project names
    temporal_scope: dict[str, str]  # start_date, end_date if detectable
    interaction_signals: list[str]  # e.g., "meeting", "decision", "request"


# Document pipeline status enum (Queue-Based Ingestion Pipeline)
# NOTE: This enum must be defined before the Document class since it's used in column defaults
class DocumentStatus(str, Enum):
    """Status values for document ingestion pipeline.

    Tracks the document's progress through the async ingestion pipeline:
    - PENDING: Document created, awaiting OCR/extraction
    - EXTRACTING: OCR/text extraction in progress
    - EMBEDDING: Chunking and embedding in progress
    - PROFILING: LLM profiling in progress (if enabled)
    - PROCESSED: Document fully processed and searchable
    - ERROR: Processing failed (see processing_error for details)
    """

    PENDING = "pending"
    EXTRACTING = "extracting"
    EMBEDDING = "embedding"
    PROFILING = "profiling"
    PROCESSED = "processed"
    ERROR = "error"


class Document(BaseModel):
    """Document metadata and content.

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
    source_hash = Column(
        String(64), nullable=True, index=True
    )  # Hash of original source content (md5Checksum, etag, etc.)

    # Processing status: pending → extracting → embedding → profiling → processed (or error)
    processing_status = Column(String(50), default="pending", nullable=False)
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

    # Shu RAG Document Profile (SHU-342)
    # Synopsis: One-paragraph summary for document-level retrieval
    synopsis = Column(Text, nullable=True)
    synopsis_embedding = Column(Vector(384), nullable=True)

    # Document type classification (narrative, transactional, technical, conversational)
    document_type = Column(String(50), nullable=True)

    # Capability manifest: what questions/queries this document can satisfy
    capability_manifest = Column(JSONB, nullable=True)

    # Profiling status: pending, in_progress, complete, failed
    profiling_status = Column(String(20), nullable=True, default="pending")
    profiling_error = Column(Text, nullable=True)
    # Coverage percentage: tracks what fraction of chunks were successfully profiled
    profiling_coverage_percent = Column(Float, nullable=True)

    # Shu RAG Relational Context (SHU-355)
    # Denormalized summary of participants and projects for query-time access
    relational_context = Column(JSONB, nullable=True)

    # Relationships
    knowledge_base = relationship("KnowledgeBase", back_populates="documents")
    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")
    queries = relationship("DocumentQuery", back_populates="document", cascade="all, delete-orphan")
    participants = relationship("DocumentParticipant", back_populates="document", cascade="all, delete-orphan")
    projects = relationship("DocumentProject", back_populates="document", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        """Represent as string."""
        return f"<Document(id={self.id}, title='{self.title}', kb_id='{self.knowledge_base_id}')>"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary with computed fields."""
        base_dict = super().to_dict()
        base_dict.update(
            {
                "chunk_count": self.chunk_count,
                "processing_status": self.processing_status,
                "processing_error": self.processing_error,
                "processed_at": self.processed_at.isoformat() if self.processed_at else None,
                "source_modified_at": self.source_modified_at.isoformat() if self.source_modified_at else None,
                # Profile fields (SHU-342)
                "synopsis": self.synopsis,
                "document_type": self.document_type,
                "capability_manifest": self.capability_manifest,
                "profiling_status": self.profiling_status,
                "profiling_error": self.profiling_error,
                "profiling_coverage_percent": self.profiling_coverage_percent,
                "has_synopsis_embedding": self.synopsis_embedding is not None,
                # Relational context (SHU-355)
                "relational_context": self.relational_context,
            }
        )
        return base_dict

    def to_list_dict(self) -> dict[str, Any]:
        """Convert to lightweight dictionary for list views.

        Excludes heavy fields like content, synopsis_embedding, capability_manifest,
        extraction_metadata, and source_metadata to minimize response size and
        avoid loading deferred columns.
        """
        return {
            "id": self.id,
            "knowledge_base_id": self.knowledge_base_id,
            "title": self.title,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "file_type": self.file_type,
            "file_size": self.file_size,
            "mime_type": self.mime_type,
            "processing_status": self.processing_status,
            "processing_error": self.processing_error,
            "extraction_method": self.extraction_method,
            "extraction_engine": self.extraction_engine,
            "extraction_confidence": self.extraction_confidence,
            "source_url": self.source_url,
            "word_count": self.word_count,
            "character_count": self.character_count,
            "chunk_count": self.chunk_count,
            "document_type": self.document_type,
            "profiling_status": self.profiling_status,
            "profiling_coverage_percent": self.profiling_coverage_percent,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
            "source_modified_at": self.source_modified_at.isoformat() if self.source_modified_at else None,
        }

    @property
    def is_processed(self) -> bool:
        """Check if document has been processed successfully."""
        return self.processing_status == DocumentStatus.PROCESSED.value

    @property
    def has_error(self) -> bool:
        """Check if document processing had an error."""
        return self.processing_status == DocumentStatus.ERROR.value

    def mark_processed(self) -> None:
        """Mark document as processed successfully."""
        self.processing_status = DocumentStatus.PROCESSED.value
        self.processed_at = datetime.now(UTC)
        self.processing_error = None

    def mark_error(self, error_message: str) -> None:
        """Mark document as having a processing error."""
        self.processing_status = DocumentStatus.ERROR.value
        self.processing_error = error_message
        self.processed_at = datetime.now(UTC)

    def update_status(self, new_status: "DocumentStatus") -> None:
        """Update document pipeline status.

        Args:
            new_status: The new DocumentStatus value

        """
        self.processing_status = new_status.value

        if new_status == DocumentStatus.PROCESSED:
            self.processed_at = datetime.now(UTC)
            self.processing_error = None
        elif new_status == DocumentStatus.ERROR:
            self.processed_at = datetime.now(UTC)
        else:
            # Clear stale timestamps and error fields when moving to non-terminal states
            self.processed_at = None
            self.processing_error = None

    def update_content_stats(self, word_count: int, character_count: int, chunk_count: int) -> None:
        """Update content statistics."""
        self.word_count = word_count
        self.character_count = character_count
        self.chunk_count = chunk_count

    # Profiling status helpers
    @property
    def is_profiled(self) -> bool:
        """Check if document has been profiled successfully."""
        return self.profiling_status == "complete"

    @property
    def profiling_failed(self) -> bool:
        """Check if document profiling failed."""
        return self.profiling_status == "failed"

    def mark_profiling_started(self) -> None:
        """Mark document as currently being profiled."""
        self.profiling_status = "in_progress"

    # Valid document types for profiling (must match schemas.profiling.DocumentType)
    VALID_DOCUMENT_TYPES: ClassVar[set[str]] = {"narrative", "transactional", "technical", "conversational"}

    def mark_profiling_complete(
        self,
        synopsis: str,
        document_type: str,
        capability_manifest: dict[str, Any],
        synopsis_embedding: list[float] | None = None,
        coverage_percent: float = 100.0,
    ) -> None:
        """Mark document profiling as complete with profile data.

        Args:
            synopsis: One-paragraph summary of the document
            document_type: Must be one of: narrative, transactional, technical, conversational
            capability_manifest: Dict describing what queries this document can satisfy
            synopsis_embedding: Optional vector embedding for synopsis
            coverage_percent: Percentage of chunks successfully profiled (0-100)

        Raises:
            ValueError: If document_type is not a valid type

        """
        # Validate document_type
        if document_type not in self.VALID_DOCUMENT_TYPES:
            raise ValueError(
                f"Invalid document_type '{document_type}'. "
                f"Must be one of: {', '.join(sorted(self.VALID_DOCUMENT_TYPES))}"
            )

        self.synopsis = synopsis
        self.document_type = document_type
        self.capability_manifest = capability_manifest
        self.synopsis_embedding = synopsis_embedding
        self.profiling_coverage_percent = coverage_percent
        self.profiling_status = "complete"
        self.profiling_error = None  # Clear any previous error

    def mark_profiling_failed(self, error_message: str | None = None) -> None:
        """Mark document profiling as failed with optional error message."""
        self.profiling_status = "failed"
        self.profiling_error = error_message


class DocumentChunk(BaseModel):
    """Document chunk with vector embedding.

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
    end_char = Column(Integer, nullable=True)  # End position in original document

    # Similarity search metadata
    embedding_model = Column(String(100), nullable=True)  # Model used for embedding
    embedding_created_at = Column(TIMESTAMP(timezone=True), nullable=True)

    # Chunk metadata
    chunk_metadata = Column(JSON, nullable=True)  # Flexible metadata storage for chunk-specific data

    # Shu RAG Chunk Profile (SHU-342)
    # Summary: One-line description with specific content for agent scanning and retrieval
    summary = Column(Text, nullable=True)
    # Keywords: Specific extractable terms (names, numbers, dates, technical terms)
    keywords = Column(JSONB, nullable=True)
    # Topics: Conceptual categories the chunk relates to (broader themes, domains)
    topics = Column(JSONB, nullable=True)

    # Relationships
    document = relationship("Document", back_populates="chunks")
    knowledge_base = relationship("KnowledgeBase")

    def __repr__(self) -> str:
        """Represent as string."""
        return f"<DocumentChunk(id={self.id}, doc_id='{self.document_id}', chunk_index={self.chunk_index})>"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary with computed fields."""
        base_dict = super().to_dict()
        base_dict.update(
            {
                "char_count": self.char_count,
                "word_count": self.word_count,
                "token_count": self.token_count,
                "chunk_index": self.chunk_index,
                "has_embedding": self.embedding is not None,
                "embedding_created_at": self.embedding_created_at.isoformat() if self.embedding_created_at else None,
                # Profile fields
                "summary": self.summary,
                "keywords": self.keywords,
                "topics": self.topics,
            }
        )
        # Don't include the actual embedding vector in the dict (too large)
        return base_dict

    @property
    def has_embedding(self) -> bool:
        """Check if chunk has an embedding."""
        return self.embedding is not None

    def set_embedding(self, embedding: list[float], model_name: str) -> None:
        """Set the embedding vector for this chunk."""
        self.embedding = embedding
        self.embedding_model = model_name
        self.embedding_created_at = datetime.now(UTC)

    def set_profile(
        self,
        summary: str,
        keywords: list[str],
        topics: list[str],
    ) -> None:
        """Set the chunk profile data.

        Args:
            summary: One-line description with specific content for agent scanning and retrieval.
            keywords: Specific extractable terms (names, numbers, dates, technical terms).
            topics: Conceptual categories the chunk relates to (broader themes, domains).

        """
        self.summary = summary
        self.keywords = keywords
        self.topics = topics

    @property
    def is_profiled(self) -> bool:
        """Check if chunk has been profiled (has summary)."""
        return self.summary is not None

    def get_preview(self, max_chars: int = 100) -> str:
        """Get a preview of the chunk content."""
        if len(self.content) <= max_chars:
            return self.content
        return self.content[:max_chars] + "..."

    def calculate_similarity_score(self, query_embedding: list[float]) -> float:
        """Calculate cosine similarity with query embedding.

        Note: In production, similarity search is done at the database level
        with pgvector for efficiency. This method exists for testing and
        fallback scenarios.

        Raises:
            ValueError: If embedding dimensions don't match (likely due to
                embedding model change in configuration).

        """
        if not self.embedding or not query_embedding:
            return 0.0

        # Validate dimension match - mismatched dimensions indicate embedding
        # model configuration change, which would produce incorrect results
        if len(self.embedding) != len(query_embedding):
            raise ValueError(
                f"Embedding dimension mismatch: chunk has {len(self.embedding)} dimensions, "
                f"query has {len(query_embedding)} dimensions. This usually indicates the "
                "embedding model was changed after documents were indexed. Re-index the "
                "knowledge base to fix this."
            )

        # Simple cosine similarity calculation
        import math

        dot_product = sum(a * b for a, b in zip(self.embedding, query_embedding, strict=False))
        magnitude_a = math.sqrt(sum(a * a for a in self.embedding))
        magnitude_b = math.sqrt(sum(b * b for b in query_embedding))

        if magnitude_a == 0 or magnitude_b == 0:
            return 0.0

        return dot_product / (magnitude_a * magnitude_b)

    @classmethod
    def create_from_text(
        cls,
        document_id: str,
        knowledge_base_id: str,
        chunk_index: int,
        content: str,
        start_char: int | None = None,
        end_char: int | None = None,
    ) -> "DocumentChunk":
        """Create a document chunk from text content."""
        return cls(
            document_id=document_id,
            knowledge_base_id=knowledge_base_id,
            chunk_index=chunk_index,
            content=content,
            char_count=len(content),
            word_count=len(content.split()) if content else 0,
            start_char=start_char,
            end_char=end_char,
        )


class DocumentQuery(BaseModel):
    """Synthesized query for a document (Shu RAG).

    Stores hypothetical queries that a document can answer. These are generated
    during document profiling and enable query-match retrieval, where user queries
    are matched against synthesized queries rather than document content.

    Query types include:
    - Interrogative: "What is the Q3 marketing budget?"
    - Imperative: "Show quarterly revenue breakdown"
    - Declarative: "Q3 marketing budget figures"
    """

    __tablename__ = "document_queries"

    # Foreign keys (knowledge_base_id denormalized for query efficiency)
    document_id = Column(
        String,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    knowledge_base_id = Column(
        String,
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Query content
    query_text = Column(Text, nullable=False)

    # Vector embedding for similarity search (384 dimensions for MiniLM)
    query_embedding = Column(Vector(384), nullable=True)

    # Relationships
    document = relationship("Document", back_populates="queries")
    knowledge_base = relationship("KnowledgeBase")

    def __repr__(self) -> str:
        """Represent as string."""
        if self.query_text:
            preview = self.query_text[:50] + "..." if len(self.query_text) > 50 else self.query_text
        else:
            preview = "<not set>"
        return f"<DocumentQuery(id={self.id}, doc_id='{self.document_id}', query='{preview}')>"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        base_dict = super().to_dict()
        base_dict.update(
            {
                "query_text": self.query_text,
                "has_embedding": self.query_embedding is not None,
            }
        )
        return base_dict

    @property
    def has_embedding(self) -> bool:
        """Check if query has an embedding."""
        return self.query_embedding is not None

    def set_embedding(self, embedding: list[float]) -> None:
        """Set the embedding vector for this query."""
        self.query_embedding = embedding

    @classmethod
    def create_for_document(
        cls,
        document_id: str,
        knowledge_base_id: str,
        query_text: str,
        query_embedding: list[float] | None = None,
    ) -> "DocumentQuery":
        """Create a synthesized query for a document."""
        return cls(
            document_id=document_id,
            knowledge_base_id=knowledge_base_id,
            query_text=query_text,
            query_embedding=query_embedding,
        )


# Entity type enum (SHU-355)
class ParticipantEntityType(str, Enum):
    """Entity types for document participants."""

    PERSON = "person"
    ORGANIZATION = "organization"
    EMAIL_ADDRESS = "email_address"


# Participant role enum (SHU-355)
class ParticipantRole(str, Enum):
    """Roles that entities can have in a document."""

    AUTHOR = "author"
    RECIPIENT = "recipient"
    MENTIONED = "mentioned"
    DECISION_MAKER = "decision_maker"
    SUBJECT = "subject"


# Backward-compatible constants (use enums for new code)
ENTITY_TYPE_PERSON = ParticipantEntityType.PERSON.value
ENTITY_TYPE_ORGANIZATION = ParticipantEntityType.ORGANIZATION.value
ENTITY_TYPE_EMAIL_ADDRESS = ParticipantEntityType.EMAIL_ADDRESS.value
ROLE_AUTHOR = ParticipantRole.AUTHOR.value
ROLE_RECIPIENT = ParticipantRole.RECIPIENT.value
ROLE_MENTIONED = ParticipantRole.MENTIONED.value
ROLE_DECISION_MAKER = ParticipantRole.DECISION_MAKER.value
ROLE_SUBJECT = ParticipantRole.SUBJECT.value


class DocumentParticipant(BaseModel):
    """Document participant entity (Shu RAG Relational Context).

    Tracks people, organizations, and email addresses mentioned in or
    associated with a document. Used for relational boost scoring.

    Entity types:
    - person: Named individual (may require resolution)
    - organization: Company, team, or group
    - email_address: Unique email identifier (no resolution needed)
    """

    __tablename__ = "document_participants"

    # Foreign keys (knowledge_base_id denormalized for query efficiency)
    document_id = Column(
        String,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    knowledge_base_id = Column(
        String,
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Entity identification
    entity_id = Column(String(36), nullable=True, index=True)  # For future resolution
    entity_type = Column(String(50), nullable=False)  # person, organization, email_address
    entity_name = Column(String(255), nullable=False, index=True)

    # Role in document
    role = Column(String(50), nullable=False)  # author, recipient, mentioned, decision_maker, subject

    # Extraction confidence
    confidence = Column(Float, nullable=True)

    # Relationships
    document = relationship("Document", back_populates="participants")
    knowledge_base = relationship("KnowledgeBase")

    def __repr__(self) -> str:
        """Represent as string."""
        return f"<DocumentParticipant(id={self.id}, entity='{self.entity_name}', role='{self.role}')>"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        base_dict = super().to_dict()
        base_dict.update(
            {
                "entity_id": self.entity_id,
                "entity_type": self.entity_type,
                "entity_name": self.entity_name,
                "role": self.role,
                "confidence": self.confidence,
            }
        )
        return base_dict

    @classmethod
    def create_for_document(
        cls,
        document_id: str,
        knowledge_base_id: str,
        entity_type: str,
        entity_name: str,
        role: str,
        confidence: float | None = None,
        entity_id: str | None = None,
    ) -> "DocumentParticipant":
        """Create a participant record for a document."""
        return cls(
            document_id=document_id,
            knowledge_base_id=knowledge_base_id,
            entity_id=entity_id,
            entity_type=entity_type,
            entity_name=entity_name,
            role=role,
            confidence=confidence,
        )


class DocumentProject(BaseModel):
    """Document project association (Shu RAG Relational Context).

    Tracks projects, initiatives, or topics associated with a document.
    Used for relational boost scoring based on user's active projects.
    """

    __tablename__ = "document_projects"

    # Foreign keys (knowledge_base_id denormalized for query efficiency)
    document_id = Column(
        String,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    knowledge_base_id = Column(
        String,
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Project identification
    project_name = Column(String(255), nullable=False, index=True)

    # How strongly the document relates to this project (0.0 - 1.0)
    association_strength = Column(Float, nullable=True)

    # Relationships
    document = relationship("Document", back_populates="projects")
    knowledge_base = relationship("KnowledgeBase")

    def __repr__(self) -> str:
        """Represent as string."""
        return f"<DocumentProject(id={self.id}, project='{self.project_name}')>"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        base_dict = super().to_dict()
        base_dict.update(
            {
                "project_name": self.project_name,
                "association_strength": self.association_strength,
            }
        )
        return base_dict

    @classmethod
    def create_for_document(
        cls,
        document_id: str,
        knowledge_base_id: str,
        project_name: str,
        association_strength: float | None = None,
    ) -> "DocumentProject":
        """Create a project association for a document."""
        return cls(
            document_id=document_id,
            knowledge_base_id=knowledge_base_id,
            project_name=project_name,
            association_strength=association_strength,
        )
