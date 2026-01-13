"""
Property-based tests for QueueBackend protocol.

These tests verify the correctness properties defined in the design document
for the unified queue interface.

Feature: queue-backend-interface
"""

import pytest
from hypothesis import given, strategies as st, settings
from datetime import datetime, timezone
from typing import Any, Dict

from shu.core.queue_backend import (
    Job,
    QueueBackend,
    QueueError,
    QueueConnectionError,
    QueueOperationError,
    JobSerializationError,
)


# =============================================================================
# Hypothesis Strategies for Job Generation
# =============================================================================


# Strategy for generating valid queue names
queue_name_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N"),  # Letters and numbers
        whitelist_characters=(":", "_", "-"),  # Common separators
    ),
    min_size=1,
    max_size=100,
)


# Strategy for generating valid job IDs (UUID-like strings)
job_id_strategy = st.uuids().map(str)


# Strategy for generating JSON-serializable payload values
json_value_strategy = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False, allow_infinity=False) | st.text(max_size=100),
    lambda children: st.lists(children, max_size=5) | st.dictionaries(st.text(min_size=1, max_size=20), children, max_size=5),
    max_leaves=10,
)


# Strategy for generating valid job payloads
payload_strategy = st.dictionaries(
    keys=st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters=("_",))),
    values=json_value_strategy,
    min_size=0,
    max_size=10,
)


# Strategy for generating valid attempts count
attempts_strategy = st.integers(min_value=0, max_value=100)


# Strategy for generating valid max_attempts
max_attempts_strategy = st.integers(min_value=1, max_value=100)


# Strategy for generating valid visibility_timeout
visibility_timeout_strategy = st.integers(min_value=1, max_value=86400)  # 1 second to 24 hours


# Strategy for generating complete Job objects
job_strategy = st.builds(
    Job,
    queue_name=queue_name_strategy,
    payload=payload_strategy,
    id=job_id_strategy,
    created_at=st.datetimes(
        min_value=datetime(2020, 1, 1),
        max_value=datetime(2030, 12, 31),
        timezones=st.just(timezone.utc),
    ),
    attempts=attempts_strategy,
    max_attempts=max_attempts_strategy,
    visibility_timeout=visibility_timeout_strategy,
)


# =============================================================================
# Property 1: Job Serialization Round-Trip
# =============================================================================


class TestProperty1JobSerializationRoundTrip:
    """
    Property 1: Job serialization round-trip
    
    *For any* valid Job object, serializing to JSON then deserializing
    SHALL produce an equivalent Job object with identical field values.
    
    **Validates: Requirements 11.3**
    
    Feature: queue-backend-interface, Property 1: Job serialization round-trip
    """
    
    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(job=job_strategy)
    async def test_job_serialization_round_trip(self, job: Job):
        """
        Property test: For any valid Job, to_json() then from_json() produces
        an equivalent Job.
        
        Feature: queue-backend-interface, Property 1: Job serialization round-trip
        **Validates: Requirements 11.3**
        """
        # Serialize to JSON
        json_str = job.to_json()
        
        # Deserialize back to Job
        restored_job = Job.from_json(json_str)
        
        # Verify all fields are equal
        assert restored_job.id == job.id, f"ID mismatch: {restored_job.id} != {job.id}"
        assert restored_job.queue_name == job.queue_name, f"queue_name mismatch: {restored_job.queue_name} != {job.queue_name}"
        assert restored_job.payload == job.payload, f"payload mismatch: {restored_job.payload} != {job.payload}"
        assert restored_job.created_at == job.created_at, f"created_at mismatch: {restored_job.created_at} != {job.created_at}"
        assert restored_job.attempts == job.attempts, f"attempts mismatch: {restored_job.attempts} != {job.attempts}"
        assert restored_job.max_attempts == job.max_attempts, f"max_attempts mismatch: {restored_job.max_attempts} != {job.max_attempts}"
        assert restored_job.visibility_timeout == job.visibility_timeout, f"visibility_timeout mismatch: {restored_job.visibility_timeout} != {job.visibility_timeout}"
    
    @pytest.mark.asyncio
    async def test_job_serialization_with_empty_payload(self):
        """Unit test: Job with empty payload serializes correctly."""
        job = Job(queue_name="test", payload={})
        json_str = job.to_json()
        restored = Job.from_json(json_str)
        
        assert restored.payload == {}
        assert restored.queue_name == "test"
    
    @pytest.mark.asyncio
    async def test_job_serialization_with_nested_payload(self):
        """Unit test: Job with nested payload serializes correctly."""
        payload = {
            "user": {"id": 123, "name": "test"},
            "items": [1, 2, 3],
            "metadata": {"nested": {"deep": True}},
        }
        job = Job(queue_name="test", payload=payload)
        json_str = job.to_json()
        restored = Job.from_json(json_str)
        
        assert restored.payload == payload
    
    @pytest.mark.asyncio
    async def test_job_default_values(self):
        """Unit test: Job uses correct default values."""
        job = Job(queue_name="test", payload={"key": "value"})
        
        # Check defaults
        assert job.attempts == 0
        assert job.max_attempts == 3
        assert job.visibility_timeout == 300
        assert job.id is not None
        assert job.created_at is not None
        
        # Verify round-trip preserves defaults
        restored = Job.from_json(job.to_json())
        assert restored.attempts == 0
        assert restored.max_attempts == 3
        assert restored.visibility_timeout == 300


# =============================================================================
# Job Deserialization Error Handling Tests
# =============================================================================


class TestJobDeserializationErrors:
    """Tests for Job deserialization error handling."""
    
    @pytest.mark.asyncio
    async def test_from_json_invalid_json(self):
        """Unit test: from_json raises JobSerializationError for invalid JSON."""
        with pytest.raises(JobSerializationError) as exc_info:
            Job.from_json("not valid json")
        
        assert "Failed to parse job JSON" in exc_info.value.message
    
    @pytest.mark.asyncio
    async def test_from_json_missing_required_field(self):
        """Unit test: from_json raises JobSerializationError for missing fields."""
        # Missing queue_name
        incomplete_json = '{"id": "123", "payload": {}}'
        
        with pytest.raises(JobSerializationError) as exc_info:
            Job.from_json(incomplete_json)
        
        assert "Missing required field" in exc_info.value.message
    
    @pytest.mark.asyncio
    async def test_from_json_invalid_datetime(self):
        """Unit test: from_json raises JobSerializationError for invalid datetime."""
        invalid_json = '{"id": "123", "queue_name": "test", "payload": {}, "created_at": "not-a-date"}'
        
        with pytest.raises(JobSerializationError) as exc_info:
            Job.from_json(invalid_json)
        
        assert "Invalid job data" in exc_info.value.message


# =============================================================================
# Exception Hierarchy Tests
# =============================================================================


class TestExceptionHierarchy:
    """Tests for the queue exception hierarchy."""
    
    def test_queue_connection_error_is_queue_error(self):
        """QueueConnectionError should inherit from QueueError."""
        error = QueueConnectionError("Connection failed")
        assert isinstance(error, QueueError)
        assert error.message == "Connection failed"
    
    def test_queue_operation_error_is_queue_error(self):
        """QueueOperationError should inherit from QueueError."""
        error = QueueOperationError("Operation failed")
        assert isinstance(error, QueueError)
        assert error.message == "Operation failed"
    
    def test_job_serialization_error_is_queue_error(self):
        """JobSerializationError should inherit from QueueError."""
        error = JobSerializationError("Serialization failed")
        assert isinstance(error, QueueError)
        assert error.message == "Serialization failed"
    
    def test_queue_error_with_details(self):
        """QueueError should support details dictionary."""
        error = QueueError(
            "Something went wrong",
            details={"key": "value", "code": 123}
        )
        assert error.message == "Something went wrong"
        assert error.details == {"key": "value", "code": 123}
    
    def test_queue_error_default_details(self):
        """QueueError should default to empty details."""
        error = QueueError("Error message")
        assert error.details == {}
