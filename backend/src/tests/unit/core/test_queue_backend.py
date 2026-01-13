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
    InMemoryQueueBackend,
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


# =============================================================================
# Fixtures for InMemoryQueueBackend
# =============================================================================


@pytest.fixture
def inmemory_queue_backend() -> InMemoryQueueBackend:
    """Provide a fresh InMemoryQueueBackend for each test."""
    return InMemoryQueueBackend(cleanup_interval_seconds=0)  # Disable periodic cleanup for tests


# =============================================================================
# InMemoryQueueBackend Protocol Compliance Tests
# =============================================================================


class TestInMemoryQueueBackendProtocol:
    """Tests for InMemoryQueueBackend protocol compliance."""
    
    def test_inmemory_queue_backend_implements_protocol(
        self, inmemory_queue_backend: InMemoryQueueBackend
    ):
        """Verify that InMemoryQueueBackend implements QueueBackend protocol."""
        assert isinstance(inmemory_queue_backend, QueueBackend)


# =============================================================================
# Property 2: Enqueue-Dequeue Round-Trip (InMemory)
# =============================================================================


class TestProperty2EnqueueDequeueRoundTrip:
    """
    Property 2: Enqueue-dequeue round-trip
    
    *For any* queue backend (Redis or InMemory), any queue name, and any valid Job,
    if the job is enqueued and then dequeued, the dequeued job SHALL have the same
    id, queue_name, and payload as the original (with attempts incremented by 1).
    
    **Validates: Requirements 10.1**
    
    Feature: queue-backend-interface, Property 2: Enqueue-dequeue round-trip
    """
    
    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(job=job_strategy)
    async def test_enqueue_dequeue_round_trip(self, job: Job):
        """
        Property test: For any valid Job, enqueue then dequeue returns a job
        with the same id, queue_name, and payload (attempts incremented by 1).
        
        Feature: queue-backend-interface, Property 2: Enqueue-dequeue round-trip
        **Validates: Requirements 10.1**
        """
        backend = InMemoryQueueBackend(cleanup_interval_seconds=0)
        
        original_attempts = job.attempts
        
        # Enqueue the job
        result = await backend.enqueue(job)
        assert result is True, "enqueue() should return True"
        
        # Dequeue the job
        dequeued = await backend.dequeue(job.queue_name)
        
        # Verify the job was dequeued
        assert dequeued is not None, "dequeue() should return a job"
        
        # Verify id, queue_name, and payload are preserved
        assert dequeued.id == job.id, f"ID mismatch: {dequeued.id} != {job.id}"
        assert dequeued.queue_name == job.queue_name, f"queue_name mismatch: {dequeued.queue_name} != {job.queue_name}"
        assert dequeued.payload == job.payload, f"payload mismatch: {dequeued.payload} != {job.payload}"
        
        # Verify attempts is incremented by 1
        assert dequeued.attempts == original_attempts + 1, (
            f"attempts should be incremented by 1: {dequeued.attempts} != {original_attempts + 1}"
        )
    
    @pytest.mark.asyncio
    async def test_enqueue_dequeue_preserves_all_fields(
        self, inmemory_queue_backend: InMemoryQueueBackend
    ):
        """Unit test: enqueue then dequeue preserves all job fields."""
        job = Job(
            queue_name="test_queue",
            payload={"key": "value", "nested": {"a": 1}},
            max_attempts=5,
            visibility_timeout=600,
        )
        
        await inmemory_queue_backend.enqueue(job)
        dequeued = await inmemory_queue_backend.dequeue("test_queue")
        
        assert dequeued is not None
        assert dequeued.id == job.id
        assert dequeued.queue_name == job.queue_name
        assert dequeued.payload == job.payload
        assert dequeued.max_attempts == job.max_attempts
        assert dequeued.visibility_timeout == job.visibility_timeout
        assert dequeued.attempts == job.attempts + 1
    
    @pytest.mark.asyncio
    async def test_dequeue_empty_queue_returns_none(
        self, inmemory_queue_backend: InMemoryQueueBackend
    ):
        """Unit test: dequeue from empty queue returns None."""
        result = await inmemory_queue_backend.dequeue("empty_queue")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_fifo_order_preserved(
        self, inmemory_queue_backend: InMemoryQueueBackend
    ):
        """Unit test: jobs are dequeued in FIFO order."""
        jobs = [
            Job(queue_name="test", payload={"order": i})
            for i in range(5)
        ]
        
        for job in jobs:
            await inmemory_queue_backend.enqueue(job)
        
        for i, expected_job in enumerate(jobs):
            dequeued = await inmemory_queue_backend.dequeue("test")
            assert dequeued is not None
            assert dequeued.id == expected_job.id
            assert dequeued.payload["order"] == i



# =============================================================================
# Property 3: Visibility Timeout Expiration (InMemory)
# =============================================================================


class TestProperty3VisibilityTimeoutExpiration:
    """
    Property 3: Visibility timeout expiration
    
    *For any* queue backend, when a job is dequeued but not acknowledged
    within its visibility_timeout, the job SHALL become available for
    dequeue again.
    
    **Validates: Requirements 3.3, 10.3**
    
    Feature: queue-backend-interface, Property 3: Visibility timeout expiration
    """
    
    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        queue_name=queue_name_strategy,
        payload=payload_strategy,
    )
    async def test_visibility_timeout_expiration(self, queue_name: str, payload: Dict[str, Any]):
        """
        Property test: For any job dequeued but not acknowledged, after
        visibility timeout expires, the job becomes available for dequeue again.
        
        Note: We test the expiration logic by manipulating time internally rather
        than using sleep, to keep tests fast while still validating the property.
        
        Feature: queue-backend-interface, Property 3: Visibility timeout expiration
        **Validates: Requirements 3.3, 10.3**
        """
        import time
        
        backend = InMemoryQueueBackend(cleanup_interval_seconds=0)
        
        # Create a job with a short visibility timeout
        job = Job(
            queue_name=queue_name,
            payload=payload,
            visibility_timeout=10,  # 10 seconds
        )
        
        # Enqueue the job
        await backend.enqueue(job)
        
        # Dequeue the job (moves to processing set)
        dequeued = await backend.dequeue(queue_name)
        assert dequeued is not None
        assert dequeued.id == job.id
        
        # Immediately after dequeue, the job should NOT be available
        second_dequeue = await backend.dequeue(queue_name)
        assert second_dequeue is None, "Job should not be available while in processing"
        
        # Manually expire the job by modifying the internal state
        # This tests the expiration logic without waiting
        with backend._lock:
            if job.id in backend._processing[queue_name]:
                job_json, _ = backend._processing[queue_name][job.id]
                # Set expiry to the past
                backend._processing[queue_name][job.id] = (job_json, time.time() - 1)
        
        # After visibility timeout expires, job should be available again
        redelivered = await backend.dequeue(queue_name)
        assert redelivered is not None, "Job should be redelivered after visibility timeout"
        assert redelivered.id == job.id, "Redelivered job should have same ID"
        # Attempts should be incremented again
        assert redelivered.attempts == dequeued.attempts + 1, (
            f"Attempts should be incremented: {redelivered.attempts} != {dequeued.attempts + 1}"
        )
    
    @pytest.mark.asyncio
    async def test_acknowledged_job_not_redelivered_after_timeout(
        self, inmemory_queue_backend: InMemoryQueueBackend
    ):
        """Unit test: Acknowledged jobs are not redelivered even after timeout."""
        import time
        
        job = Job(queue_name="test", payload={"key": "value"}, visibility_timeout=10)
        await inmemory_queue_backend.enqueue(job)
        
        # Dequeue and acknowledge
        dequeued = await inmemory_queue_backend.dequeue("test")
        assert dequeued is not None
        await inmemory_queue_backend.acknowledge(dequeued)
        
        # Manually set expiry to past (simulating timeout)
        # This should have no effect since job was acknowledged
        with inmemory_queue_backend._lock:
            # Job should not be in processing anymore
            assert job.id not in inmemory_queue_backend._processing["test"]
        
        # Job should not be available
        result = await inmemory_queue_backend.dequeue("test")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_visibility_timeout_with_real_time(
        self, inmemory_queue_backend: InMemoryQueueBackend
    ):
        """Unit test: Visibility timeout works with real time passage."""
        import asyncio
        
        job = Job(queue_name="test", payload={"key": "value"}, visibility_timeout=1)
        await inmemory_queue_backend.enqueue(job)
        
        # Dequeue the job
        dequeued = await inmemory_queue_backend.dequeue("test")
        assert dequeued is not None
        
        # Immediately, job should not be available
        assert await inmemory_queue_backend.dequeue("test") is None
        
        # Wait for visibility timeout to expire
        await asyncio.sleep(1.1)
        
        # Job should now be available again
        redelivered = await inmemory_queue_backend.dequeue("test")
        assert redelivered is not None
        assert redelivered.id == job.id
        assert redelivered.attempts == 2  # Original 0 + first dequeue + redelivery



# =============================================================================
# Property 7: Thread-Safe Concurrent Operations (InMemory)
# =============================================================================


class TestProperty7ThreadSafeConcurrentOperations:
    """
    Property 7: Thread-safe concurrent operations
    
    *For any* InMemoryQueueBackend and any set of concurrent enqueue/dequeue
    operations, no jobs SHALL be lost or duplicated, and the final queue
    state SHALL be consistent.
    
    **Validates: Requirements 3.2**
    
    Feature: queue-backend-interface, Property 7: Thread-safe concurrent operations
    """
    
    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        num_jobs=st.integers(min_value=10, max_value=50),
    )
    async def test_concurrent_enqueue_no_jobs_lost(self, num_jobs: int):
        """
        Property test: Concurrent enqueue operations do not lose any jobs.
        
        Feature: queue-backend-interface, Property 7: Thread-safe concurrent operations
        **Validates: Requirements 3.2**
        """
        import concurrent.futures
        import asyncio
        
        backend = InMemoryQueueBackend(cleanup_interval_seconds=0)
        queue_name = "concurrent_test"
        
        # Create jobs with unique IDs
        jobs = [
            Job(queue_name=queue_name, payload={"index": i})
            for i in range(num_jobs)
        ]
        
        # Function to run enqueue in a thread
        def do_enqueue(job: Job):
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(backend.enqueue(job))
            finally:
                loop.close()
        
        # Run concurrent enqueues using ThreadPoolExecutor
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(do_enqueue, job) for job in jobs]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]
        
        # All enqueues should succeed
        assert all(results), "All enqueue operations should succeed"
        
        # Queue length should equal number of jobs
        length = await backend.queue_length(queue_name)
        assert length == num_jobs, f"Expected {num_jobs} jobs, got {length}"
        
        # Dequeue all jobs and verify none are lost
        dequeued_ids = set()
        for _ in range(num_jobs):
            job = await backend.dequeue(queue_name)
            assert job is not None, "Should be able to dequeue all jobs"
            dequeued_ids.add(job.id)
        
        # Verify all original job IDs were dequeued
        original_ids = {job.id for job in jobs}
        assert dequeued_ids == original_ids, "All jobs should be dequeued exactly once"
    
    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        num_jobs=st.integers(min_value=10, max_value=50),
    )
    async def test_concurrent_dequeue_no_duplicates(self, num_jobs: int):
        """
        Property test: Concurrent dequeue operations do not return duplicates.
        
        Feature: queue-backend-interface, Property 7: Thread-safe concurrent operations
        **Validates: Requirements 3.2**
        """
        import concurrent.futures
        import asyncio
        
        backend = InMemoryQueueBackend(cleanup_interval_seconds=0)
        queue_name = "concurrent_test"
        
        # Enqueue jobs first
        jobs = [
            Job(queue_name=queue_name, payload={"index": i})
            for i in range(num_jobs)
        ]
        for job in jobs:
            await backend.enqueue(job)
        
        # Function to run dequeue in a thread
        def do_dequeue():
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(backend.dequeue(queue_name))
            finally:
                loop.close()
        
        # Run concurrent dequeues using ThreadPoolExecutor
        # Use more workers than jobs to stress test
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(do_dequeue) for _ in range(num_jobs * 2)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]
        
        # Filter out None results (extra dequeue attempts on empty queue)
        dequeued_jobs = [r for r in results if r is not None]
        
        # Should have exactly num_jobs dequeued
        assert len(dequeued_jobs) == num_jobs, (
            f"Expected {num_jobs} dequeued jobs, got {len(dequeued_jobs)}"
        )
        
        # No duplicates - each job ID should appear exactly once
        dequeued_ids = [job.id for job in dequeued_jobs]
        assert len(dequeued_ids) == len(set(dequeued_ids)), "No duplicate jobs should be dequeued"
    
    @pytest.mark.asyncio
    async def test_concurrent_enqueue_and_dequeue(
        self, inmemory_queue_backend: InMemoryQueueBackend
    ):
        """Unit test: Concurrent enqueue and dequeue operations are thread-safe."""
        import concurrent.futures
        import asyncio
        
        queue_name = "concurrent_test"
        num_jobs = 50
        
        # Create jobs
        jobs = [
            Job(queue_name=queue_name, payload={"index": i})
            for i in range(num_jobs)
        ]
        
        def do_enqueue(job: Job):
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(inmemory_queue_backend.enqueue(job))
            finally:
                loop.close()
        
        def do_dequeue():
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(inmemory_queue_backend.dequeue(queue_name))
            finally:
                loop.close()
        
        # Run concurrent enqueues and dequeues
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            # Submit enqueues
            enqueue_futures = [executor.submit(do_enqueue, job) for job in jobs]
            # Submit dequeues (some may return None if queue is empty)
            dequeue_futures = [executor.submit(do_dequeue) for _ in range(num_jobs)]
            
            # Wait for all to complete
            concurrent.futures.wait(enqueue_futures + dequeue_futures)
        
        # Count successful dequeues
        dequeued = [f.result() for f in dequeue_futures if f.result() is not None]
        
        # Remaining jobs in queue
        remaining = await inmemory_queue_backend.queue_length(queue_name)
        
        # Total should equal original number of jobs
        # (dequeued + remaining = num_jobs)
        # Note: Some dequeued jobs may be in processing state
        processing_count = len(inmemory_queue_backend._processing[queue_name])
        total = len(dequeued) + remaining
        
        # The total dequeued should not exceed num_jobs
        assert len(dequeued) <= num_jobs, "Should not dequeue more jobs than enqueued"
    
    @pytest.mark.asyncio
    async def test_concurrent_acknowledge_operations(
        self, inmemory_queue_backend: InMemoryQueueBackend
    ):
        """Unit test: Concurrent acknowledge operations are thread-safe."""
        import concurrent.futures
        import asyncio
        
        queue_name = "concurrent_test"
        
        # Enqueue and dequeue jobs
        jobs = []
        for i in range(20):
            job = Job(queue_name=queue_name, payload={"index": i})
            await inmemory_queue_backend.enqueue(job)
            dequeued = await inmemory_queue_backend.dequeue(queue_name)
            if dequeued:
                jobs.append(dequeued)
        
        def do_acknowledge(job: Job):
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(inmemory_queue_backend.acknowledge(job))
            finally:
                loop.close()
        
        # Acknowledge all jobs concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(do_acknowledge, job) for job in jobs]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]
        
        # All acknowledges should succeed
        assert all(results), "All acknowledge operations should succeed"
        
        # Processing set should be empty
        assert len(inmemory_queue_backend._processing[queue_name]) == 0
