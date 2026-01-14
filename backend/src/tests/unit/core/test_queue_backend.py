"""
Property-based tests for QueueBackend protocol.

These tests verify the correctness properties defined in the design document
for the unified queue interface.

Feature: queue-backend-interface
"""

import pytest
from hypothesis import given, strategies as st, settings
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import asyncio
import time

from shu.core.queue_backend import (
    Job,
    QueueBackend,
    QueueError,
    QueueConnectionError,
    QueueOperationError,
    JobSerializationError,
    InMemoryQueueBackend,
    RedisQueueBackend,
)


# =============================================================================
# Mock Redis Client for Queue Operations
# =============================================================================


class MockRedisClientForQueue:
    """Mock Redis client for testing RedisQueueBackend.
    
    This mock implements the same interface as the real Redis client
    to allow testing RedisQueueBackend without a real Redis server.
    It simulates Redis lists, sorted sets, and key-value operations.
    """
    
    def __init__(self):
        # Lists: key -> list of values
        self._lists: Dict[str, list] = {}
        # Sorted sets: key -> {member: score}
        self._zsets: Dict[str, Dict[str, float]] = {}
        # Key-value store: key -> value
        self._data: Dict[str, str] = {}
        # Expiry times: key -> expiry_timestamp
        self._expiry: Dict[str, float] = {}
        # For blocking operations
        self._events: Dict[str, asyncio.Event] = {}
    
    def _check_expiry(self, key: str) -> bool:
        """Check if a key has expired and clean up if so. Returns True if expired."""
        if key in self._expiry and time.time() > self._expiry[key]:
            if key in self._data:
                del self._data[key]
            if key in self._expiry:
                del self._expiry[key]
            return True
        return False
    
    def _get_event(self, key: str) -> asyncio.Event:
        """Get or create an event for the key."""
        if key not in self._events:
            self._events[key] = asyncio.Event()
        return self._events[key]
    
    # List operations
    async def lpush(self, key: str, *values: str) -> int:
        """Push values to the left (head) of a list."""
        if key not in self._lists:
            self._lists[key] = []
        for value in values:
            self._lists[key].insert(0, value)
        # Signal any waiting brpop
        event = self._get_event(key)
        event.set()
        return len(self._lists[key])
    
    async def rpush(self, key: str, *values: str) -> int:
        """Push values to the right (tail) of a list."""
        if key not in self._lists:
            self._lists[key] = []
        for value in values:
            self._lists[key].append(value)
        # Signal any waiting brpop
        event = self._get_event(key)
        event.set()
        return len(self._lists[key])
    
    async def rpop(self, key: str) -> Optional[str]:
        """Pop a value from the right (tail) of a list."""
        if key not in self._lists or not self._lists[key]:
            return None
        return self._lists[key].pop()
    
    async def brpop(self, key: str, timeout: int = 0) -> Optional[tuple]:
        """Blocking pop from the right of a list."""
        # Try immediate pop first
        if key in self._lists and self._lists[key]:
            value = self._lists[key].pop()
            return (key, value)
        
        # If timeout is 0, we should block indefinitely, but for tests
        # we'll use a reasonable timeout to avoid hanging
        if timeout == 0:
            timeout = 30  # 30 seconds max for tests
        
        deadline = time.time() + timeout
        event = self._get_event(key)
        
        while time.time() < deadline:
            event.clear()
            if key in self._lists and self._lists[key]:
                value = self._lists[key].pop()
                return (key, value)
            
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            
            try:
                await asyncio.wait_for(event.wait(), timeout=min(remaining, 0.1))
            except asyncio.TimeoutError:
                pass
        
        return None
    
    async def lrange(self, key: str, start: int, end: int) -> list:
        """Get a range of elements from a list."""
        if key not in self._lists:
            return []
        lst = self._lists[key]
        # Handle negative indices
        if end == -1:
            end = len(lst)
        else:
            end = end + 1  # Redis end is inclusive
        if start < 0:
            start = max(0, len(lst) + start)
        return lst[start:end]
    
    async def llen(self, key: str) -> int:
        """Get the length of a list."""
        if key not in self._lists:
            return 0
        return len(self._lists[key])
    
    # Sorted set operations
    async def zadd(self, key: str, mapping: Dict[str, float]) -> int:
        """Add members to a sorted set with scores."""
        if key not in self._zsets:
            self._zsets[key] = {}
        added = 0
        for member, score in mapping.items():
            if member not in self._zsets[key]:
                added += 1
            self._zsets[key][member] = score
        return added
    
    async def zrem(self, key: str, *members: str) -> int:
        """Remove members from a sorted set."""
        if key not in self._zsets:
            return 0
        removed = 0
        for member in members:
            if member in self._zsets[key]:
                del self._zsets[key][member]
                removed += 1
        return removed
    
    async def zrangebyscore(
        self,
        key: str,
        min_score: str,
        max_score: float,
    ) -> list:
        """Get members with scores in a range."""
        if key not in self._zsets:
            return []
        
        min_val = float('-inf') if min_score == "-inf" else float(min_score)
        max_val = float('inf') if max_score == "+inf" else float(max_score)
        
        result = []
        for member, score in self._zsets[key].items():
            if min_val <= score <= max_val:
                result.append(member)
        return result
    
    # Key-value operations
    async def get(self, key: str) -> Optional[str]:
        """Get a value by key."""
        self._check_expiry(key)
        return self._data.get(key)
    
    async def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        """Set a key-value pair with optional expiration."""
        self._data[key] = value
        if ex:
            self._expiry[key] = time.time() + ex
        elif key in self._expiry:
            del self._expiry[key]
        return True
    
    async def delete(self, *keys: str) -> int:
        """Delete one or more keys."""
        deleted = 0
        for key in keys:
            if key in self._data:
                del self._data[key]
                deleted += 1
            if key in self._expiry:
                del self._expiry[key]
            if key in self._lists:
                del self._lists[key]
                deleted += 1
            if key in self._zsets:
                del self._zsets[key]
                deleted += 1
        return deleted


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
# Fixtures for RedisQueueBackend
# =============================================================================


@pytest.fixture
def redis_queue_backend() -> RedisQueueBackend:
    """Provide a fresh RedisQueueBackend with mock client for each test."""
    mock_client = MockRedisClientForQueue()
    return RedisQueueBackend(mock_client)


@pytest.fixture(params=["inmemory", "redis"])
def queue_backend(request) -> QueueBackend:
    """Parametrized fixture providing both backend implementations.
    
    This allows running the same tests against both InMemoryQueueBackend
    and RedisQueueBackend to verify backend substitutability.
    """
    if request.param == "inmemory":
        return InMemoryQueueBackend(cleanup_interval_seconds=0)
    else:
        mock_client = MockRedisClientForQueue()
        return RedisQueueBackend(mock_client)


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
# RedisQueueBackend Protocol Compliance Tests
# =============================================================================


class TestRedisQueueBackendProtocol:
    """Tests for RedisQueueBackend protocol compliance."""
    
    def test_redis_queue_backend_implements_protocol(
        self, redis_queue_backend: RedisQueueBackend
    ):
        """Verify that RedisQueueBackend implements QueueBackend protocol."""
        assert isinstance(redis_queue_backend, QueueBackend)


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
        
        # Jobs in processing state (dequeued but not yet acknowledged)
        processing_count = len(inmemory_queue_backend._processing[queue_name])
        
        # Total should equal original number of jobs
        # dequeued jobs are in processing state, so: processing + remaining = num_jobs
        assert processing_count + remaining == num_jobs, (
            f"Invariant violated: processing ({processing_count}) + remaining ({remaining}) "
            f"should equal num_jobs ({num_jobs})"
        )
        
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


# =============================================================================
# Parametrized Property Tests for Both Backends (Backend Substitutability)
# =============================================================================


class TestProperty2EnqueueDequeueRoundTripParametrized:
    """
    Property 2: Enqueue-dequeue round-trip (Parametrized for both backends)
    
    *For any* queue backend (Redis or InMemory), any queue name, and any valid Job,
    if the job is enqueued and then dequeued, the dequeued job SHALL have the same
    id, queue_name, and payload as the original (with attempts incremented by 1).
    
    **Validates: Requirements 2.5, 10.1**
    
    Feature: queue-backend-interface, Property 2: Enqueue-dequeue round-trip
    """
    
    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(job=job_strategy)
    async def test_enqueue_dequeue_round_trip_inmemory(self, job: Job):
        """
        Property test: For any valid Job with InMemoryQueueBackend, enqueue then dequeue
        returns a job with the same id, queue_name, and payload (attempts incremented by 1).
        
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
    @settings(max_examples=100)
    @given(job=job_strategy)
    async def test_enqueue_dequeue_round_trip_redis(self, job: Job):
        """
        Property test: For any valid Job with RedisQueueBackend, enqueue then dequeue
        returns a job with the same id, queue_name, and payload (attempts incremented by 1).
        
        Feature: queue-backend-interface, Property 2: Enqueue-dequeue round-trip
        **Validates: Requirements 2.5, 10.1**
        """
        mock_client = MockRedisClientForQueue()
        backend = RedisQueueBackend(mock_client)
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
    async def test_enqueue_dequeue_preserves_all_fields_inmemory(
        self, inmemory_queue_backend: InMemoryQueueBackend
    ):
        """Unit test: enqueue then dequeue preserves all job fields (InMemory)."""
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
    async def test_enqueue_dequeue_preserves_all_fields_redis(
        self, redis_queue_backend: RedisQueueBackend
    ):
        """Unit test: enqueue then dequeue preserves all job fields (Redis)."""
        job = Job(
            queue_name="test_queue",
            payload={"key": "value", "nested": {"a": 1}},
            max_attempts=5,
            visibility_timeout=600,
        )
        
        await redis_queue_backend.enqueue(job)
        dequeued = await redis_queue_backend.dequeue("test_queue")
        
        assert dequeued is not None
        assert dequeued.id == job.id
        assert dequeued.queue_name == job.queue_name
        assert dequeued.payload == job.payload
        assert dequeued.max_attempts == job.max_attempts
        assert dequeued.visibility_timeout == job.visibility_timeout
        assert dequeued.attempts == job.attempts + 1
    
    @pytest.mark.asyncio
    async def test_dequeue_empty_queue_returns_none_inmemory(
        self, inmemory_queue_backend: InMemoryQueueBackend
    ):
        """Unit test: dequeue from empty queue returns None (InMemory)."""
        result = await inmemory_queue_backend.dequeue("empty_queue_test")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_dequeue_empty_queue_returns_none_redis(
        self, redis_queue_backend: RedisQueueBackend
    ):
        """Unit test: dequeue from empty queue returns None (Redis)."""
        result = await redis_queue_backend.dequeue("empty_queue_test")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_fifo_order_preserved_inmemory(
        self, inmemory_queue_backend: InMemoryQueueBackend
    ):
        """Unit test: jobs are dequeued in FIFO order (InMemory)."""
        queue_name = "fifo_test_queue"
        jobs = [
            Job(queue_name=queue_name, payload={"order": i})
            for i in range(5)
        ]
        
        for job in jobs:
            await inmemory_queue_backend.enqueue(job)
        
        for i, expected_job in enumerate(jobs):
            dequeued = await inmemory_queue_backend.dequeue(queue_name)
            assert dequeued is not None
            assert dequeued.id == expected_job.id
            assert dequeued.payload["order"] == i
    
    @pytest.mark.asyncio
    async def test_fifo_order_preserved_redis(
        self, redis_queue_backend: RedisQueueBackend
    ):
        """Unit test: jobs are dequeued in FIFO order (Redis)."""
        queue_name = "fifo_test_queue"
        jobs = [
            Job(queue_name=queue_name, payload={"order": i})
            for i in range(5)
        ]
        
        for job in jobs:
            await redis_queue_backend.enqueue(job)
        
        for i, expected_job in enumerate(jobs):
            dequeued = await redis_queue_backend.dequeue(queue_name)
            assert dequeued is not None
            assert dequeued.id == expected_job.id
            assert dequeued.payload["order"] == i


class TestProperty3VisibilityTimeoutExpirationParametrized:
    """
    Property 3: Visibility timeout expiration (Parametrized for both backends)
    
    *For any* queue backend, when a job is dequeued but not acknowledged
    within its visibility_timeout, the job SHALL become available for
    dequeue again.
    
    **Validates: Requirements 2.5, 3.3, 10.3**
    
    Feature: queue-backend-interface, Property 3: Visibility timeout expiration
    """
    
    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        queue_name=queue_name_strategy,
        payload=payload_strategy,
    )
    async def test_visibility_timeout_expiration_inmemory(self, queue_name: str, payload: Dict[str, Any]):
        """
        Property test: For any job dequeued but not acknowledged with InMemoryQueueBackend,
        after visibility timeout expires, the job becomes available for dequeue again.
        
        Feature: queue-backend-interface, Property 3: Visibility timeout expiration
        **Validates: Requirements 3.3, 10.3**
        """
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
        with backend._lock:
            if job.id in backend._processing[queue_name]:
                job_json, _ = backend._processing[queue_name][job.id]
                backend._processing[queue_name][job.id] = (job_json, time.time() - 1)
        
        # After visibility timeout expires, job should be available again
        redelivered = await backend.dequeue(queue_name)
        assert redelivered is not None, "Job should be redelivered after visibility timeout"
        assert redelivered.id == job.id, "Redelivered job should have same ID"
        assert redelivered.attempts == dequeued.attempts + 1
    
    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        queue_name=queue_name_strategy,
        payload=payload_strategy,
    )
    async def test_visibility_timeout_expiration_redis(self, queue_name: str, payload: Dict[str, Any]):
        """
        Property test: For any job dequeued but not acknowledged with RedisQueueBackend,
        after visibility timeout expires, the job becomes available for dequeue again.
        
        Feature: queue-backend-interface, Property 3: Visibility timeout expiration
        **Validates: Requirements 2.5, 10.3**
        """
        mock_client = MockRedisClientForQueue()
        backend = RedisQueueBackend(mock_client)
        
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
        
        # Manually expire the job by modifying the processing set score
        processing_key = backend._processing_key(queue_name)
        await mock_client.zadd(processing_key, {job.id: time.time() - 1})
        
        # After visibility timeout expires, job should be available again
        redelivered = await backend.dequeue(queue_name)
        assert redelivered is not None, "Job should be redelivered after visibility timeout"
        assert redelivered.id == job.id, "Redelivered job should have same ID"
        assert redelivered.attempts == dequeued.attempts + 1
    
    @pytest.mark.asyncio
    async def test_visibility_timeout_with_real_time_inmemory(
        self, inmemory_queue_backend: InMemoryQueueBackend
    ):
        """Unit test: Visibility timeout works with real time passage (InMemory)."""
        queue_name = "visibility_test_queue"
        job = Job(queue_name=queue_name, payload={"key": "value"}, visibility_timeout=1)
        await inmemory_queue_backend.enqueue(job)
        
        # Dequeue the job
        dequeued = await inmemory_queue_backend.dequeue(queue_name)
        assert dequeued is not None
        
        # Immediately, job should not be available
        assert await inmemory_queue_backend.dequeue(queue_name) is None
        
        # Wait for visibility timeout to expire
        await asyncio.sleep(1.2)
        
        # Job should now be available again
        redelivered = await inmemory_queue_backend.dequeue(queue_name)
        assert redelivered is not None
        assert redelivered.id == job.id
        assert redelivered.attempts == 2  # Original 0 + first dequeue + redelivery
    
    @pytest.mark.asyncio
    async def test_visibility_timeout_with_real_time_redis(
        self, redis_queue_backend: RedisQueueBackend
    ):
        """Unit test: Visibility timeout works with real time passage (Redis)."""
        queue_name = "visibility_test_queue"
        job = Job(queue_name=queue_name, payload={"key": "value"}, visibility_timeout=1)
        await redis_queue_backend.enqueue(job)
        
        # Dequeue the job
        dequeued = await redis_queue_backend.dequeue(queue_name)
        assert dequeued is not None
        
        # Immediately, job should not be available
        assert await redis_queue_backend.dequeue(queue_name) is None
        
        # Wait for visibility timeout to expire
        await asyncio.sleep(1.2)
        
        # Job should now be available again
        redelivered = await redis_queue_backend.dequeue(queue_name)
        assert redelivered is not None
        assert redelivered.id == job.id
        assert redelivered.attempts == 2  # Original 0 + first dequeue + redelivery
    
    @pytest.mark.asyncio
    async def test_acknowledged_job_not_redelivered_inmemory(
        self, inmemory_queue_backend: InMemoryQueueBackend
    ):
        """Unit test: Acknowledged jobs are not redelivered even after timeout (InMemory)."""
        queue_name = "ack_test_queue"
        job = Job(queue_name=queue_name, payload={"key": "value"}, visibility_timeout=1)
        await inmemory_queue_backend.enqueue(job)
        
        # Dequeue and acknowledge
        dequeued = await inmemory_queue_backend.dequeue(queue_name)
        assert dequeued is not None
        await inmemory_queue_backend.acknowledge(dequeued)
        
        # Wait for visibility timeout to expire
        await asyncio.sleep(1.2)
        
        # Job should not be available (was acknowledged)
        result = await inmemory_queue_backend.dequeue(queue_name)
        assert result is None
    
    @pytest.mark.asyncio
    async def test_acknowledged_job_not_redelivered_redis(
        self, redis_queue_backend: RedisQueueBackend
    ):
        """Unit test: Acknowledged jobs are not redelivered even after timeout (Redis)."""
        queue_name = "ack_test_queue"
        job = Job(queue_name=queue_name, payload={"key": "value"}, visibility_timeout=1)
        await redis_queue_backend.enqueue(job)
        
        # Dequeue and acknowledge
        dequeued = await redis_queue_backend.dequeue(queue_name)
        assert dequeued is not None
        await redis_queue_backend.acknowledge(dequeued)
        
        # Wait for visibility timeout to expire
        await asyncio.sleep(1.2)
        
        # Job should not be available (was acknowledged)
        result = await redis_queue_backend.dequeue(queue_name)
        assert result is None


class TestRedisQueueBackendSpecific:
    """Tests specific to RedisQueueBackend implementation."""
    
    @pytest.mark.asyncio
    async def test_redis_enqueue_dequeue_basic(self, redis_queue_backend: RedisQueueBackend):
        """Unit test: Basic enqueue and dequeue with Redis backend."""
        job = Job(queue_name="test", payload={"key": "value"})
        
        result = await redis_queue_backend.enqueue(job)
        assert result is True
        
        dequeued = await redis_queue_backend.dequeue("test")
        assert dequeued is not None
        assert dequeued.id == job.id
        assert dequeued.payload == job.payload
        assert dequeued.attempts == 1
    
    @pytest.mark.asyncio
    async def test_redis_acknowledge(self, redis_queue_backend: RedisQueueBackend):
        """Unit test: Acknowledge removes job from processing."""
        job = Job(queue_name="test", payload={"key": "value"})
        await redis_queue_backend.enqueue(job)
        
        dequeued = await redis_queue_backend.dequeue("test")
        assert dequeued is not None
        
        result = await redis_queue_backend.acknowledge(dequeued)
        assert result is True
        
        # Acknowledging again should return False
        result = await redis_queue_backend.acknowledge(dequeued)
        assert result is False
    
    @pytest.mark.asyncio
    async def test_redis_reject_with_requeue(self, redis_queue_backend: RedisQueueBackend):
        """Unit test: Reject with requeue returns job to queue."""
        job = Job(queue_name="test", payload={"key": "value"}, max_attempts=3)
        await redis_queue_backend.enqueue(job)
        
        dequeued = await redis_queue_backend.dequeue("test")
        assert dequeued is not None
        assert dequeued.attempts == 1
        
        # Reject with requeue
        result = await redis_queue_backend.reject(dequeued, requeue=True)
        assert result is True
        
        # Job should be available again
        requeued = await redis_queue_backend.dequeue("test")
        assert requeued is not None
        assert requeued.id == job.id
        assert requeued.attempts == 2
    
    @pytest.mark.asyncio
    async def test_redis_reject_without_requeue(self, redis_queue_backend: RedisQueueBackend):
        """Unit test: Reject without requeue discards job."""
        job = Job(queue_name="test", payload={"key": "value"})
        await redis_queue_backend.enqueue(job)
        
        dequeued = await redis_queue_backend.dequeue("test")
        assert dequeued is not None
        
        # Reject without requeue
        result = await redis_queue_backend.reject(dequeued, requeue=False)
        assert result is True
        
        # Job should not be available
        result = await redis_queue_backend.dequeue("test")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_redis_peek(self, redis_queue_backend: RedisQueueBackend):
        """Unit test: Peek returns jobs without removing them."""
        jobs = [
            Job(queue_name="test", payload={"order": i})
            for i in range(5)
        ]
        
        for job in jobs:
            await redis_queue_backend.enqueue(job)
        
        # Peek should return jobs
        peeked = await redis_queue_backend.peek("test", limit=3)
        assert len(peeked) == 3
        
        # Queue length should still be 5
        length = await redis_queue_backend.queue_length("test")
        assert length == 5
    
    @pytest.mark.asyncio
    async def test_redis_queue_length(self, redis_queue_backend: RedisQueueBackend):
        """Unit test: Queue length returns correct count."""
        queue_name = "length_test"
        
        # Empty queue
        assert await redis_queue_backend.queue_length(queue_name) == 0
        
        # Add jobs
        for i in range(5):
            job = Job(queue_name=queue_name, payload={"index": i})
            await redis_queue_backend.enqueue(job)
        
        assert await redis_queue_backend.queue_length(queue_name) == 5
        
        # Dequeue one
        await redis_queue_backend.dequeue(queue_name)
        assert await redis_queue_backend.queue_length(queue_name) == 4
    
    @pytest.mark.asyncio
    async def test_redis_schedule(self, redis_queue_backend: RedisQueueBackend):
        """Unit test: Schedule adds job to scheduled set."""
        job = Job(queue_name="test", payload={"key": "value"})
        
        result = await redis_queue_backend.schedule(job, delay_seconds=1)
        assert result is True
        
        # Job should not be immediately available
        dequeued = await redis_queue_backend.dequeue("test")
        assert dequeued is None
        
        # Wait for delay
        await asyncio.sleep(1.1)
        
        # Now job should be available (after scheduled jobs are moved)
        dequeued = await redis_queue_backend.dequeue("test")
        assert dequeued is not None
        assert dequeued.id == job.id
    
    @pytest.mark.asyncio
    async def test_redis_schedule_invalid_delay(self, redis_queue_backend: RedisQueueBackend):
        """Unit test: Schedule with non-positive delay raises ValueError."""
        job = Job(queue_name="test", payload={"key": "value"})
        
        with pytest.raises(ValueError, match="delay_seconds must be positive"):
            await redis_queue_backend.schedule(job, delay_seconds=0)
        
        with pytest.raises(ValueError, match="delay_seconds must be positive"):
            await redis_queue_backend.schedule(job, delay_seconds=-1)


# =============================================================================
# Property 4: Factory Returns Singleton
# =============================================================================


# =============================================================================
# Property 8: Acknowledged Jobs Are Not Redelivered
# =============================================================================


class TestProperty8AcknowledgedJobsNotRedelivered:
    """
    Property 8: Acknowledged jobs are not redelivered
    
    *For any* queue backend, once a job is acknowledged, it SHALL NOT be
    returned by subsequent dequeue operations, even after visibility timeout
    would have expired.
    
    **Validates: Requirements 10.1**
    
    Feature: queue-backend-interface, Property 8: Acknowledged jobs are not redelivered
    """
    
    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(job=job_strategy)
    async def test_acknowledged_jobs_not_redelivered_inmemory(self, job: Job):
        """
        Property test: For any job with InMemoryQueueBackend, once acknowledged,
        it SHALL NOT be redelivered even after visibility timeout expires.
        
        Feature: queue-backend-interface, Property 8: Acknowledged jobs are not redelivered
        **Validates: Requirements 10.1**
        """
        backend = InMemoryQueueBackend(cleanup_interval_seconds=0)
        
        # Enqueue the job
        await backend.enqueue(job)
        
        # Dequeue the job
        dequeued = await backend.dequeue(job.queue_name)
        assert dequeued is not None
        
        # Acknowledge the job
        ack_result = await backend.acknowledge(dequeued)
        assert ack_result is True, "Acknowledgment should succeed"
        
        # Manually expire the visibility timeout (simulate time passing)
        # This should have no effect since the job was acknowledged
        with backend._lock:
            # Job should not be in processing anymore
            assert job.id not in backend._processing[job.queue_name], (
                "Acknowledged job should be removed from processing"
            )
        
        # Try to dequeue again - should get None
        redelivered = await backend.dequeue(job.queue_name)
        assert redelivered is None, (
            "Acknowledged job should NOT be redelivered"
        )
    
    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(job=job_strategy)
    async def test_acknowledged_jobs_not_redelivered_redis(self, job: Job):
        """
        Property test: For any job with RedisQueueBackend, once acknowledged,
        it SHALL NOT be redelivered even after visibility timeout expires.
        
        Feature: queue-backend-interface, Property 8: Acknowledged jobs are not redelivered
        **Validates: Requirements 10.1**
        """
        mock_client = MockRedisClientForQueue()
        backend = RedisQueueBackend(mock_client)
        
        # Enqueue the job
        await backend.enqueue(job)
        
        # Dequeue the job
        dequeued = await backend.dequeue(job.queue_name)
        assert dequeued is not None
        
        # Acknowledge the job
        ack_result = await backend.acknowledge(dequeued)
        assert ack_result is True, "Acknowledgment should succeed"
        
        # Manually expire the visibility timeout in the processing set
        # This should have no effect since the job was acknowledged
        processing_key = backend._processing_key(job.queue_name)
        members = await mock_client.zrangebyscore(processing_key, "-inf", "+inf")
        assert job.id not in members, (
            "Acknowledged job should be removed from processing set"
        )
        
        # Try to dequeue again - should get None
        redelivered = await backend.dequeue(job.queue_name)
        assert redelivered is None, (
            "Acknowledged job should NOT be redelivered"
        )
    
    @pytest.mark.asyncio
    async def test_acknowledged_job_not_redelivered_with_real_timeout_inmemory(
        self, inmemory_queue_backend: InMemoryQueueBackend
    ):
        """Unit test: Acknowledged job not redelivered even after real timeout (InMemory)."""
        job = Job(
            queue_name="ack_test",
            payload={"key": "value"},
            visibility_timeout=1,  # 1 second
        )
        
        await inmemory_queue_backend.enqueue(job)
        dequeued = await inmemory_queue_backend.dequeue("ack_test")
        assert dequeued is not None
        
        # Acknowledge the job
        await inmemory_queue_backend.acknowledge(dequeued)
        
        # Wait for visibility timeout to expire
        await asyncio.sleep(1.2)
        
        # Job should NOT be available
        result = await inmemory_queue_backend.dequeue("ack_test")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_acknowledged_job_not_redelivered_with_real_timeout_redis(
        self, redis_queue_backend: RedisQueueBackend
    ):
        """Unit test: Acknowledged job not redelivered even after real timeout (Redis)."""
        job = Job(
            queue_name="ack_test",
            payload={"key": "value"},
            visibility_timeout=1,  # 1 second
        )
        
        await redis_queue_backend.enqueue(job)
        dequeued = await redis_queue_backend.dequeue("ack_test")
        assert dequeued is not None
        
        # Acknowledge the job
        await redis_queue_backend.acknowledge(dequeued)
        
        # Wait for visibility timeout to expire
        await asyncio.sleep(1.2)
        
        # Job should NOT be available
        result = await redis_queue_backend.dequeue("ack_test")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_double_acknowledge_returns_false_inmemory(
        self, inmemory_queue_backend: InMemoryQueueBackend
    ):
        """Unit test: Acknowledging the same job twice returns False on second attempt (InMemory)."""
        job = Job(queue_name="test", payload={"key": "value"})
        
        await inmemory_queue_backend.enqueue(job)
        dequeued = await inmemory_queue_backend.dequeue("test")
        assert dequeued is not None
        
        # First acknowledge should succeed
        result1 = await inmemory_queue_backend.acknowledge(dequeued)
        assert result1 is True
        
        # Second acknowledge should return False
        result2 = await inmemory_queue_backend.acknowledge(dequeued)
        assert result2 is False
    
    @pytest.mark.asyncio
    async def test_double_acknowledge_returns_false_redis(
        self, redis_queue_backend: RedisQueueBackend
    ):
        """Unit test: Acknowledging the same job twice returns False on second attempt (Redis)."""
        job = Job(queue_name="test", payload={"key": "value"})
        
        await redis_queue_backend.enqueue(job)
        dequeued = await redis_queue_backend.dequeue("test")
        assert dequeued is not None
        
        # First acknowledge should succeed
        result1 = await redis_queue_backend.acknowledge(dequeued)
        assert result1 is True
        
        # Second acknowledge should return False
        result2 = await redis_queue_backend.acknowledge(dequeued)
        assert result2 is False


# =============================================================================
# Property 9: Rejected Jobs with Requeue Are Redelivered
# =============================================================================


class TestProperty9RejectedJobsWithRequeueRedelivered:
    """
    Property 9: Rejected jobs with requeue are redelivered
    
    *For any* queue backend, when a job is rejected with `requeue=True` and
    `attempts < max_attempts`, the job SHALL become available for dequeue again.
    
    **Validates: Requirements 10.1**
    
    Feature: queue-backend-interface, Property 9: Rejected jobs with requeue are redelivered
    """
    
    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        queue_name=queue_name_strategy,
        payload=payload_strategy,
        max_attempts=st.integers(min_value=2, max_value=10),
    )
    async def test_rejected_jobs_with_requeue_redelivered_inmemory(
        self, queue_name: str, payload: Dict[str, Any], max_attempts: int
    ):
        """
        Property test: For any job with InMemoryQueueBackend, when rejected with
        requeue=True and attempts < max_attempts, the job SHALL be redelivered.
        
        Feature: queue-backend-interface, Property 9: Rejected jobs with requeue are redelivered
        **Validates: Requirements 10.1**
        """
        backend = InMemoryQueueBackend(cleanup_interval_seconds=0)
        
        # Create a job with max_attempts > 1
        job = Job(
            queue_name=queue_name,
            payload=payload,
            max_attempts=max_attempts,
            attempts=0,
        )
        
        # Enqueue the job
        await backend.enqueue(job)
        
        # Dequeue the job
        dequeued = await backend.dequeue(queue_name)
        assert dequeued is not None
        assert dequeued.attempts == 1
        
        # Reject with requeue
        reject_result = await backend.reject(dequeued, requeue=True)
        assert reject_result is True, "Reject should succeed"
        
        # Job should be available for dequeue again
        redelivered = await backend.dequeue(queue_name)
        assert redelivered is not None, (
            "Rejected job with requeue=True should be redelivered"
        )
        assert redelivered.id == job.id, "Redelivered job should have same ID"
        assert redelivered.attempts == 2, (
            f"Redelivered job should have incremented attempts: {redelivered.attempts} != 2"
        )
    
    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        queue_name=queue_name_strategy,
        payload=payload_strategy,
        max_attempts=st.integers(min_value=2, max_value=10),
    )
    async def test_rejected_jobs_with_requeue_redelivered_redis(
        self, queue_name: str, payload: Dict[str, Any], max_attempts: int
    ):
        """
        Property test: For any job with RedisQueueBackend, when rejected with
        requeue=True and attempts < max_attempts, the job SHALL be redelivered.
        
        Feature: queue-backend-interface, Property 9: Rejected jobs with requeue are redelivered
        **Validates: Requirements 10.1**
        """
        mock_client = MockRedisClientForQueue()
        backend = RedisQueueBackend(mock_client)
        
        # Create a job with max_attempts > 1
        job = Job(
            queue_name=queue_name,
            payload=payload,
            max_attempts=max_attempts,
            attempts=0,
        )
        
        # Enqueue the job
        await backend.enqueue(job)
        
        # Dequeue the job
        dequeued = await backend.dequeue(queue_name)
        assert dequeued is not None
        assert dequeued.attempts == 1
        
        # Reject with requeue
        reject_result = await backend.reject(dequeued, requeue=True)
        assert reject_result is True, "Reject should succeed"
        
        # Job should be available for dequeue again
        redelivered = await backend.dequeue(queue_name)
        assert redelivered is not None, (
            "Rejected job with requeue=True should be redelivered"
        )
        assert redelivered.id == job.id, "Redelivered job should have same ID"
        assert redelivered.attempts == 2, (
            f"Redelivered job should have incremented attempts: {redelivered.attempts} != 2"
        )
    
    @pytest.mark.asyncio
    async def test_rejected_job_without_requeue_not_redelivered_inmemory(
        self, inmemory_queue_backend: InMemoryQueueBackend
    ):
        """Unit test: Rejected job with requeue=False is NOT redelivered (InMemory)."""
        job = Job(queue_name="test", payload={"key": "value"}, max_attempts=3)
        
        await inmemory_queue_backend.enqueue(job)
        dequeued = await inmemory_queue_backend.dequeue("test")
        assert dequeued is not None
        
        # Reject without requeue
        await inmemory_queue_backend.reject(dequeued, requeue=False)
        
        # Job should NOT be available
        result = await inmemory_queue_backend.dequeue("test")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_rejected_job_without_requeue_not_redelivered_redis(
        self, redis_queue_backend: RedisQueueBackend
    ):
        """Unit test: Rejected job with requeue=False is NOT redelivered (Redis)."""
        job = Job(queue_name="test", payload={"key": "value"}, max_attempts=3)
        
        await redis_queue_backend.enqueue(job)
        dequeued = await redis_queue_backend.dequeue("test")
        assert dequeued is not None
        
        # Reject without requeue
        await redis_queue_backend.reject(dequeued, requeue=False)
        
        # Job should NOT be available
        result = await redis_queue_backend.dequeue("test")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_rejected_job_at_max_attempts_not_redelivered_inmemory(
        self, inmemory_queue_backend: InMemoryQueueBackend
    ):
        """Unit test: Rejected job at max_attempts is NOT redelivered even with requeue=True (InMemory)."""
        job = Job(queue_name="test", payload={"key": "value"}, max_attempts=2, attempts=1)
        
        await inmemory_queue_backend.enqueue(job)
        dequeued = await inmemory_queue_backend.dequeue("test")
        assert dequeued is not None
        assert dequeued.attempts == 2  # Now at max_attempts
        
        # Reject with requeue, but job is at max_attempts
        await inmemory_queue_backend.reject(dequeued, requeue=True)
        
        # Job should NOT be available (exceeded max_attempts)
        result = await inmemory_queue_backend.dequeue("test")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_rejected_job_at_max_attempts_not_redelivered_redis(
        self, redis_queue_backend: RedisQueueBackend
    ):
        """Unit test: Rejected job at max_attempts is NOT redelivered even with requeue=True (Redis)."""
        job = Job(queue_name="test", payload={"key": "value"}, max_attempts=2, attempts=1)
        
        await redis_queue_backend.enqueue(job)
        dequeued = await redis_queue_backend.dequeue("test")
        assert dequeued is not None
        assert dequeued.attempts == 2  # Now at max_attempts
        
        # Reject with requeue, but job is at max_attempts
        await redis_queue_backend.reject(dequeued, requeue=True)
        
        # Job should NOT be available (exceeded max_attempts)
        result = await redis_queue_backend.dequeue("test")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_multiple_reject_requeue_cycles_inmemory(
        self, inmemory_queue_backend: InMemoryQueueBackend
    ):
        """Unit test: Job can be rejected and requeued multiple times until max_attempts (InMemory)."""
        job = Job(queue_name="test", payload={"key": "value"}, max_attempts=3)
        
        await inmemory_queue_backend.enqueue(job)
        
        # First attempt
        dequeued1 = await inmemory_queue_backend.dequeue("test")
        assert dequeued1 is not None
        assert dequeued1.attempts == 1
        await inmemory_queue_backend.reject(dequeued1, requeue=True)
        
        # Second attempt
        dequeued2 = await inmemory_queue_backend.dequeue("test")
        assert dequeued2 is not None
        assert dequeued2.attempts == 2
        await inmemory_queue_backend.reject(dequeued2, requeue=True)
        
        # Third attempt (at max_attempts)
        dequeued3 = await inmemory_queue_backend.dequeue("test")
        assert dequeued3 is not None
        assert dequeued3.attempts == 3
        await inmemory_queue_backend.reject(dequeued3, requeue=True)
        
        # Should not be redelivered (exceeded max_attempts)
        result = await inmemory_queue_backend.dequeue("test")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_multiple_reject_requeue_cycles_redis(
        self, redis_queue_backend: RedisQueueBackend
    ):
        """Unit test: Job can be rejected and requeued multiple times until max_attempts (Redis)."""
        job = Job(queue_name="test", payload={"key": "value"}, max_attempts=3)
        
        await redis_queue_backend.enqueue(job)
        
        # First attempt
        dequeued1 = await redis_queue_backend.dequeue("test")
        assert dequeued1 is not None
        assert dequeued1.attempts == 1
        await redis_queue_backend.reject(dequeued1, requeue=True)
        
        # Second attempt
        dequeued2 = await redis_queue_backend.dequeue("test")
        assert dequeued2 is not None
        assert dequeued2.attempts == 2
        await redis_queue_backend.reject(dequeued2, requeue=True)
        
        # Third attempt (at max_attempts)
        dequeued3 = await redis_queue_backend.dequeue("test")
        assert dequeued3 is not None
        assert dequeued3.attempts == 3
        await redis_queue_backend.reject(dequeued3, requeue=True)
        
        # Should not be redelivered (exceeded max_attempts)
        result = await redis_queue_backend.dequeue("test")
        assert result is None


# =============================================================================
# Property 4: Factory Returns Singleton
# =============================================================================


class TestProperty4FactoryReturnsSingleton:
    """
    Property 4: Factory returns singleton
    
    *For any* number of calls to `get_queue_backend()` within the same process,
    the same QueueBackend instance SHALL be returned.
    
    **Validates: Requirements 4.4**
    
    Feature: queue-backend-interface, Property 4: Factory returns singleton
    """
    
    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(num_calls=st.integers(min_value=2, max_value=20))
    async def test_factory_returns_singleton(self, num_calls: int):
        """
        Property test: Multiple calls to get_queue_backend() return the same instance.
        
        Feature: queue-backend-interface, Property 4: Factory returns singleton
        **Validates: Requirements 4.4**
        """
        from shu.core.queue_backend import get_queue_backend, reset_queue_backend
        
        # Reset to ensure clean state for each test case
        reset_queue_backend()
        
        try:
            # Make multiple calls to get_queue_backend
            backends = []
            for _ in range(num_calls):
                backend = await get_queue_backend()
                backends.append(backend)
            
            # All backends should be the same instance
            first_backend = backends[0]
            for i, backend in enumerate(backends[1:], start=2):
                assert backend is first_backend, (
                    f"Call {i} returned different instance than call 1"
                )
        finally:
            # Clean up
            reset_queue_backend()
    
    @pytest.mark.asyncio
    async def test_factory_returns_same_instance_across_calls(self):
        """Unit test: get_queue_backend() returns the same instance."""
        from shu.core.queue_backend import get_queue_backend, reset_queue_backend
        
        reset_queue_backend()
        
        try:
            backend1 = await get_queue_backend()
            backend2 = await get_queue_backend()
            backend3 = await get_queue_backend()
            
            assert backend1 is backend2
            assert backend2 is backend3
        finally:
            reset_queue_backend()
    
    @pytest.mark.asyncio
    async def test_reset_clears_singleton(self):
        """Unit test: reset_queue_backend() clears the singleton."""
        from shu.core.queue_backend import get_queue_backend, reset_queue_backend
        
        reset_queue_backend()
        
        try:
            backend1 = await get_queue_backend()
            reset_queue_backend()
            backend2 = await get_queue_backend()
            
            # After reset, a new instance should be created
            # Note: Both will be InMemoryQueueBackend in test environment,
            # but they should be different instances
            assert backend1 is not backend2
        finally:
            reset_queue_backend()


# =============================================================================
# Backend Selection Logic Tests
# =============================================================================


class TestBackendSelectionLogic:
    """
    Tests for backend selection logic in the factory.
    
    **Validates: Requirements 4.1, 4.2**
    """
    
    @pytest.mark.asyncio
    async def test_factory_returns_inmemory_when_no_redis_url(self):
        """Unit test: Factory returns InMemoryQueueBackend when no Redis URL configured."""
        from shu.core.queue_backend import (
            get_queue_backend, reset_queue_backend, InMemoryQueueBackend
        )
        from unittest.mock import patch, MagicMock
        
        reset_queue_backend()
        
        # Mock settings with no Redis URL
        mock_settings = MagicMock()
        mock_settings.redis_url = ""
        mock_settings.redis_required = False
        mock_settings.redis_fallback_enabled = True
        
        try:
            with patch('shu.core.config.get_settings_instance', return_value=mock_settings):
                backend = await get_queue_backend()
                assert isinstance(backend, InMemoryQueueBackend)
        finally:
            reset_queue_backend()
    
    @pytest.mark.asyncio
    async def test_factory_returns_inmemory_when_redis_unreachable_and_fallback_enabled(self):
        """Unit test: Factory returns InMemoryQueueBackend when Redis unreachable and fallback enabled."""
        from shu.core.queue_backend import (
            get_queue_backend, reset_queue_backend, InMemoryQueueBackend,
            QueueConnectionError
        )
        from unittest.mock import patch, MagicMock, AsyncMock
        
        reset_queue_backend()
        
        # Mock settings with Redis URL but unreachable
        mock_settings = MagicMock()
        mock_settings.redis_url = "redis://unreachable:6379"
        mock_settings.redis_required = False
        mock_settings.redis_fallback_enabled = True
        mock_settings.redis_connection_timeout = 1
        mock_settings.redis_socket_timeout = 1
        
        # Mock shared Redis client to fail
        async def mock_get_shared_redis_client_error():
            raise QueueConnectionError("Connection refused")
        
        try:
            with patch('shu.core.config.get_settings_instance', return_value=mock_settings):
                with patch('shu.core.queue_backend._get_shared_redis_client', mock_get_shared_redis_client_error):
                    backend = await get_queue_backend()
                    assert isinstance(backend, InMemoryQueueBackend)
        finally:
            reset_queue_backend()
    
    @pytest.mark.asyncio
    async def test_factory_raises_error_when_redis_required_but_unreachable(self):
        """Unit test: Factory raises error when Redis required but unreachable."""
        from shu.core.queue_backend import (
            get_queue_backend, reset_queue_backend, QueueConnectionError
        )
        from unittest.mock import patch, MagicMock
        
        reset_queue_backend()
        
        # Mock settings with Redis required
        mock_settings = MagicMock()
        mock_settings.redis_url = "redis://unreachable:6379"
        mock_settings.redis_required = True
        mock_settings.redis_fallback_enabled = True
        mock_settings.redis_connection_timeout = 1
        mock_settings.redis_socket_timeout = 1
        
        # Mock shared Redis client to fail
        async def mock_get_shared_redis_client_error():
            raise QueueConnectionError("Connection refused")
        
        try:
            with patch('shu.core.config.get_settings_instance', return_value=mock_settings):
                with patch('shu.core.queue_backend._get_shared_redis_client', mock_get_shared_redis_client_error):
                    with pytest.raises(QueueConnectionError):
                        await get_queue_backend()
        finally:
            reset_queue_backend()
    
    @pytest.mark.asyncio
    async def test_factory_raises_error_when_fallback_disabled_and_redis_unreachable(self):
        """Unit test: Factory raises error when fallback disabled and Redis unreachable."""
        from shu.core.queue_backend import (
            get_queue_backend, reset_queue_backend, QueueConnectionError
        )
        from unittest.mock import patch, MagicMock
        
        reset_queue_backend()
        
        # Mock settings with fallback disabled
        mock_settings = MagicMock()
        mock_settings.redis_url = "redis://unreachable:6379"
        mock_settings.redis_required = False
        mock_settings.redis_fallback_enabled = False
        mock_settings.redis_connection_timeout = 1
        mock_settings.redis_socket_timeout = 1
        
        # Mock shared Redis client to fail
        async def mock_get_shared_redis_client_error():
            raise QueueConnectionError("Connection refused")
        
        try:
            with patch('shu.core.config.get_settings_instance', return_value=mock_settings):
                with patch('shu.core.queue_backend._get_shared_redis_client', mock_get_shared_redis_client_error):
                    with pytest.raises(QueueConnectionError):
                        await get_queue_backend()
        finally:
            reset_queue_backend()
    
    @pytest.mark.asyncio
    async def test_factory_returns_redis_backend_when_redis_available(self):
        """Unit test: Factory returns RedisQueueBackend when Redis is available."""
        from shu.core.queue_backend import (
            get_queue_backend, reset_queue_backend, RedisQueueBackend
        )
        from unittest.mock import patch, MagicMock, AsyncMock
        
        reset_queue_backend()
        
        # Mock settings with a non-default Redis URL (to avoid the default URL check)
        mock_settings = MagicMock()
        mock_settings.redis_url = "redis://production-redis:6379"
        mock_settings.redis_required = False
        mock_settings.redis_fallback_enabled = True
        mock_settings.redis_connection_timeout = 5
        mock_settings.redis_socket_timeout = 5
        
        # Mock shared Redis client that works
        mock_redis_client = MagicMock()
        mock_redis_client.ping = AsyncMock(return_value=True)
        
        async def mock_get_shared_redis_client_success():
            return mock_redis_client
        
        try:
            with patch('shu.core.config.get_settings_instance', return_value=mock_settings):
                with patch('shu.core.queue_backend._get_shared_redis_client', mock_get_shared_redis_client_success):
                    backend = await get_queue_backend()
                    assert isinstance(backend, RedisQueueBackend)
        finally:
            reset_queue_backend()


# =============================================================================
# Dependency Injection Tests
# =============================================================================


class TestQueueBackendDependency:
    """Tests for the FastAPI dependency injection function."""
    
    def test_dependency_returns_cached_backend_if_available(self):
        """Unit test: get_queue_backend_dependency returns cached backend."""
        from shu.core.queue_backend import (
            get_queue_backend_dependency, reset_queue_backend,
            InMemoryQueueBackend, QueueBackend
        )
        import shu.core.queue_backend as queue_module
        
        reset_queue_backend()
        
        # Set up a cached backend
        cached_backend = InMemoryQueueBackend()
        queue_module._queue_backend = cached_backend
        
        try:
            # Dependency should return the cached backend
            result = get_queue_backend_dependency()
            assert result is cached_backend
        finally:
            reset_queue_backend()
    
    def test_dependency_returns_inmemory_if_no_cached_backend(self):
        """Unit test: get_queue_backend_dependency returns InMemoryQueueBackend if no cached backend."""
        from shu.core.queue_backend import (
            get_queue_backend_dependency, reset_queue_backend,
            InMemoryQueueBackend
        )
        
        reset_queue_backend()
        
        try:
            # No cached backend, should return InMemoryQueueBackend
            result = get_queue_backend_dependency()
            assert isinstance(result, InMemoryQueueBackend)
        finally:
            reset_queue_backend()
    
    def test_dependency_is_synchronous(self):
        """Unit test: get_queue_backend_dependency is synchronous (not async)."""
        from shu.core.queue_backend import get_queue_backend_dependency
        import asyncio
        
        # Should not be a coroutine
        result = get_queue_backend_dependency()
        assert not asyncio.iscoroutine(result)


# =============================================================================
# Initialize Queue Backend Tests
# =============================================================================


class TestInitializeQueueBackend:
    """Tests for the initialize_queue_backend function."""
    
    @pytest.mark.asyncio
    async def test_initialize_returns_backend(self):
        """Unit test: initialize_queue_backend returns a QueueBackend."""
        from shu.core.queue_backend import (
            initialize_queue_backend, reset_queue_backend, QueueBackend
        )
        
        reset_queue_backend()
        
        try:
            backend = await initialize_queue_backend()
            assert isinstance(backend, QueueBackend)
        finally:
            reset_queue_backend()
    
    @pytest.mark.asyncio
    async def test_initialize_sets_singleton(self):
        """Unit test: initialize_queue_backend sets the singleton."""
        from shu.core.queue_backend import (
            initialize_queue_backend, get_queue_backend, reset_queue_backend
        )
        
        reset_queue_backend()
        
        try:
            initialized = await initialize_queue_backend()
            subsequent = await get_queue_backend()
            
            assert initialized is subsequent
        finally:
            reset_queue_backend()
