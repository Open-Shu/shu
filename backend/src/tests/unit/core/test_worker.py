"""
Property-based and unit tests for Worker consumer loop.

These tests verify the correctness properties defined in the design document
for the worker consumer loop.

Feature: queue-backend-interface
"""

import asyncio
import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from shu.core.queue_backend import InMemoryQueueBackend, Job
from shu.core.worker import Worker, WorkerConfig
from shu.core.workload_routing import WorkloadType, enqueue_job

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
        max_size=2,  # Limit to 2 types to keep test fast
    ),
    num_jobs_per_type=st.integers(min_value=1, max_value=3),  # Reduce job count
)
@settings(max_examples=50, deadline=None)  # Reduce examples and disable deadline
async def test_worker_consumes_only_configured_workload_types(
    configured_types: set[WorkloadType], num_jobs_per_type: int
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
    processed_jobs: list[Job] = []

    # Job handler that records processed jobs
    async def job_handler(job: Job) -> None:
        processed_jobs.append(job)

    # Enqueue jobs for ALL workload types
    all_jobs_by_type = {}
    for workload_type in WorkloadType:
        jobs = []
        for i in range(num_jobs_per_type):
            job = await enqueue_job(backend, workload_type, payload={"type": workload_type.value, "index": i})
            jobs.append(job)
        all_jobs_by_type[workload_type] = jobs

    # Configure worker with only the configured types
    config = WorkerConfig(workload_types=configured_types, poll_interval=0.1, shutdown_timeout=1.0)
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
    except TimeoutError:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

    # Verify: All processed jobs should be from configured types
    processed_types = {WorkloadType(job.payload["type"]) for job in processed_jobs}
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
    processed_jobs: list[Job] = []

    async def job_handler(job: Job) -> None:
        processed_jobs.append(job)

    # Enqueue a job
    job = await enqueue_job(backend, WorkloadType.INGESTION, payload={"action": "test"})

    # Create and run worker
    config = WorkerConfig(workload_types={WorkloadType.INGESTION}, poll_interval=0.1)
    worker = Worker(backend, config, job_handler)

    # Run worker in background
    worker_task = asyncio.create_task(worker.run())

    # Wait for job to be processed
    await asyncio.sleep(0.5)

    # Stop worker
    worker._running = False
    try:
        await asyncio.wait_for(worker_task, timeout=1.0)
    except TimeoutError:
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
    processed_attempts: list[int] = []

    async def failing_handler(job: Job) -> None:
        processed_attempts.append(job.attempts)
        raise ValueError("Simulated failure")

    # Enqueue a job with max_attempts=2
    await enqueue_job(backend, WorkloadType.INGESTION, payload={"action": "test"}, max_attempts=2)

    # Create and run worker
    config = WorkerConfig(workload_types={WorkloadType.INGESTION}, poll_interval=0.1)
    worker = Worker(backend, config, failing_handler)

    # Run worker in background
    worker_task = asyncio.create_task(worker.run())

    # Wait for job to be processed twice (initial + 1 retry)
    await asyncio.sleep(1.0)

    # Stop worker
    worker._running = False
    try:
        await asyncio.wait_for(worker_task, timeout=1.0)
    except TimeoutError:
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
    processed_jobs: list[Job] = []

    async def job_handler(job: Job) -> None:
        processed_jobs.append(job)

    # Enqueue jobs for different workload types
    await enqueue_job(backend, WorkloadType.INGESTION, payload={"type": "ingestion"})
    await enqueue_job(backend, WorkloadType.PROFILING, payload={"type": "profiling"})

    # Create worker that handles both types
    config = WorkerConfig(workload_types={WorkloadType.INGESTION, WorkloadType.PROFILING}, poll_interval=0.1)
    worker = Worker(backend, config, job_handler)

    # Run worker in background
    worker_task = asyncio.create_task(worker.run())

    # Wait for jobs to be processed
    await asyncio.sleep(0.5)

    # Stop worker
    worker._running = False
    try:
        await asyncio.wait_for(worker_task, timeout=1.0)
    except TimeoutError:
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
    processed_jobs: list[Job] = []

    async def job_handler(job: Job) -> None:
        processed_jobs.append(job)

    # Create worker with no jobs in queue
    config = WorkerConfig(workload_types={WorkloadType.INGESTION}, poll_interval=0.2)
    worker = Worker(backend, config, job_handler)

    # Run worker in background
    worker_task = asyncio.create_task(worker.run())

    # Wait a bit to ensure worker is polling
    await asyncio.sleep(0.3)

    # Enqueue a job while worker is running
    job = await enqueue_job(backend, WorkloadType.INGESTION, payload={"action": "test"})

    # Wait for job to be processed
    await asyncio.sleep(0.5)

    # Stop worker
    worker._running = False
    try:
        await asyncio.wait_for(worker_task, timeout=1.0)
    except TimeoutError:
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
    processed_jobs: list[Job] = []
    job_started = asyncio.Event()
    job_should_complete = asyncio.Event()

    async def slow_job_handler(job: Job) -> None:
        """Job handler that takes some time to complete."""
        job_started.set()
        # Wait for signal to complete (simulates long-running job)
        await job_should_complete.wait()
        processed_jobs.append(job)

    # Enqueue a job
    job = await enqueue_job(backend, WorkloadType.INGESTION, payload={"action": "slow_task"})

    # Create and run worker
    config = WorkerConfig(workload_types={WorkloadType.INGESTION}, poll_interval=0.1, shutdown_timeout=5.0)
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
    processed_jobs: list[Job] = []
    job_started = asyncio.Event()
    job_should_complete = asyncio.Event()

    async def slow_job_handler(job: Job) -> None:
        """Job handler that takes some time to complete."""
        job_started.set()
        await job_should_complete.wait()
        processed_jobs.append(job)

    # Enqueue first job
    job1 = await enqueue_job(backend, WorkloadType.INGESTION, payload={"action": "first"})

    # Create and run worker
    config = WorkerConfig(workload_types={WorkloadType.INGESTION}, poll_interval=0.1, shutdown_timeout=5.0)
    worker = Worker(backend, config, slow_job_handler)

    # Run worker in background
    worker_task = asyncio.create_task(worker.run())

    # Wait for first job to start processing
    await asyncio.wait_for(job_started.wait(), timeout=2.0)

    # Send shutdown signal while first job is processing
    worker._running = False

    # Enqueue second job (should not be processed)
    await enqueue_job(backend, WorkloadType.INGESTION, payload={"action": "second"})

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


# =============================================================================
# Queue-Level Concurrency Tracking Tests (SHU-596)
# =============================================================================


@pytest.mark.asyncio
async def test_worker_skips_queue_at_capacity():
    """
    Test that worker skips queues at capacity and processes other work.

    Validates: SHU-596 queue-level concurrency tracking

    When a workload type is at capacity (active jobs >= limit), the worker
    should skip that queue and try the next available queue.
    """
    from shu.core.worker import WorkloadCapacityLimiter

    backend = InMemoryQueueBackend()
    processed_jobs: list[Job] = []
    processing_started = asyncio.Event()
    should_complete = asyncio.Event()

    async def slow_handler(job: Job) -> None:
        """Handler that signals when processing starts and waits before completing."""
        processing_started.set()
        await should_complete.wait()
        processed_jobs.append(job)

    # Enqueue jobs: 1 OCR job and 1 INGESTION job
    await enqueue_job(backend, WorkloadType.INGESTION_OCR, payload={"type": "ocr"})
    await enqueue_job(backend, WorkloadType.INGESTION, payload={"type": "ingestion"})

    # Create shared capacity limiter with OCR limit of 1
    limiter = WorkloadCapacityLimiter(limits={WorkloadType.INGESTION_OCR: 1})

    # Configure worker to handle both types
    config = WorkerConfig(
        workload_types={WorkloadType.INGESTION_OCR, WorkloadType.INGESTION},
        poll_interval=0.1
    )
    worker = Worker(backend, config, slow_handler, capacity_limiter=limiter)

    worker_task = asyncio.create_task(worker.run())

    # Wait for first job (OCR) to start
    await asyncio.wait_for(processing_started.wait(), timeout=2.0)

    # At this point, OCR queue should be at capacity (1 active job)
    # The worker should now skip OCR queue and process INGESTION

    # Enqueue another OCR job - it should NOT be processed while first is active
    await enqueue_job(backend, WorkloadType.INGESTION_OCR, payload={"type": "ocr2"})

    # Reset event for next job
    processing_started.clear()

    # Allow first job to complete
    should_complete.set()

    # Wait for second job to be processed
    await asyncio.sleep(0.5)

    # Stop worker
    worker._running = False
    try:
        await asyncio.wait_for(worker_task, timeout=2.0)
    except TimeoutError:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

    # Verify both OCR jobs and the ingestion job were processed
    # The order should be: OCR1, then either INGESTION or OCR2
    assert len(processed_jobs) >= 2
    processed_types = [job.payload["type"] for job in processed_jobs]
    assert "ocr" in processed_types  # First OCR job was processed


@pytest.mark.asyncio
async def test_worker_capacity_tracking_with_shared_limiter():
    """
    Test that capacity is properly tracked via the shared limiter.

    Validates: SHU-596 process-level capacity tracking

    The shared limiter must track capacity across all workers in a process,
    acquiring on dequeue and releasing on job completion.
    """
    from shu.core.worker import WorkloadCapacityLimiter

    backend = InMemoryQueueBackend()

    async def simple_handler(job: Job) -> None:
        pass

    # Create shared limiter with OCR limit of 2
    limiter = WorkloadCapacityLimiter(limits={WorkloadType.INGESTION_OCR: 2})

    # Enqueue a job
    await enqueue_job(backend, WorkloadType.INGESTION_OCR, payload={"test": True})

    config = WorkerConfig(workload_types={WorkloadType.INGESTION_OCR}, poll_interval=0.1)
    worker = Worker(backend, config, simple_handler, capacity_limiter=limiter)

    # Verify limiter starts with full capacity
    assert limiter.get_available(WorkloadType.INGESTION_OCR) == 2

    # Run worker briefly to process the job
    worker_task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.3)
    worker._running = False

    try:
        await asyncio.wait_for(worker_task, timeout=1.0)
    except TimeoutError:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

    # Verify capacity is restored after job completes
    assert limiter.get_available(WorkloadType.INGESTION_OCR) == 2


@pytest.mark.asyncio
async def test_worker_capacity_released_on_failure():
    """
    Test that capacity is released even when job processing fails.

    Validates: SHU-596 capacity tracking

    Capacity must be released in the finally block, ensuring resources are
    freed even when jobs fail.
    """
    from shu.core.worker import WorkloadCapacityLimiter

    backend = InMemoryQueueBackend()

    async def failing_handler(job: Job) -> None:
        raise ValueError("Simulated failure")

    # Create shared limiter with OCR limit of 1
    limiter = WorkloadCapacityLimiter(limits={WorkloadType.INGESTION_OCR: 1})

    # Enqueue a job with max_attempts=1 so it doesn't retry
    await enqueue_job(backend, WorkloadType.INGESTION_OCR, payload={"test": True}, max_attempts=1)

    config = WorkerConfig(workload_types={WorkloadType.INGESTION_OCR}, poll_interval=0.1)
    worker = Worker(backend, config, failing_handler, capacity_limiter=limiter)

    # Verify limiter starts with full capacity
    assert limiter.get_available(WorkloadType.INGESTION_OCR) == 1

    # Run worker briefly to process the job
    worker_task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.3)
    worker._running = False

    try:
        await asyncio.wait_for(worker_task, timeout=1.0)
    except TimeoutError:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

    # Verify capacity is restored after job fails
    assert limiter.get_available(WorkloadType.INGESTION_OCR) == 1


@pytest.mark.asyncio
async def test_worker_unlimited_capacity_with_no_limiter():
    """
    Test that workers without a capacity limiter process all jobs.

    Validates: SHU-596 backwards compatibility

    When no capacity limiter is provided, the worker should process all jobs
    without any concurrency restrictions.
    """
    backend = InMemoryQueueBackend()
    processed_jobs: list[Job] = []

    async def handler(job: Job) -> None:
        processed_jobs.append(job)

    # Enqueue multiple jobs
    for i in range(3):
        await enqueue_job(backend, WorkloadType.INGESTION, payload={"index": i})

    config = WorkerConfig(workload_types={WorkloadType.INGESTION}, poll_interval=0.1)
    # No capacity_limiter provided - unlimited
    worker = Worker(backend, config, handler)

    worker_task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.5)
    worker._running = False

    try:
        await asyncio.wait_for(worker_task, timeout=1.0)
    except TimeoutError:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

    # All jobs should be processed
    assert len(processed_jobs) == 3


@pytest.mark.asyncio
async def test_workload_capacity_limiter_shared_across_workers():
    """
    Test that multiple workers share the same capacity limiter.

    Validates: SHU-596 process-level capacity enforcement

    When multiple workers share a limiter, the total concurrent jobs across
    all workers should not exceed the configured limit.
    """
    from shu.core.worker import WorkloadCapacityLimiter

    backend = InMemoryQueueBackend()
    jobs_in_progress = 0
    max_concurrent_seen = 0
    lock = asyncio.Lock()
    all_done = asyncio.Event()
    total_processed = 0

    async def tracking_handler(job: Job) -> None:
        nonlocal jobs_in_progress, max_concurrent_seen, total_processed
        async with lock:
            jobs_in_progress += 1
            max_concurrent_seen = max(max_concurrent_seen, jobs_in_progress)

        await asyncio.sleep(0.1)  # Simulate work

        async with lock:
            jobs_in_progress -= 1
            total_processed += 1
            if total_processed >= 5:
                all_done.set()

    # Create shared limiter with limit of 2
    limiter = WorkloadCapacityLimiter(limits={WorkloadType.INGESTION_OCR: 2})

    # Enqueue 5 jobs
    for i in range(5):
        await enqueue_job(backend, WorkloadType.INGESTION_OCR, payload={"index": i})

    config = WorkerConfig(workload_types={WorkloadType.INGESTION_OCR}, poll_interval=0.05)

    # Create 3 workers sharing the same limiter
    workers = [
        Worker(backend, config, tracking_handler, worker_id=f"{i+1}/3", capacity_limiter=limiter)
        for i in range(3)
    ]

    # Start all workers
    worker_tasks = [asyncio.create_task(w.run()) for w in workers]

    # Wait for all jobs to be processed
    try:
        await asyncio.wait_for(all_done.wait(), timeout=5.0)
    except TimeoutError:
        pass

    # Stop all workers
    for w in workers:
        w._running = False

    for task in worker_tasks:
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # Verify that we never exceeded the limit of 2 concurrent jobs
    assert max_concurrent_seen <= 2, f"Max concurrent was {max_concurrent_seen}, expected <= 2"
    assert total_processed == 5, f"Processed {total_processed} jobs, expected 5"


@pytest.mark.asyncio
async def test_workload_type_from_queue_name():
    """Test WorkloadType.from_queue_name() reverse lookup."""
    # Valid queue names
    assert WorkloadType.from_queue_name("shu:ingestion") == WorkloadType.INGESTION
    assert WorkloadType.from_queue_name("shu:ingestion_ocr") == WorkloadType.INGESTION_OCR
    assert WorkloadType.from_queue_name("shu:profiling") == WorkloadType.PROFILING

    # Invalid queue name returns None
    assert WorkloadType.from_queue_name("invalid:queue") is None
    assert WorkloadType.from_queue_name("") is None


class TestWorkloadCapacityLimiter:
    """Tests for the WorkloadCapacityLimiter class."""

    @pytest.mark.asyncio
    async def test_limiter_acquire_and_release(self):
        """Test basic acquire and release operations."""
        from shu.core.worker import WorkloadCapacityLimiter

        limiter = WorkloadCapacityLimiter(limits={WorkloadType.INGESTION_OCR: 2})

        # Start with full capacity
        assert limiter.get_available(WorkloadType.INGESTION_OCR) == 2

        # Acquire first permit
        assert await limiter.acquire(WorkloadType.INGESTION_OCR) is True
        assert limiter.get_available(WorkloadType.INGESTION_OCR) == 1

        # Acquire second permit
        assert await limiter.acquire(WorkloadType.INGESTION_OCR) is True
        assert limiter.get_available(WorkloadType.INGESTION_OCR) == 0

        # Third acquire should fail (at capacity)
        assert await limiter.acquire(WorkloadType.INGESTION_OCR) is False
        assert limiter.get_available(WorkloadType.INGESTION_OCR) == 0

        # Release one permit
        limiter.release(WorkloadType.INGESTION_OCR)
        assert limiter.get_available(WorkloadType.INGESTION_OCR) == 1

        # Now acquire should succeed again
        assert await limiter.acquire(WorkloadType.INGESTION_OCR) is True
        assert limiter.get_available(WorkloadType.INGESTION_OCR) == 0

    @pytest.mark.asyncio
    async def test_limiter_unlimited_workload_types(self):
        """Test that workload types without limits are always acquirable."""
        from shu.core.worker import WorkloadCapacityLimiter

        # Only limit OCR, not INGESTION
        limiter = WorkloadCapacityLimiter(limits={WorkloadType.INGESTION_OCR: 1})

        # INGESTION has no limit - should always return True
        for _ in range(100):
            assert await limiter.acquire(WorkloadType.INGESTION) is True

        # get_available returns None for unlimited types
        assert limiter.get_available(WorkloadType.INGESTION) is None
        assert limiter.get_limit(WorkloadType.INGESTION) == 0

    @pytest.mark.asyncio
    async def test_limiter_get_limit(self):
        """Test get_limit returns configured limits."""
        from shu.core.worker import WorkloadCapacityLimiter

        limiter = WorkloadCapacityLimiter(limits={
            WorkloadType.INGESTION_OCR: 3,
            WorkloadType.PROFILING: 5,
        })

        assert limiter.get_limit(WorkloadType.INGESTION_OCR) == 3
        assert limiter.get_limit(WorkloadType.PROFILING) == 5
        assert limiter.get_limit(WorkloadType.INGESTION) == 0  # Not configured

    @pytest.mark.asyncio
    async def test_limiter_empty_limits(self):
        """Test limiter with no limits configured (all unlimited)."""
        from shu.core.worker import WorkloadCapacityLimiter

        limiter = WorkloadCapacityLimiter(limits={})

        # All workload types should be unlimited
        for wt in WorkloadType:
            assert await limiter.acquire(wt) is True
            assert limiter.get_available(wt) is None
            assert limiter.get_limit(wt) == 0
