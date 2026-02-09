"""
Unit tests for job payload validation in worker handlers.

These tests verify that the OCR and Embedding handlers correctly validate
required payload fields and raise descriptive errors for missing fields.

Feature: queue-ingestion-pipeline
"""

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from shu.core.queue_backend import Job


class MockJob:
    """Mock job object for testing."""

    def __init__(
        self,
        payload: dict,
        job_id: str = "test-job-123",
        attempts: int = 1,
        max_attempts: int = 3,
        queue_name: str = "shu:ingestion_ocr",
    ):
        self.id = job_id
        self.payload = payload
        self.attempts = attempts
        self.max_attempts = max_attempts
        self.queue_name = queue_name


# Hypothesis strategies for generating valid payloads
ocr_payload_strategy = st.fixed_dictionaries(
    {
        "document_id": st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N", "P"))),
        "knowledge_base_id": st.text(
            min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N", "P"))
        ),
        "staging_key": st.text(min_size=1, max_size=100, alphabet=st.characters(whitelist_categories=("L", "N", "P"))),
        "filename": st.text(min_size=1, max_size=100, alphabet=st.characters(whitelist_categories=("L", "N", "P"))),
        "mime_type": st.sampled_from(
            ["application/pdf", "text/plain", "image/png", "image/jpeg", "application/msword"]
        ),
    },
    optional={
        "ocr_mode": st.sampled_from(["auto", "text_only", "ocr_only", None]),
        "source_id": st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N", "P"))),
    },
)

embed_payload_strategy = st.fixed_dictionaries(
    {
        "document_id": st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N", "P"))),
        "knowledge_base_id": st.text(
            min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N", "P"))
        ),
    },
    optional={
        "action": st.just("embed_document"),
    },
)


class TestPayloadRoundTrip:
    """Property-based tests for job payload serialization round-trip."""

    @given(payload=ocr_payload_strategy)
    @settings(max_examples=100)
    def test_ocr_payload_round_trip(self, payload: dict):
        """
        Property 12: Job Payload Round-Trip (OCR)

        For any valid OCR job payload, serializing to JSON then deserializing
        SHALL produce an equivalent payload.

        **Validates: Requirements 8.4**
        """
        # Create a job with the payload
        job = Job(queue_name="shu:ingestion_ocr", payload=payload)

        # Serialize to JSON
        json_str = job.to_json()

        # Deserialize back
        restored_job = Job.from_json(json_str)

        # Verify payload equivalence
        assert restored_job.payload == payload, f"Payload mismatch: {restored_job.payload} != {payload}"
        assert restored_job.queue_name == job.queue_name

    @given(payload=embed_payload_strategy)
    @settings(max_examples=100)
    def test_embed_payload_round_trip(self, payload: dict):
        """
        Property 12: Job Payload Round-Trip (Embed)

        For any valid Embed job payload, serializing to JSON then deserializing
        SHALL produce an equivalent payload.

        **Validates: Requirements 8.4**
        """
        # Create a job with the payload
        job = Job(queue_name="shu:ingestion_embed", payload=payload)

        # Serialize to JSON
        json_str = job.to_json()

        # Deserialize back
        restored_job = Job.from_json(json_str)

        # Verify payload equivalence
        assert restored_job.payload == payload, f"Payload mismatch: {restored_job.payload} != {payload}"
        assert restored_job.queue_name == job.queue_name

    @given(
        payload=st.fixed_dictionaries(
            {
                "document_id": st.text(
                    min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N", "P"))
                ),
                "knowledge_base_id": st.text(
                    min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N", "P"))
                ),
                "staging_key": st.text(
                    min_size=1, max_size=100, alphabet=st.characters(whitelist_categories=("L", "N", "P"))
                ),
                "filename": st.text(
                    min_size=1, max_size=100, alphabet=st.characters(whitelist_categories=("L", "N", "P"))
                ),
                "mime_type": st.text(
                    min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N", "P"))
                ),
                "nested_data": st.dictionaries(
                    keys=st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L", "N"))),
                    values=st.one_of(
                        st.text(min_size=0, max_size=50),
                        st.integers(min_value=-1000, max_value=1000),
                        st.booleans(),
                        st.none(),
                    ),
                    max_size=5,
                ),
            }
        )
    )
    @settings(max_examples=100)
    def test_complex_payload_round_trip(self, payload: dict):
        """
        Property 12: Job Payload Round-Trip (Complex)

        For any valid job payload with nested data, serializing to JSON then
        deserializing SHALL produce an equivalent payload.

        **Validates: Requirements 8.4**
        """
        # Create a job with the payload
        job = Job(queue_name="shu:ingestion_ocr", payload=payload)

        # Serialize to JSON
        json_str = job.to_json()

        # Deserialize back
        restored_job = Job.from_json(json_str)

        # Verify payload equivalence
        assert restored_job.payload == payload, f"Payload mismatch: {restored_job.payload} != {payload}"


class TestOCRPayloadValidation:
    """Unit tests for OCR handler payload validation errors."""

    @pytest.mark.asyncio
    async def test_missing_document_id_raises_validation_error(self):
        """
        Test that missing document_id raises a descriptive validation error.

        **Validates: Requirements 8.5**
        """
        job = MockJob(
            payload={
                "knowledge_base_id": "kb-123",
                "staging_key": "file_staging:doc-123",
                "filename": "test.pdf",
                "mime_type": "application/pdf",
            },
            queue_name="shu:ingestion_ocr",
        )

        from shu.worker import _handle_ocr_job

        with pytest.raises(ValueError) as exc_info:
            await _handle_ocr_job(job)

        assert "document_id" in str(exc_info.value).lower()
        assert "missing" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_missing_knowledge_base_id_raises_validation_error(self):
        """
        Test that missing knowledge_base_id raises a descriptive validation error.

        **Validates: Requirements 8.5**
        """
        job = MockJob(
            payload={
                "document_id": "doc-123",
                "staging_key": "file_staging:doc-123",
                "filename": "test.pdf",
                "mime_type": "application/pdf",
            },
            queue_name="shu:ingestion_ocr",
        )

        from shu.worker import _handle_ocr_job

        with pytest.raises(ValueError) as exc_info:
            await _handle_ocr_job(job)

        assert "knowledge_base_id" in str(exc_info.value).lower()
        assert "missing" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_missing_filename_raises_validation_error(self):
        """
        Test that missing filename raises a descriptive validation error.

        **Validates: Requirements 8.5**
        """
        job = MockJob(
            payload={
                "document_id": "doc-123",
                "knowledge_base_id": "kb-123",
                "staging_key": "file_staging:doc-123",
                "mime_type": "application/pdf",
            },
            queue_name="shu:ingestion_ocr",
        )

        from shu.worker import _handle_ocr_job

        with pytest.raises(ValueError) as exc_info:
            await _handle_ocr_job(job)

        assert "filename" in str(exc_info.value).lower()
        assert "missing" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_missing_mime_type_raises_validation_error(self):
        """
        Test that missing mime_type raises a descriptive validation error.

        **Validates: Requirements 8.5**
        """
        job = MockJob(
            payload={
                "document_id": "doc-123",
                "knowledge_base_id": "kb-123",
                "staging_key": "file_staging:doc-123",
                "filename": "test.pdf",
            },
            queue_name="shu:ingestion_ocr",
        )

        from shu.worker import _handle_ocr_job

        with pytest.raises(ValueError) as exc_info:
            await _handle_ocr_job(job)

        assert "mime_type" in str(exc_info.value).lower()
        assert "missing" in str(exc_info.value).lower()


class TestEmbedPayloadValidation:
    """Unit tests for Embedding handler payload validation errors."""

    @pytest.mark.asyncio
    async def test_missing_document_id_raises_validation_error(self):
        """
        Test that missing document_id raises a descriptive validation error.

        **Validates: Requirements 8.5**
        """
        job = MockJob(
            payload={
                "knowledge_base_id": "kb-123",
                "action": "embed_document",
            },
            queue_name="shu:ingestion_embed",
        )

        from shu.worker import _handle_embed_job

        with pytest.raises(ValueError) as exc_info:
            await _handle_embed_job(job)

        assert "document_id" in str(exc_info.value).lower()
        assert "missing" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_missing_knowledge_base_id_raises_validation_error(self):
        """
        Test that missing knowledge_base_id raises a descriptive validation error.

        **Validates: Requirements 8.5**
        """
        job = MockJob(
            payload={
                "document_id": "doc-123",
                "action": "embed_document",
            },
            queue_name="shu:ingestion_embed",
        )

        from shu.worker import _handle_embed_job

        with pytest.raises(ValueError) as exc_info:
            await _handle_embed_job(job)

        assert "knowledge_base_id" in str(exc_info.value).lower()
        assert "missing" in str(exc_info.value).lower()
