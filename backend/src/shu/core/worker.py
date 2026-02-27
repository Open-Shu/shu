"""Worker Consumer Loop for Queue Backend.

This module implements the worker consumer loop that processes jobs from queues
using the competing consumers pattern. Workers can be configured to consume
specific workload types and can run either in-process with the API or as
dedicated worker processes.

Example usage:
    from shu.core.worker import Worker, WorkerConfig, WorkloadCapacityLimiter
    from shu.core.workload_routing import WorkloadType
    from shu.core.queue_backend import get_queue_backend

    # Create a process-shared capacity limiter (once per process)
    limiter = WorkloadCapacityLimiter.from_settings()

    # Configure worker to consume ingestion and profiling jobs
    config = WorkerConfig(
        workload_types={WorkloadType.INGESTION, WorkloadType.PROFILING},
        poll_interval=1.0,
        shutdown_timeout=30.0
    )

    # Create workers sharing the same limiter
    backend = await get_queue_backend()
    worker1 = Worker(backend, config, job_handler=process_job, capacity_limiter=limiter)
    worker2 = Worker(backend, config, job_handler=process_job, capacity_limiter=limiter)

    # Run worker loops (blocks until shutdown signal)
    await asyncio.gather(worker1.run(), worker2.run())
"""

import asyncio
import logging
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .queue_backend import Job, QueueBackend
from .workload_routing import WorkloadType

logger = logging.getLogger(__name__)


@dataclass
class WorkloadCapacityLimiter:
    """Process-shared concurrency limiter for workload types.

    This class provides atomic capacity enforcement across all Worker instances
    in a process. It uses asyncio.Semaphore for each workload type that has a
    configured limit, ensuring that the total concurrent jobs of that type
    across all workers does not exceed the limit.

    The limiter must be created once per process and shared across all Worker
    instances to ensure correct capacity enforcement.

    Example:
        # Create once per process
        limiter = WorkloadCapacityLimiter.from_settings()

        # Share across all workers
        worker1 = Worker(backend, config, handler, capacity_limiter=limiter)
        worker2 = Worker(backend, config, handler, capacity_limiter=limiter)

    Attributes:
        limits: Mapping of WorkloadType to max concurrent jobs (0 = unlimited).
        _semaphores: Internal semaphores for capacity enforcement.

    """

    limits: dict[WorkloadType, int] = field(default_factory=dict)
    _semaphores: dict[WorkloadType, asyncio.BoundedSemaphore] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        """Initialize semaphores for workload types with limits > 0."""
        for work_type, limit in self.limits.items():
            if limit > 0:
                self._semaphores[work_type] = asyncio.BoundedSemaphore(limit)

    @classmethod
    def from_settings(cls) -> "WorkloadCapacityLimiter":
        """Create a capacity limiter from current settings.

        Reads OCR and profiling limits from settings and creates a limiter
        with appropriate semaphores. Call this once per process at startup.

        Returns:
            A configured WorkloadCapacityLimiter.

        """
        from .config import get_settings_instance

        settings = get_settings_instance()
        limits: dict[WorkloadType, int] = {}

        # OCR concurrency limit
        if settings.ocr_max_concurrent_jobs > 0:
            limits[WorkloadType.INGESTION_OCR] = settings.ocr_max_concurrent_jobs

        # Profiling concurrency limit
        if settings.profiling_max_concurrent_tasks > 0:
            limits[WorkloadType.PROFILING] = settings.profiling_max_concurrent_tasks

        return cls(limits=limits)

    async def acquire(self, work_type: WorkloadType) -> bool:
        """Acquire a permit for the given workload type (non-blocking).

        This is the preferred method for capacity checking before dequeue.
        It attempts a non-blocking acquire and returns immediately.

        Args:
            work_type: The workload type to acquire capacity for.

        Returns:
            True if a permit was acquired (or workload has no limit),
            False if at capacity.

        """
        semaphore = self._semaphores.get(work_type)
        if semaphore is None:
            # No limit configured for this workload type
            return True

        # Non-blocking check: if semaphore value > 0, we can acquire
        # Note: _value is an internal attribute but is the standard way to
        # check semaphore availability without blocking in asyncio
        if semaphore._value > 0:
            # Decrement immediately - this is atomic within the event loop
            semaphore._value -= 1
            return True
        return False

    def release(self, work_type: WorkloadType) -> None:
        """Release a permit for the given workload type.

        Must be called after job processing completes (success or failure).

        Args:
            work_type: The workload type to release capacity for.

        """
        semaphore = self._semaphores.get(work_type)
        if semaphore is not None:
            semaphore.release()

    def get_available(self, work_type: WorkloadType) -> int | None:
        """Get the number of available permits for a workload type.

        Args:
            work_type: The workload type to check.

        Returns:
            Number of available permits, or None if no limit is configured.

        """
        semaphore = self._semaphores.get(work_type)
        if semaphore is None:
            return None
        return semaphore._value

    def get_limit(self, work_type: WorkloadType) -> int:
        """Get the configured limit for a workload type.

        Args:
            work_type: The workload type to check.

        Returns:
            The configured limit, or 0 if no limit (unlimited).

        """
        return self.limits.get(work_type, 0)


@dataclass
class WorkerConfig:
    """Configuration for a worker process.

    Attributes:
        workload_types: Set of WorkloadTypes this worker consumes.
            The worker will dequeue jobs from queues corresponding to
            these workload types. For example, {WorkloadType.INGESTION}
            means the worker only processes ingestion jobs.

        poll_interval: Seconds between dequeue attempts when idle.
            When no jobs are available, the worker waits this long
            before trying again. Default is 1.0 second.

        shutdown_timeout: Seconds to wait for current job on shutdown.
            When a shutdown signal (SIGTERM/SIGINT) is received, the
            worker finishes the current job but will forcefully exit
            if it takes longer than this timeout. Default is 30 seconds.

    Raises:
        ValueError: If workload_types is empty.

    Example:
        # Worker that handles all workload types
        config = WorkerConfig(
            workload_types={
                WorkloadType.INGESTION,
                WorkloadType.LLM_WORKFLOW,
                WorkloadType.MAINTENANCE,
                WorkloadType.PROFILING
            }
        )

        # Worker specialized for LLM-heavy workloads
        config = WorkerConfig(
            workload_types={WorkloadType.LLM_WORKFLOW, WorkloadType.PROFILING},
            poll_interval=0.5,  # Poll more frequently
            shutdown_timeout=60.0  # Allow more time for LLM calls
        )

    """

    workload_types: set[WorkloadType]
    poll_interval: float = 1.0
    shutdown_timeout: float = 30.0

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if not self.workload_types:
            raise ValueError(
                "WorkerConfig requires at least one workload type. "
                "A worker with no workload types would never process any jobs."
            )
        if self.poll_interval <= 0:
            raise ValueError(
                f"poll_interval must be positive, got {self.poll_interval}. "
                "A zero or negative poll interval would cause a tight loop or errors."
            )
        if self.shutdown_timeout <= 0:
            raise ValueError(
                f"shutdown_timeout must be positive, got {self.shutdown_timeout}. "
                "A zero or negative timeout is invalid."
            )


class Worker:
    """Worker that consumes and processes jobs from queues.

    Implements the competing consumers pattern - multiple worker instances
    can run concurrently, each competing to dequeue jobs from shared queues.
    This enables horizontal scaling of job processing.

    The worker runs a continuous loop that:
    1. Dequeues jobs from configured queues
    2. Processes each job using the provided handler
    3. Acknowledges successful jobs or rejects failed jobs
    4. Logs job processing metrics
    5. Handles graceful shutdown on SIGTERM/SIGINT

    Thread Safety:
        The worker is designed to run in a single asyncio event loop.
        Multiple workers can run in separate processes or coroutines.

    Graceful Shutdown:
        When SIGTERM or SIGINT is received, the worker:
        1. Stops accepting new jobs
        2. Finishes processing the current job (if any)
        3. Exits cleanly

        Note: The shutdown is cooperative - if the current job handler does not
        return, the worker will wait indefinitely. Job handlers should respect
        reasonable timeouts or check for cancellation. The shutdown_timeout
        config is reserved for future forceful termination support.

    Example:
        async def process_job(job: Job) -> None:
            '''Process a job based on its payload.'''
            action = job.payload.get("action")
            if action == "index_document":
                await index_document(job.payload["document_id"])
            elif action == "generate_profile":
                await generate_profile(job.payload["document_id"])
            else:
                raise ValueError(f"Unknown action: {action}")

        config = WorkerConfig(
            workload_types={WorkloadType.INGESTION, WorkloadType.PROFILING}
        )
        backend = await get_queue_backend()
        worker = Worker(backend, config, process_job)

        # Run until shutdown signal
        await worker.run()

    Attributes:
        _backend: The queue backend to dequeue jobs from.
        _config: Worker configuration.
        _handler: Async function to process jobs.
        _running: Flag indicating if worker is running.
        _current_job: The job currently being processed (if any).

    """

    def __init__(
        self,
        backend: QueueBackend,
        config: WorkerConfig,
        job_handler: Callable[[Job], Awaitable[None]],
        worker_id: str | None = None,
        install_signal_handlers: bool = True,
        capacity_limiter: WorkloadCapacityLimiter | None = None,
    ) -> None:
        """Initialize the worker.


        Args:
            backend: The queue backend to use for dequeuing jobs.
            config: Worker configuration specifying workload types and timeouts.
            job_handler: Async function that processes a job. Should raise
                an exception if processing fails. The worker will handle
                acknowledgment/rejection based on success/failure.
            worker_id: Optional identifier for this worker instance (e.g., "1/4").
                Used in logs to distinguish concurrent workers in the same process.
            install_signal_handlers: Whether to install SIGTERM/SIGINT handlers.
                Set to False when running multiple workers in the same process
                (e.g., inline workers in the API server) to avoid overwriting
                each other's handlers. Default is True for standalone workers.
            capacity_limiter: Optional shared capacity limiter for concurrency control.
                When provided, the worker will use this limiter to enforce process-level
                concurrency limits for workload types like OCR and profiling.
                Should be shared across all Worker instances in the same process.

        """
        self._backend = backend
        self._config = config
        self._handler = job_handler
        self._worker_id = worker_id
        self._install_signal_handlers = install_signal_handlers
        self._running = False
        self._current_job: Job | None = None
        self._queue_index: int = 0  # Round-robin index for fair queue polling
        self._capacity_limiter = capacity_limiter
        # Track which workload types have acquired capacity for the current job
        self._acquired_capacity: WorkloadType | None = None

    def _setup_signal_handlers(self) -> None:
        """Set up graceful shutdown on SIGTERM and SIGINT.

        When a shutdown signal is received, the worker stops accepting
        new jobs and finishes the current job before exiting.
        """

        def handle_signal(signum: int, frame: Any) -> None:
            """Signal handler that sets the running flag to False."""
            signal_name = signal.Signals(signum).name
            logger.info(
                f"Received {signal_name} signal, finishing current job and shutting down...",
                extra={"signal": signal_name},
            )
            self._running = False

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

    async def _try_acquire_capacity(self, work_type: WorkloadType | None) -> bool:
        """Try to acquire capacity for a workload type (non-blocking).

        Uses the shared capacity limiter if available. If no limiter is
        configured, always returns True (no limiting).

        Args:
            work_type: The workload type to acquire capacity for, or None.

        Returns:
            True if capacity was acquired (or no limiting), False if at capacity.

        """
        if work_type is None or self._capacity_limiter is None:
            return True
        return await self._capacity_limiter.acquire(work_type)

    def _release_capacity(self, work_type: WorkloadType | None) -> None:
        """Release previously acquired capacity for a workload type.

        Args:
            work_type: The workload type to release capacity for, or None.

        """
        if work_type is None or self._capacity_limiter is None:
            return
        self._capacity_limiter.release(work_type)

    async def run(self) -> None:
        """Run the worker loop until shutdown signal.

        This method blocks until a shutdown signal (SIGTERM/SIGINT) is
        received. It continuously dequeues jobs from the configured queues
        and processes them using the job handler.

        The worker will:
        1. Try to dequeue from each configured queue in round-robin fashion
        2. Process any dequeued job
        3. Sleep for poll_interval if no jobs are available
        4. Exit gracefully on shutdown signal

        Example:
            worker = Worker(backend, config, process_job)
            await worker.run()  # Blocks until SIGTERM/SIGINT

        """
        self._running = True
        if self._install_signal_handlers:
            self._setup_signal_handlers()

        # Get queue names for configured workload types
        # Sort to ensure deterministic polling order across runs
        queue_names = sorted([wt.queue_name for wt in self._config.workload_types])

        worker_label = f"Worker[{self._worker_id}]" if self._worker_id else "Worker"
        logger.info(
            f"{worker_label} starting",
            extra={
                "worker_id": self._worker_id,
                "workload_types": [wt.value for wt in self._config.workload_types],
                "queue_names": queue_names,
                "poll_interval": self._config.poll_interval,
                "shutdown_timeout": self._config.shutdown_timeout,
            },
        )

        while self._running:
            # Try to dequeue from any of the configured queues
            job = await self._dequeue_from_any(queue_names)

            if job:
                await self._process_job(job)
            else:
                # No jobs available, sleep before trying again
                await asyncio.sleep(self._config.poll_interval)

        logger.info(f"{worker_label} stopped", extra={"worker_id": self._worker_id})

    async def _dequeue_from_any(
        self,
        queue_names: list[str],
    ) -> Job | None:
        """Try to dequeue from any of the configured queues using round-robin.

        Uses round-robin polling to ensure fair processing across all queues.
        Each call starts from the next queue in rotation, preventing any single
        queue from starving others when under continuous load.

        Before attempting to dequeue from a queue, tries to acquire capacity
        from the shared limiter. If at capacity, skips that queue and tries
        the next. This prevents workers from blocking on rate-limited work
        types (OCR, profiling) while other work sits in queues undone.

        When a job is successfully dequeued, the acquired capacity is held
        and will be released by _process_job() in its finally block.

        Args:
            queue_names: List of queue names to try dequeuing from.

        Returns:
            The first job found, or None if no jobs are available or all
            queues are at capacity.

        """
        num_queues = len(queue_names)

        for i in range(num_queues):
            idx = (self._queue_index + i) % num_queues
            queue_name = queue_names[idx]
            work_type = WorkloadType.from_queue_name(queue_name)

            # Try to acquire capacity before attempting to dequeue
            if not await self._try_acquire_capacity(work_type):
                limiter = self._capacity_limiter
                logger.debug(
                    f"Skipping queue '{queue_name}' - workload type at capacity",
                    extra={
                        "queue_name": queue_name,
                        "workload_type": work_type.value if work_type else None,
                        "available": limiter.get_available(work_type) if limiter and work_type else None,
                        "limit": limiter.get_limit(work_type) if limiter and work_type else 0,
                    },
                )
                continue

            # Capacity acquired - now try to dequeue
            try:
                job = await self._backend.dequeue(queue_name)
                if job:
                    # Keep the acquired capacity for this job
                    self._acquired_capacity = work_type
                    # Advance to next queue for next poll cycle
                    self._queue_index = (idx + 1) % num_queues
                    return job
                # No job in queue - release the capacity we acquired
                self._release_capacity(work_type)
            except Exception as e:
                # Dequeue failed - release the capacity we acquired
                self._release_capacity(work_type)
                logger.error(
                    f"Failed to dequeue from queue '{queue_name}': {e}",
                    extra={"queue_name": queue_name, "error": str(e)},
                )

        # No jobs found, still advance index for fairness on next poll
        self._queue_index = (self._queue_index + 1) % num_queues
        return None

    async def _process_job(self, job: Job) -> None:
        """Process a job with error handling and acknowledgment.

        This method:
        1. Records the job as current (for shutdown handling)
        2. Calls the job handler
        3. Acknowledges the job on success
        4. Rejects the job on failure (with requeue if under max_attempts)
        5. Logs job processing metrics
        6. Releases acquired capacity in finally block

        Args:
            job: The job to process.

        """
        self._current_job = job
        start_time = time.time()

        try:
            # Process the job using the provided handler
            await self._handler(job)

            # Acknowledge successful processing
            await self._backend.acknowledge(job)

            duration = time.time() - start_time
            logger.info(
                "Job completed successfully",
                extra={
                    "worker_id": self._worker_id,
                    "job_id": job.id,
                    "queue": job.queue_name,
                    "attempts": job.attempts,
                    "duration_ms": int(duration * 1000),
                },
            )

        except Exception as e:
            duration = time.time() - start_time
            logger.error(
                "Job processing failed",
                extra={
                    "worker_id": self._worker_id,
                    "job_id": job.id,
                    "queue": job.queue_name,
                    "error": str(e),
                    "attempts": job.attempts,
                    "max_attempts": job.max_attempts,
                    "duration_ms": int(duration * 1000),
                },
                exc_info=True,
            )

            # Requeue if under max attempts
            should_requeue = job.attempts < job.max_attempts
            await self._backend.reject(job, requeue=should_requeue)

            if should_requeue:
                logger.info(
                    "Job requeued for retry",
                    extra={
                        "worker_id": self._worker_id,
                        "job_id": job.id,
                        "queue": job.queue_name,
                        "attempts": job.attempts,
                        "max_attempts": job.max_attempts,
                    },
                )
            else:
                logger.warning(
                    "Job discarded after max attempts",
                    extra={
                        "worker_id": self._worker_id,
                        "job_id": job.id,
                        "queue": job.queue_name,
                        "attempts": job.attempts,
                        "max_attempts": job.max_attempts,
                    },
                )

        finally:
            self._current_job = None
            # Release acquired capacity back to the shared limiter
            if self._acquired_capacity is not None:
                self._release_capacity(self._acquired_capacity)
                self._acquired_capacity = None
