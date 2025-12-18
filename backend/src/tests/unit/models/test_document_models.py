"""
Unit tests for Document, DocumentChunk, DocumentQuery, DocumentParticipant, DocumentProject models.

These tests verify model instantiation, methods, and property behavior without database.
SHU-342, SHU-355
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from shu.models.document import (
    Document,
    DocumentChunk,
    DocumentQuery,
    DocumentParticipant,
    DocumentProject,
    ParticipantEntityType,
    ParticipantRole,
    CapabilityManifest,
    RelationalContext,
    ENTITY_TYPE_PERSON,
    ENTITY_TYPE_ORGANIZATION,
    ROLE_AUTHOR,
    ROLE_RECIPIENT,
)


class TestDocument:
    """Tests for Document model."""

    def test_document_instantiation(self):
        """Test basic document instantiation."""
        doc = Document()
        doc.id = "doc-123"
        doc.knowledge_base_id = "kb-456"
        doc.title = "Test Document"
        doc.content = "Test content"
        doc.source_type = "filesystem"
        doc.source_id = "file://test.txt"
        doc.file_type = "txt"

        assert doc.id == "doc-123"
        assert doc.title == "Test Document"
        # Note: Default values like profiling_status="pending" are only applied
        # when the object is persisted to the database. Without DB, it's None.

    def test_is_processed_property(self):
        """Test is_processed property."""
        doc = Document()
        doc.processing_status = "pending"
        assert doc.is_processed is False
        
        doc.processing_status = "processed"
        assert doc.is_processed is True

    def test_mark_processed(self):
        """Test mark_processed method."""
        doc = Document()
        doc.processing_status = "pending"
        doc.processing_error = "Previous error"
        
        doc.mark_processed()
        
        assert doc.processing_status == "processed"
        assert doc.processing_error is None
        assert doc.processed_at is not None

    def test_mark_error(self):
        """Test mark_error method."""
        doc = Document()
        doc.mark_error("Processing failed")
        
        assert doc.processing_status == "error"
        assert doc.processing_error == "Processing failed"
        assert doc.processed_at is not None

    def test_profiling_status_helpers(self):
        """Test profiling status helper methods and properties."""
        doc = Document()

        # Initial state (no DB, so default not applied)
        assert doc.profiling_status is None
        assert doc.is_profiled is False
        assert doc.profiling_failed is False

        # Mark in progress
        doc.mark_profiling_started()
        assert doc.profiling_status == "in_progress"

        # Mark complete
        doc.mark_profiling_complete(
            synopsis="Test synopsis",
            document_type="narrative",
            capability_manifest={"answers_questions_about": ["test"]},
            synopsis_embedding=[0.1, 0.2, 0.3],
        )
        assert doc.is_profiled is True
        assert doc.synopsis == "Test synopsis"
        assert doc.document_type == "narrative"
        assert doc.profiling_error is None

    def test_mark_profiling_failed(self):
        """Test mark_profiling_failed method with error message."""
        doc = Document()
        doc.mark_profiling_failed("LLM timeout")
        
        assert doc.profiling_status == "failed"
        assert doc.profiling_error == "LLM timeout"
        assert doc.profiling_failed is True

    def test_mark_profiling_failed_clears_on_complete(self):
        """Test that mark_profiling_complete clears previous errors."""
        doc = Document()
        doc.mark_profiling_failed("Previous error")
        
        doc.mark_profiling_complete(
            synopsis="New synopsis",
            document_type="technical",
            capability_manifest={},
        )
        
        assert doc.profiling_status == "complete"
        assert doc.profiling_error is None

    def test_to_dict_includes_profile_fields(self):
        """Test that to_dict includes all profile fields."""
        doc = Document()
        doc.id = "doc-123"
        doc.knowledge_base_id = "kb-456"
        doc.title = "Test"
        doc.content = "Content"
        doc.source_type = "test"
        doc.source_id = "test-id"
        doc.file_type = "txt"
        doc.chunk_count = 5
        doc.synopsis = "Test synopsis"
        doc.document_type = "narrative"
        doc.capability_manifest = {"test": "value"}
        doc.profiling_status = "complete"
        doc.profiling_error = None
        doc.relational_context = {"participant_count": 2}
        
        result = doc.to_dict()
        
        assert result["synopsis"] == "Test synopsis"
        assert result["document_type"] == "narrative"
        assert result["capability_manifest"] == {"test": "value"}
        assert result["profiling_status"] == "complete"
        assert result["profiling_error"] is None
        assert result["relational_context"] == {"participant_count": 2}
        assert result["has_synopsis_embedding"] is False

    def test_update_content_stats(self):
        """Test update_content_stats method."""
        doc = Document()
        doc.update_content_stats(word_count=100, character_count=500, chunk_count=5)

        assert doc.word_count == 100
        assert doc.character_count == 500
        assert doc.chunk_count == 5


class TestDocumentChunk:
    """Tests for DocumentChunk model."""

    def test_chunk_instantiation(self):
        """Test basic chunk instantiation."""
        chunk = DocumentChunk()
        chunk.id = "chunk-123"
        chunk.document_id = "doc-456"
        chunk.knowledge_base_id = "kb-789"
        chunk.chunk_index = 0
        chunk.content = "Test chunk content"
        chunk.char_count = len("Test chunk content")

        assert chunk.id == "chunk-123"
        assert chunk.chunk_index == 0
        assert chunk.has_embedding is False

    def test_create_from_text(self):
        """Test create_from_text class method."""
        chunk = DocumentChunk.create_from_text(
            document_id="doc-123",
            knowledge_base_id="kb-456",
            chunk_index=0,
            content="This is test content for the chunk.",
            start_char=0,
            end_char=35,
        )

        assert chunk.document_id == "doc-123"
        assert chunk.knowledge_base_id == "kb-456"
        assert chunk.chunk_index == 0
        assert chunk.char_count == 35
        assert chunk.word_count == 7
        assert chunk.start_char == 0
        assert chunk.end_char == 35

    def test_set_embedding(self):
        """Test set_embedding method."""
        chunk = DocumentChunk()
        chunk.content = "Test"
        chunk.char_count = 4

        embedding = [0.1] * 384
        chunk.set_embedding(embedding, "all-MiniLM-L6-v2")

        assert chunk.embedding == embedding
        assert chunk.embedding_model == "all-MiniLM-L6-v2"
        assert chunk.embedding_created_at is not None
        assert chunk.has_embedding is True

    def test_set_profile(self):
        """Test set_profile method for chunk profiling."""
        chunk = DocumentChunk()
        chunk.content = "Q3 budget was $2.5M approved by Sarah"
        chunk.char_count = len(chunk.content)

        chunk.set_profile(
            summary="Q3 budget approval record",
            keywords=["Q3", "$2.5M", "Sarah"],
            topics=["budget planning", "approvals"],
        )

        assert chunk.summary == "Q3 budget approval record"
        assert chunk.keywords == ["Q3", "$2.5M", "Sarah"]
        assert chunk.topics == ["budget planning", "approvals"]
        assert chunk.is_profiled is True

    def test_is_profiled_property(self):
        """Test is_profiled property."""
        chunk = DocumentChunk()
        chunk.content = "Test"
        chunk.char_count = 4

        assert chunk.is_profiled is False

        chunk.summary = "Test summary"
        assert chunk.is_profiled is True

    def test_get_preview(self):
        """Test get_preview method."""
        chunk = DocumentChunk()
        chunk.content = "A" * 200
        chunk.char_count = 200

        # Default max_chars=100
        preview = chunk.get_preview()
        assert len(preview) == 103  # 100 + "..."
        assert preview.endswith("...")

        # Short content
        chunk.content = "Short"
        chunk.char_count = 5
        assert chunk.get_preview() == "Short"

    def test_calculate_similarity_score(self):
        """Test calculate_similarity_score method."""
        chunk = DocumentChunk()
        chunk.content = "Test"
        chunk.char_count = 4

        # No embedding
        assert chunk.calculate_similarity_score([0.1] * 384) == 0.0

        # With embedding (unit vectors for easy verification)
        chunk.embedding = [1.0, 0.0, 0.0]
        query = [1.0, 0.0, 0.0]
        score = chunk.calculate_similarity_score(query)
        assert score == pytest.approx(1.0, rel=1e-6)  # Same vector = 1.0

        # Orthogonal vectors
        query = [0.0, 1.0, 0.0]
        score = chunk.calculate_similarity_score(query)
        assert score == pytest.approx(0.0, rel=1e-6)

    def test_calculate_similarity_score_dimension_mismatch(self):
        """Test that dimension mismatch raises ValueError."""
        chunk = DocumentChunk()
        chunk.content = "Test"
        chunk.char_count = 4
        chunk.embedding = [1.0, 0.0, 0.0]  # 3 dimensions

        # Query with different dimensions should raise
        query = [1.0, 0.0]  # 2 dimensions
        with pytest.raises(ValueError) as exc_info:
            chunk.calculate_similarity_score(query)

        assert "dimension mismatch" in str(exc_info.value).lower()
        assert "3" in str(exc_info.value)
        assert "2" in str(exc_info.value)

    def test_to_dict_includes_profile_fields(self):
        """Test that to_dict includes chunk profile fields."""
        chunk = DocumentChunk()
        chunk.id = "chunk-123"
        chunk.document_id = "doc-456"
        chunk.knowledge_base_id = "kb-789"
        chunk.chunk_index = 0
        chunk.content = "Test"
        chunk.char_count = 4
        chunk.summary = "Test summary"
        chunk.keywords = ["test"]
        chunk.topics = ["testing"]

        result = chunk.to_dict()

        assert result["summary"] == "Test summary"
        assert result["keywords"] == ["test"]
        assert result["topics"] == ["testing"]


class TestDocumentQuery:
    """Tests for DocumentQuery model (SHU-342)."""

    def test_query_instantiation(self):
        """Test basic query instantiation."""
        query = DocumentQuery()
        query.id = "query-123"
        query.document_id = "doc-456"
        query.knowledge_base_id = "kb-789"
        query.query_text = "What is the Q3 budget?"

        assert query.id == "query-123"
        assert query.query_text == "What is the Q3 budget?"
        assert query.has_embedding is False

    def test_create_for_document(self):
        """Test create_for_document class method."""
        query = DocumentQuery.create_for_document(
            document_id="doc-123",
            knowledge_base_id="kb-456",
            query_text="Show quarterly revenue breakdown",
            query_embedding=[0.1] * 384,
        )

        assert query.document_id == "doc-123"
        assert query.knowledge_base_id == "kb-456"
        assert query.query_text == "Show quarterly revenue breakdown"
        assert query.has_embedding is True

    def test_set_embedding(self):
        """Test set_embedding method."""
        query = DocumentQuery()
        query.query_text = "Test query"

        embedding = [0.1] * 384
        query.set_embedding(embedding)

        assert query.query_embedding == embedding
        assert query.has_embedding is True

    def test_to_dict(self):
        """Test to_dict method."""
        query = DocumentQuery()
        query.id = "query-123"
        query.document_id = "doc-456"
        query.knowledge_base_id = "kb-789"
        query.query_text = "Test query"
        query.query_embedding = [0.1] * 384

        result = query.to_dict()

        assert result["query_text"] == "Test query"
        assert result["has_embedding"] is True

    def test_repr(self):
        """Test __repr__ method truncates long queries."""
        query = DocumentQuery()
        query.id = "query-123"
        query.document_id = "doc-456"
        query.query_text = "A" * 100  # Long query

        repr_str = repr(query)
        assert "..." in repr_str
        assert len(repr_str) < 150  # Should be truncated


class TestDocumentParticipant:
    """Tests for DocumentParticipant model (SHU-355)."""

    def test_participant_instantiation(self):
        """Test basic participant instantiation."""
        participant = DocumentParticipant()
        participant.id = "part-123"
        participant.document_id = "doc-456"
        participant.knowledge_base_id = "kb-789"
        participant.entity_type = ParticipantEntityType.PERSON.value
        participant.entity_name = "Sarah Chen"
        participant.role = ParticipantRole.AUTHOR.value

        assert participant.entity_name == "Sarah Chen"
        assert participant.entity_type == "person"
        assert participant.role == "author"

    def test_create_for_document(self):
        """Test create_for_document class method."""
        participant = DocumentParticipant.create_for_document(
            document_id="doc-123",
            knowledge_base_id="kb-456",
            entity_type=ParticipantEntityType.PERSON.value,
            entity_name="John Smith",
            role=ParticipantRole.RECIPIENT.value,
            confidence=0.95,
            entity_id="entity-789",
        )

        assert participant.document_id == "doc-123"
        assert participant.entity_name == "John Smith"
        assert participant.entity_type == "person"
        assert participant.role == "recipient"
        assert participant.confidence == 0.95
        assert participant.entity_id == "entity-789"

    def test_backward_compatible_constants(self):
        """Test that string constants still work."""
        participant = DocumentParticipant()
        participant.entity_type = ENTITY_TYPE_PERSON
        participant.role = ROLE_AUTHOR

        assert participant.entity_type == "person"
        assert participant.role == "author"

        # Also verify enum values match constants
        assert ENTITY_TYPE_PERSON == ParticipantEntityType.PERSON.value
        assert ENTITY_TYPE_ORGANIZATION == ParticipantEntityType.ORGANIZATION.value
        assert ROLE_AUTHOR == ParticipantRole.AUTHOR.value
        assert ROLE_RECIPIENT == ParticipantRole.RECIPIENT.value

    def test_to_dict(self):
        """Test to_dict method."""
        participant = DocumentParticipant()
        participant.id = "part-123"
        participant.document_id = "doc-456"
        participant.knowledge_base_id = "kb-789"
        participant.entity_id = "entity-111"
        participant.entity_type = "organization"
        participant.entity_name = "Acme Corp"
        participant.role = "mentioned"
        participant.confidence = 0.8

        result = participant.to_dict()

        assert result["entity_id"] == "entity-111"
        assert result["entity_type"] == "organization"
        assert result["entity_name"] == "Acme Corp"
        assert result["role"] == "mentioned"
        assert result["confidence"] == 0.8


class TestDocumentProject:
    """Tests for DocumentProject model (SHU-355)."""

    def test_project_instantiation(self):
        """Test basic project instantiation."""
        project = DocumentProject()
        project.id = "proj-123"
        project.document_id = "doc-456"
        project.knowledge_base_id = "kb-789"
        project.project_name = "Q3 Marketing Campaign"
        project.association_strength = 0.9

        assert project.project_name == "Q3 Marketing Campaign"
        assert project.association_strength == 0.9

    def test_create_for_document(self):
        """Test create_for_document class method."""
        project = DocumentProject.create_for_document(
            document_id="doc-123",
            knowledge_base_id="kb-456",
            project_name="Product Launch",
            association_strength=0.85,
        )

        assert project.document_id == "doc-123"
        assert project.knowledge_base_id == "kb-456"
        assert project.project_name == "Product Launch"
        assert project.association_strength == 0.85

    def test_create_for_document_without_strength(self):
        """Test create_for_document without association_strength."""
        project = DocumentProject.create_for_document(
            document_id="doc-123",
            knowledge_base_id="kb-456",
            project_name="Internal Initiative",
        )

        assert project.project_name == "Internal Initiative"
        assert project.association_strength is None

    def test_to_dict(self):
        """Test to_dict method."""
        project = DocumentProject()
        project.id = "proj-123"
        project.document_id = "doc-456"
        project.knowledge_base_id = "kb-789"
        project.project_name = "Budget Review"
        project.association_strength = 0.75

        result = project.to_dict()

        assert result["project_name"] == "Budget Review"
        assert result["association_strength"] == 0.75


class TestEnumsAndTypedDicts:
    """Tests for enums and TypedDicts."""

    def test_participant_entity_type_enum(self):
        """Test ParticipantEntityType enum values."""
        assert ParticipantEntityType.PERSON.value == "person"
        assert ParticipantEntityType.ORGANIZATION.value == "organization"
        assert ParticipantEntityType.EMAIL_ADDRESS.value == "email_address"

        # Enum is string-based
        assert ParticipantEntityType.PERSON == "person"

    def test_participant_role_enum(self):
        """Test ParticipantRole enum values."""
        assert ParticipantRole.AUTHOR.value == "author"
        assert ParticipantRole.RECIPIENT.value == "recipient"
        assert ParticipantRole.MENTIONED.value == "mentioned"
        assert ParticipantRole.DECISION_MAKER.value == "decision_maker"
        assert ParticipantRole.SUBJECT.value == "subject"

    def test_capability_manifest_typed_dict(self):
        """Test CapabilityManifest TypedDict structure."""
        # TypedDict allows creating dictionaries with type hints
        manifest: CapabilityManifest = {
            "answers_questions_about": ["budget", "quarterly results"],
            "provides_information_type": ["facts", "decisions"],
            "authority_level": "primary",
            "completeness": "complete",
            "question_domains": ["who", "what", "when"],
        }

        assert manifest["answers_questions_about"] == ["budget", "quarterly results"]
        assert manifest["authority_level"] == "primary"

        # Partial manifest (total=False allows missing keys)
        partial_manifest: CapabilityManifest = {
            "answers_questions_about": ["test"],
        }
        assert "authority_level" not in partial_manifest

    def test_relational_context_typed_dict(self):
        """Test RelationalContext TypedDict structure."""
        context: RelationalContext = {
            "participant_count": 5,
            "primary_participants": ["Sarah Chen", "John Smith"],
            "project_associations": ["Q3 Budget", "Marketing Campaign"],
            "temporal_scope": {"start_date": "2024-01-01", "end_date": "2024-03-31"},
            "interaction_signals": ["meeting", "decision"],
        }

        assert context["participant_count"] == 5
        assert "Sarah Chen" in context["primary_participants"]

        # Partial context
        partial_context: RelationalContext = {
            "participant_count": 2,
        }
        assert partial_context["participant_count"] == 2

