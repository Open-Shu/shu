"""
Property-based and unit tests for Worker consumer loop.

These tests verify the correctness properties defined in the design document
for the worker consumer loop.

Feature: queue-backend-interface
"""

import pytest
import asyncio
import time
from hypothesis import given, strategies as st, settings
from typing import List, Set

from shu.core.worker import Worker, WorkerConfig
from shu.core.workload_routing import WorkloadType, enqueue_job
from shu.core.queue_backend import Job, InMemoryQueueBackend


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
async def backend():
    """Create an in-memory queue backend for testing."""
    return InMemoryQueueBackend()


# =============================================================================
# Property-Based Tests
# =============================================================================


@pytest.mark.asyncio
@given(
    configured_types=st.sets(
        st.sampled_from(list(WorkloadType)),
        min_size=1,
        max_size=2  # Limit to 2 types to keep test fast
    ),
    num_jobs_per_type=st.integers(min_value=1, max_value=3)  # Reduce job count
)
@settings(max_examples=50, deadline=None)  # Reduce examples and disable deadline
async def test_worker_consumes_only_configured_workload_types(
    configured_types: Set[WorkloadType],
    num_jobs_per_type: int
):
    """
    Property 6: Worker consumes only configured WorkloadTypes
    
    Feature: queue-backend-interface, Property 6: Worker consumes only configured WorkloadTypes
    Validates: Requirements 6.2
    
    For any Worker configured with a set of WorkloadTypes, the worker SHALL
    only dequeue jobs from queues corresponding to those WorkloadTypes.
    
    Test strategy:
    1. Create jobs for all WorkloadTypes
    2. Configure worker with a subset of WorkloadTypes
    3. Run worker for a short time
    4. Verify only jobs from configured types were processed
    5. Verify jobs from non-configured types remain in queues
    """
    backend = InMemoryQueueBackend()
    processed_jobs: List[Job] = []
    
    # Job handler that records processed jobs
    async def job_handler(job: Job) -> None:
        processed_jobs.append(job)
    
    # Enqueue jobs for ALL workload types
    all_jobs_by_type = {}
    for workload_type in WorkloadType:
        jobs = []
        for i in range(num_jobs_per_type):
            job = await enqueue_job(
                backend,
                workload_type,
                payload={"type": workload_type.value, "index": i}
            )
            jobs.append(job)
        all_jobs_by_type[workload_type] = jobs
    
    # Configure worker with only the configured types
    config = WorkerConfig(
        workload_types=configured_types,
        poll_interval=0.1,
        shutdown_timeout=1.0
    )
    worker = Worker(backend, config, job_handler)
    
    # Run worker for a short time (enough to process all configured jobs)
    # We'll run it in a task and cancel after a timeout
    worker_task = asyncio.create_task(worker.run())
    
    # Wait for jobs to be processed (with timeout)
    max_wait = 10.0  # seconds - increased for property-based testing
    start_time = time.time()
    expected_job_count = len(configured_types) * num_jobs_per_type
    
    while len(processed_jobs) < expected_job_count and time.time() - start_time < max_wait:
        await asyncio.sleep(0.05)  # Check more frequently
    
    # Stop the worker
    worker._running = False
    
    # Give worker a moment to finish current job
    await asyncio.sleep(0.2)
    
    try:
        await asyncio.wait_for(worker_task, timeout=2.0)
    except asyncio.TimeoutError:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
    
    # Verify: All processed jobs should be from configured types
    processed_types = {
        WorkloadType(job.payload["type"]) for job in processed_jobs
    }
    assert processed_types.issubset(configured_types), (
        f"Worker processed jobs from non-configured types. "
        f"Configured: {configured_types}, Processed: {processed_types}"
    )
    
    # Verify: All jobs from configured types should be processed
    assert len(processed_jobs) == expected_job_count, (
        f"Worker did not process all configured jobs. "
        f"Expected: {expected_job_count}, Processed: {len(processed_jobs)}"
    )
    
    # Verify: Jobs from non-configured types should remain in queues
    non_configured_types = set(WorkloadType) - configured_types
    for workload_type in non_configured_types:
        queue_length = await backend.queue_length(workload_type.queue_name)
        assert queue_length == num_jobs_per_type, (
            f"Jobs from non-configured type {workload_type.value} were processed. "
            f"Expected {num_jobs_per_type} jobs in queue, found {queue_length}"
        )


# =============================================================================
# Unit Tests
# =============================================================================


@pytest.mark.asyncio
async def test_worker_processes_jobs_successfully():
    """Test that worker successfully processes jobs and acknowledges them."""
    backend = InMemoryQueueBackend()
    processed_jobs: List[Job] = []
    
    async def job_handler(job: Job) -> None:
        processed_jobs.append(job)
    
    # Enqueue a job
    job = await enqueue_job(
        backend,
        WorkloadType.INGESTION,
        payload={"action": "test"}
    )
    
    # Create and run worker
    config = WorkerConfig(
        workload_types={WorkloadType.INGESTION},
        poll_interval=0.1
    )
    worker = Worker(backend, config, job_handler)
    
    # Run worker in background
    worker_task = asyncio.create_task(worker.run())
    
    # Wait for job to be processed
    await asyncio.sleep(0.5)
    
    # Stop worker
    worker._running = False
    try:
        await asyncio.wait_for(worker_task, timeout=1.0)
    except asyncio.TimeoutError:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
    
    # Verify job was processed
    assert len(processed_jobs) == 1
    assert processed_jobs[0].id == job.id
    assert processed_jobs[0].payload == {"action": "test"}


@pytest.mark.asyncio
async def test_worker_rejects_failed_jobs():
    """Test that worker rejects jobs that fail processing."""
    backend = InMemoryQueueBackend()
    processed_attempts: List[int] = []
    
    async def failing_handler(job: Job) -> None:
        processed_attempts.append(job.attempts)
        raise ValueError("Simulated failure")
    
    # Enqueue a job with max_attempts=2
    job = await enqueue_job(
        backend,
        WorkloadType.INGESTION,
        payload={"action": "test"},
        max_attempts=2
    )
    
    # Create and run worker
    config = WorkerConfig(
        workload_types={WorkloadType.INGESTION},
        poll_interval=0.1
    )
    worker = Worker(backend, config, failing_handler)
    
    # Run worker in background
    worker_task = asyncio.create_task(worker.run())
    
    # Wait for job to be processed twice (initial + 1 retry)
    await asyncio.sleep(1.0)
    
    # Stop worker
    worker._running = False
    try:
        await asyncio.wait_for(worker_task, timeout=1.0)
    except asyncio.TimeoutError:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
    
    # Verify job was attempted twice
    assert len(processed_attempts) == 2
    assert processed_attempts[0] == 1  # First attempt
    assert processed_attempts[1] == 2  # Second attempt (retry)
    
    # Verify job is no longer in queue (discarded after max attempts)
    queue_length = await backend.queue_length(WorkloadType.INGESTION.queue_name)
    assert queue_length == 0


@pytest.mark.asyncio
async def test_worker_handles_multiple_workload_types():
    """Test that worker can handle multiple workload types."""
    backend = InMemoryQueueBackend()
    processed_jobs: List[Job] = []
    
    async def job_handler(job: Job) -> None:
        processed_jobs.append(job)
    
    # Enqueue jobs for different workload types
    job1 = await enqueue_job(
        backend,
        WorkloadType.INGESTION,
        payload={"type": "ingestion"}
    )
    job2 = await enqueue_job(
        backend,
        WorkloadType.PROFILING,
        payload={"type": "profiling"}
    )
    
    # Create worker that handles both types
    config = WorkerConfig(
        workload_types={WorkloadType.INGESTION, WorkloadType.PROFILING},
        poll_interval=0.1
    )
    worker = Worker(backend, config, job_handler)
    
    # Run worker in background
    worker_task = asyncio.create_task(worker.run())
    
    # Wait for jobs to be processed
    await asyncio.sleep(0.5)
    
    # Stop worker
    worker._running = False
    try:
        await asyncio.wait_for(worker_task, timeout=1.0)
    except asyncio.TimeoutError:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
    
    # Verify both jobs were processed
    assert len(processed_jobs) == 2
    processed_payloads = {job.payload["type"] for job in processed_jobs}
    assert processed_payloads == {"ingestion", "profiling"}


@pytest.mark.asyncio
async def test_worker_waits_when_no_jobs_available():
    """Test that worker waits (polls) when no jobs are available."""
    backend = InMemoryQueueBackend()
    processed_jobs: List[Job] = []
    
    async def job_handler(job: Job) -> None:
        processed_jobs.append(job)
    
    # Create worker with no jobs in queue
    config = WorkerConfig(
        workload_types={WorkloadType.INGESTION},
        poll_interval=0.2
    )
    worker = Worker(backend, config, job_handler)
    
    # Run worker in background
    worker_task = asyncio.create_task(worker.run())
    
    # Wait a bit to ensure worker is polling
    await asyncio.sleep(0.3)
    
    # Enqueue a job while worker is running
    job = await enqueue_job(
        backend,
        WorkloadType.INGESTION,
        payload={"action": "test"}
    )
    
    # Wait for job to be processed
    await asyncio.sleep(0.5)
    
    # Stop worker
    worker._running = False
    try:
        await asyncio.wait_for(worker_task, timeout=1.0)
    except asyncio.TimeoutError:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
    
    # Verify job was processed
    assert len(processed_jobs) == 1
    assert processed_jobs[0].id == job.id



@pytest.mark.asyncio
async def test_worker_graceful_shutdown_finishes_current_job():
    """
    Test worker finishes current job on SIGTERM.
    
    Validates: Requirements 6.3
    
    When a worker receives SIGTERM, it should finish the current job
    before exiting (graceful shutdown).
    
    Test strategy:
    1. Start worker with a slow job handler
    2. Enqueue a job
    3. Wait for job to start processing
    4. Send shutdown signal (set _running = False)
    5. Verify job completes successfully
    """
    backend = InMemoryQueueBackend()
    processed_jobs: List[Job] = []
    job_started = asyncio.Event()
    job_should_complete = asyncio.Event()
    
    async def slow_job_handler(job: Job) -> None:
        """Job handler that takes some time to complete."""
        job_started.set()
        # Wait for signal to complete (simulates long-running job)
        await job_should_complete.wait()
        processed_jobs.append(job)
    
    # Enqueue a job
    job = await enqueue_job(
        backend,
        WorkloadType.INGESTION,
        payload={"action": "slow_task"}
    )
    
    # Create and run worker
    config = WorkerConfig(
        workload_types={WorkloadType.INGESTION},
        poll_interval=0.1,
        shutdown_timeout=5.0
    )
    worker = Worker(backend, config, slow_job_handler)
    
    # Run worker in background
    worker_task = asyncio.create_task(worker.run())
    
    # Wait for job to start processing
    await asyncio.wait_for(job_started.wait(), timeout=2.0)
    
    # Send shutdown signal while job is processing
    worker._running = False
    
    # Allow job to complete
    job_should_complete.set()
    
    # Wait for worker to finish
    await asyncio.wait_for(worker_task, timeout=2.0)
    
    # Verify job was completed
    assert len(processed_jobs) == 1
    assert processed_jobs[0].id == job.id
    
    # Verify job was acknowledged (not in queue)
    queue_length = await backend.queue_length(WorkloadType.INGESTION.queue_name)
    assert queue_length == 0


@pytest.mark.asyncio
async def test_worker_graceful_shutdown_no_new_jobs():
    """
    Test worker does not accept new jobs after shutdown signal.
    
    Validates: Requirements 6.3
    
    When a worker receives a shutdown signal, it should not accept
    new jobs, only finish the current one.
    
    Test strategy:
    1. Start worker
    2. Enqueue first job
    3. Wait for it to start processing
    4. Send shutdown signal
    5. Enqueue second job
    6. Verify only first job was processed
    """
    backend = InMemoryQueueBackend()
    processed_jobs: List[Job] = []
    job_started = asyncio.Event()
    job_should_complete = asyncio.Event()
    
    async def slow_job_handler(job: Job) -> None:
        """Job handler that takes some time to complete."""
        job_started.set()
        await job_should_complete.wait()
        processed_jobs.append(job)
    
    # Enqueue first job
    job1 = await enqueue_job(
        backend,
        WorkloadType.INGESTION,
        payload={"action": "first"}
    )
    
    # Create and run worker
    config = WorkerConfig(
        workload_types={WorkloadType.INGESTION},
        poll_interval=0.1,
        shutdown_timeout=5.0
    )
    worker = Worker(backend, config, slow_job_handler)
    
    # Run worker in background
    worker_task = asyncio.create_task(worker.run())
    
    # Wait for first job to start processing
    await asyncio.wait_for(job_started.wait(), timeout=2.0)
    
    # Send shutdown signal while first job is processing
    worker._running = False
    
    # Enqueue second job (should not be processed)
    job2 = await enqueue_job(
        backend,
        WorkloadType.INGESTION,
        payload={"action": "second"}
    )
    
    # Allow first job to complete
    job_should_complete.set()
    
    # Wait for worker to finish
    await asyncio.wait_for(worker_task, timeout=2.0)
    
    # Verify only first job was processed
    assert len(processed_jobs) == 1
    assert processed_jobs[0].id == job1.id
    
    # Verify second job is still in queue
    queue_length = await backend.queue_length(WorkloadType.INGESTION.queue_name)
    assert queue_length == 1
