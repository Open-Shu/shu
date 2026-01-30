"""
Property-based tests for WorkloadType routing.

These tests verify the correctness properties defined in the design document
for workload-based queue routing.

Feature: queue-backend-interface
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from shu.core.queue_backend import InMemoryQueueBackend
from shu.core.workload_routing import WorkloadType, enqueue_job

# =============================================================================
# Property 5: WorkloadType Routing Correctness
# =============================================================================


class TestProperty5WorkloadTypeRoutingCorrectness:
    """
    Property 5: WorkloadType routing correctness

    *For any* WorkloadType value, `workload_type.queue_name` SHALL return
    a consistent, non-empty queue name, and jobs enqueued via
    `enqueue_job(backend, workload_type, payload)` SHALL be placed in that queue.

    **Validates: Requirements 5.2, 5.3, 5.4**

    Feature: queue-backend-interface, Property 5: WorkloadType routing correctness
    """

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        workload_type=st.sampled_from(list(WorkloadType)),
        payload=st.dictionaries(
            keys=st.text(min_size=1, max_size=50),
            values=st.one_of(
                st.none(),
                st.booleans(),
                st.integers(),
                st.floats(allow_nan=False, allow_infinity=False),
                st.text(max_size=100),
            ),
            min_size=0,
            max_size=10,
        ),
    )
    async def test_workload_type_routing_correctness(
        self,
        workload_type: WorkloadType,
        payload: dict,
    ):
        """
        Property test: For any WorkloadType and payload, queue_name property returns
        a consistent queue name, and enqueue_job places the job in that queue.

        Feature: queue-backend-interface, Property 5: WorkloadType routing correctness
        **Validates: Requirements 5.2, 5.3, 5.4**
        """
        backend = InMemoryQueueBackend(cleanup_interval_seconds=0)

        # Get the queue name for this workload type
        queue_name = workload_type.queue_name

        # Verify queue name is non-empty
        assert queue_name, f"Queue name should be non-empty for {workload_type}"

        # Verify queue name is consistent (access again)
        queue_name_2 = workload_type.queue_name
        assert queue_name == queue_name_2, f"Queue name should be consistent: {queue_name} != {queue_name_2}"

        # Enqueue a job using the workload type
        job = await enqueue_job(backend, workload_type, payload)

        # Verify the job was created
        assert job is not None, "enqueue_job should return a Job"
        assert job.id is not None, "Job should have an ID"
        assert job.queue_name == queue_name, f"Job queue_name should match: {job.queue_name} != {queue_name}"
        assert job.payload == payload, f"Job payload should match: {job.payload} != {payload}"

        # Verify the job was placed in the correct queue
        dequeued = await backend.dequeue(queue_name)
        assert dequeued is not None, f"Job should be in queue {queue_name}"
        assert dequeued.id == job.id, f"Dequeued job ID should match: {dequeued.id} != {job.id}"
        assert dequeued.payload == payload, f"Dequeued job payload should match: {dequeued.payload} != {payload}"

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        workload_type_1=st.sampled_from(list(WorkloadType)),
        workload_type_2=st.sampled_from(list(WorkloadType)),
    )
    async def test_different_workload_types_use_different_queues(
        self,
        workload_type_1: WorkloadType,
        workload_type_2: WorkloadType,
    ):
        """
        Property test: Different WorkloadTypes map to different queue names
        (unless they are the same WorkloadType).

        Feature: queue-backend-interface, Property 5: WorkloadType routing correctness
        **Validates: Requirements 5.2, 5.3**
        """
        queue_name_1 = workload_type_1.queue_name
        queue_name_2 = workload_type_2.queue_name

        if workload_type_1 == workload_type_2:
            # Same workload type should map to same queue
            assert queue_name_1 == queue_name_2, (
                f"Same WorkloadType should map to same queue: " f"{queue_name_1} != {queue_name_2}"
            )
        else:
            # Different workload types should map to different queues
            assert queue_name_1 != queue_name_2, (
                f"Different WorkloadTypes should map to different queues: "
                f"{workload_type_1} -> {queue_name_1}, "
                f"{workload_type_2} -> {queue_name_2}"
            )


# =============================================================================
# Unit Tests for WorkloadType Routing
# =============================================================================


class TestWorkloadTypeRouting:
    """Unit tests for workload type routing functionality."""

    @pytest.mark.asyncio
    async def test_all_workload_types_have_queue_names(self):
        """Unit test: All WorkloadType enum values map to queue names."""
        for workload_type in WorkloadType:
            queue_name = workload_type.queue_name
            assert queue_name, f"WorkloadType {workload_type} should have a queue name"
            assert isinstance(queue_name, str), "Queue name should be a string"
            assert len(queue_name) > 0, "Queue name should not be empty"

    @pytest.mark.asyncio
    async def test_queue_names_are_namespaced(self):
        """Unit test: All queue names are prefixed with 'shu:'."""
        for workload_type in WorkloadType:
            queue_name = workload_type.queue_name
            assert queue_name.startswith("shu:"), f"Queue name should be namespaced with 'shu:': {queue_name}"

    @pytest.mark.asyncio
    async def test_ingestion_workload_type(self):
        """Unit test: INGESTION workload type maps to correct queue."""
        assert WorkloadType.INGESTION.queue_name == "shu:ingestion"

    @pytest.mark.asyncio
    async def test_llm_workflow_workload_type(self):
        """Unit test: LLM_WORKFLOW workload type maps to correct queue."""
        assert WorkloadType.LLM_WORKFLOW.queue_name == "shu:llm_workflow"

    @pytest.mark.asyncio
    async def test_maintenance_workload_type(self):
        """Unit test: MAINTENANCE workload type maps to correct queue."""
        assert WorkloadType.MAINTENANCE.queue_name == "shu:maintenance"

    @pytest.mark.asyncio
    async def test_profiling_workload_type(self):
        """Unit test: PROFILING workload type maps to correct queue."""
        assert WorkloadType.PROFILING.queue_name == "shu:profiling"

    @pytest.mark.asyncio
    async def test_enqueue_job_with_custom_job_kwargs(self):
        """Unit test: enqueue_job accepts custom Job constructor arguments."""
        backend = InMemoryQueueBackend(cleanup_interval_seconds=0)

        job = await enqueue_job(
            backend,
            WorkloadType.PROFILING,
            payload={"document_id": "doc123"},
            max_attempts=5,
            visibility_timeout=600,
        )

        assert job.max_attempts == 5
        assert job.visibility_timeout == 600
        assert job.payload == {"document_id": "doc123"}
        assert job.queue_name == "shu:profiling"

    @pytest.mark.asyncio
    async def test_enqueue_job_returns_job_with_id(self):
        """Unit test: enqueue_job returns a Job with generated ID."""
        backend = InMemoryQueueBackend(cleanup_interval_seconds=0)

        job = await enqueue_job(
            backend,
            WorkloadType.INGESTION,
            payload={"key": "value"},
        )

        assert job.id is not None
        assert isinstance(job.id, str)
        assert len(job.id) > 0

    @pytest.mark.asyncio
    async def test_enqueue_job_with_empty_payload(self):
        """Unit test: enqueue_job works with empty payload."""
        backend = InMemoryQueueBackend(cleanup_interval_seconds=0)

        job = await enqueue_job(
            backend,
            WorkloadType.MAINTENANCE,
            payload={},
        )

        assert job.payload == {}
        assert job.queue_name == "shu:maintenance"

        # Verify job can be dequeued
        dequeued = await backend.dequeue("shu:maintenance")
        assert dequeued is not None
        assert dequeued.id == job.id

    @pytest.mark.asyncio
    async def test_multiple_jobs_same_workload_type(self):
        """Unit test: Multiple jobs with same WorkloadType go to same queue."""
        backend = InMemoryQueueBackend(cleanup_interval_seconds=0)

        job1 = await enqueue_job(
            backend,
            WorkloadType.INGESTION,
            payload={"doc": "1"},
        )
        job2 = await enqueue_job(
            backend,
            WorkloadType.INGESTION,
            payload={"doc": "2"},
        )

        # Both jobs should be in the same queue
        assert job1.queue_name == job2.queue_name
        assert job1.queue_name == "shu:ingestion"

        # Verify both jobs can be dequeued from the same queue
        dequeued1 = await backend.dequeue("shu:ingestion")
        dequeued2 = await backend.dequeue("shu:ingestion")

        assert dequeued1 is not None
        assert dequeued2 is not None

        # Jobs should be dequeued in FIFO order
        assert dequeued1.id == job1.id
        assert dequeued2.id == job2.id

    @pytest.mark.asyncio
    async def test_jobs_different_workload_types_isolated(self):
        """Unit test: Jobs with different WorkloadTypes are isolated in separate queues."""
        backend = InMemoryQueueBackend(cleanup_interval_seconds=0)

        ingestion_job = await enqueue_job(
            backend,
            WorkloadType.INGESTION,
            payload={"type": "ingestion"},
        )
        profiling_job = await enqueue_job(
            backend,
            WorkloadType.PROFILING,
            payload={"type": "profiling"},
        )

        # Dequeue from ingestion queue
        dequeued_ingestion = await backend.dequeue("shu:ingestion")
        assert dequeued_ingestion is not None
        assert dequeued_ingestion.id == ingestion_job.id
        assert dequeued_ingestion.payload["type"] == "ingestion"

        # Dequeue from profiling queue
        dequeued_profiling = await backend.dequeue("shu:profiling")
        assert dequeued_profiling is not None
        assert dequeued_profiling.id == profiling_job.id
        assert dequeued_profiling.payload["type"] == "profiling"

        # Both queues should now be empty
        assert await backend.dequeue("shu:ingestion") is None
        assert await backend.dequeue("shu:profiling") is None
