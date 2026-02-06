"""
Property-based tests for ingestion service async pipeline.

These tests use Hypothesis to verify universal properties across all valid inputs.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st


class TestAPIImmediateReturnProperty:
    """
    Property 4: API Returns Immediately.

    For any document ingestion request, the API SHALL return within a bounded
    time (< 1 second) with document_id and status, regardless of file size
    or content type.

    **Validates: Requirements 2.2, 2.3, 2.4**
    """

    @given(
        file_size=st.integers(min_value=1, max_value=10 * 1024 * 1024),  # 1 byte to 10MB
    )
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,  # Disable deadline since we're measuring time ourselves
    )
    @pytest.mark.asyncio
    async def test_property_ingest_document_returns_immediately(self, file_size: int):
        """
        Feature: queue-ingestion-pipeline
        Property 4: API Returns Immediately

        **Validates: Requirements 2.2, 2.3, 2.4**

        This property verifies that ingest_document() returns within 1 second
        regardless of file size. The function should enqueue a job and return
        immediately without performing OCR or embedding.
        """
        from shu.models.document import DocumentStatus
        from shu.services.ingestion_service import ingest_document

        # Generate file bytes of the specified size
        file_bytes = b"x" * file_size

        # Create mock database session
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        # Create mock document
        mock_document = MagicMock()
        mock_document.id = "test-doc-123"
        mock_document.processing_status = DocumentStatus.PENDING.value
        mock_document.word_count = 0
        mock_document.character_count = 0
        mock_document.chunk_count = 0
        mock_document.source_hash = None
        mock_document.content_hash = None
        mock_document.update_status = MagicMock()

        # Mock the scalar_one result
        mock_result = MagicMock()
        mock_result.scalar_one = MagicMock(return_value=mock_document)
        mock_result.scalar_one_or_none = MagicMock(return_value=None)  # No existing document
        mock_db.execute.return_value = mock_result

        # Create mock document service
        mock_doc_service = MagicMock()
        mock_doc_service.get_document_by_source_id = AsyncMock(return_value=None)
        mock_doc_service.create_document = AsyncMock(return_value=mock_document)

        # Create mock cache backend
        mock_cache = AsyncMock()
        mock_cache.set_bytes = AsyncMock(return_value=True)

        # Create mock queue backend
        mock_queue = AsyncMock()
        mock_queue.enqueue = AsyncMock()

        # Create mock staging service
        mock_staging_service = MagicMock()
        mock_staging_service.stage_file = AsyncMock(return_value="file_staging:test-doc-123")

        # Patch at the module level where the imports happen
        with (
            patch("shu.services.ingestion_service.DocumentService", return_value=mock_doc_service),
            patch("shu.core.cache_backend.get_cache_backend", new=AsyncMock(return_value=mock_cache)),
            patch("shu.core.queue_backend.get_queue_backend", new=AsyncMock(return_value=mock_queue)),
            patch("shu.services.file_staging_service.FileStagingService", return_value=mock_staging_service),
        ):
            # Measure execution time
            start_time = time.monotonic()

            result = await ingest_document(
                db=mock_db,
                knowledge_base_id="test-kb-123",
                plugin_name="test_plugin",
                user_id="test-user-123",
                file_bytes=file_bytes,
                filename="test_file.pdf",
                mime_type="application/pdf",
                source_id="test-source-123",
            )

            end_time = time.monotonic()
            elapsed_time = end_time - start_time

            # Property assertion: response time < 1 second
            assert elapsed_time < 1.0, (
                f"ingest_document() took {elapsed_time:.3f}s for {file_size} bytes, "
                f"expected < 1.0s"
            )

            # Verify result structure
            assert "document_id" in result
            assert "status" in result
            assert result["status"] == DocumentStatus.PENDING.value
            assert result["skipped"] is False

    @given(
        content_length=st.integers(min_value=1, max_value=1 * 1024 * 1024),  # 1 byte to 1MB
    )
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @pytest.mark.asyncio
    async def test_property_ingest_text_returns_immediately(self, content_length: int):
        """
        Feature: queue-ingestion-pipeline
        Property 4: API Returns Immediately

        **Validates: Requirements 2.2, 2.3, 2.4**

        This property verifies that ingest_text() returns within 1 second
        regardless of content length. The function should enqueue an embed
        job and return immediately without performing embedding.
        """
        from shu.models.document import DocumentStatus
        from shu.services.ingestion_service import ingest_text

        # Generate content of the specified length
        content = "x" * content_length

        # Create mock database session
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        # Create mock document
        mock_document = MagicMock()
        mock_document.id = "test-doc-456"
        mock_document.processing_status = DocumentStatus.EMBEDDING.value
        mock_document.word_count = 0
        mock_document.character_count = 0
        mock_document.chunk_count = 0
        mock_document.source_hash = None
        mock_document.content_hash = None
        mock_document.update_status = MagicMock()

        # Mock the scalar_one result
        mock_result = MagicMock()
        mock_result.scalar_one = MagicMock(return_value=mock_document)
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.execute.return_value = mock_result

        # Create mock document service
        mock_doc_service = MagicMock()
        mock_doc_service.get_document_by_source_id = AsyncMock(return_value=None)
        mock_doc_service.create_document = AsyncMock(return_value=mock_document)

        # Create mock queue backend
        mock_queue = AsyncMock()
        mock_queue.enqueue = AsyncMock()

        # Patch at the module level where the imports happen
        with (
            patch("shu.services.ingestion_service.DocumentService", return_value=mock_doc_service),
            patch("shu.core.queue_backend.get_queue_backend", new=AsyncMock(return_value=mock_queue)),
        ):
            # Measure execution time
            start_time = time.monotonic()

            result = await ingest_text(
                db=mock_db,
                knowledge_base_id="test-kb-456",
                plugin_name="test_plugin",
                user_id="test-user-456",
                title="Test Document",
                content=content,
                source_id="test-source-456",
            )

            end_time = time.monotonic()
            elapsed_time = end_time - start_time

            # Property assertion: response time < 1 second
            assert elapsed_time < 1.0, (
                f"ingest_text() took {elapsed_time:.3f}s for {content_length} chars, "
                f"expected < 1.0s"
            )

            # Verify result structure
            assert "document_id" in result
            assert "status" in result
            assert result["status"] == DocumentStatus.EMBEDDING.value
            assert result["skipped"] is False

    @given(
        content_length=st.integers(min_value=1, max_value=1 * 1024 * 1024),  # 1 byte to 1MB
    )
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @pytest.mark.asyncio
    async def test_property_ingest_thread_returns_immediately(self, content_length: int):
        """
        Feature: queue-ingestion-pipeline
        Property 4: API Returns Immediately

        **Validates: Requirements 2.2, 2.3, 2.4**

        This property verifies that ingest_thread() returns within 1 second
        regardless of content length. The function should enqueue an embed
        job and return immediately without performing embedding.
        """
        from shu.models.document import DocumentStatus
        from shu.services.ingestion_service import ingest_thread

        # Generate content of the specified length
        content = "x" * content_length

        # Create mock database session
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        # Create mock document
        mock_document = MagicMock()
        mock_document.id = "test-doc-789"
        mock_document.processing_status = DocumentStatus.EMBEDDING.value
        mock_document.word_count = 0
        mock_document.character_count = 0
        mock_document.chunk_count = 0
        mock_document.source_hash = None
        mock_document.content_hash = None
        mock_document.update_status = MagicMock()

        # Mock the scalar_one result
        mock_result = MagicMock()
        mock_result.scalar_one = MagicMock(return_value=mock_document)
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.execute.return_value = mock_result

        # Create mock document service
        mock_doc_service = MagicMock()
        mock_doc_service.get_document_by_source_id = AsyncMock(return_value=None)
        mock_doc_service.create_document = AsyncMock(return_value=mock_document)

        # Create mock queue backend
        mock_queue = AsyncMock()
        mock_queue.enqueue = AsyncMock()

        # Patch at the module level where the imports happen
        with (
            patch("shu.services.ingestion_service.DocumentService", return_value=mock_doc_service),
            patch("shu.core.queue_backend.get_queue_backend", new=AsyncMock(return_value=mock_queue)),
        ):
            # Measure execution time
            start_time = time.monotonic()

            result = await ingest_thread(
                db=mock_db,
                knowledge_base_id="test-kb-789",
                plugin_name="test_plugin",
                user_id="test-user-789",
                title="Test Thread",
                content=content,
                thread_id="test-thread-789",
            )

            end_time = time.monotonic()
            elapsed_time = end_time - start_time

            # Property assertion: response time < 1 second
            assert elapsed_time < 1.0, (
                f"ingest_thread() took {elapsed_time:.3f}s for {content_length} chars, "
                f"expected < 1.0s"
            )

            # Verify result structure
            assert "document_id" in result
            assert "status" in result
            assert result["status"] == DocumentStatus.EMBEDDING.value
            assert result["skipped"] is False
