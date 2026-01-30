"""Worker Consumer Loop for Queue Backend.

This module implements the worker consumer loop that processes jobs from queues
using the competing consumers pattern. Workers can be configured to consume
specific workload types and can run either in-process with the API or as
dedicated worker processes.

Example usage:
    from shu.core.worker import Worker, WorkerConfig
    from shu.core.workload_routing import WorkloadType
    from shu.core.queue_backend import get_queue_backend

    # Configure worker to consume ingestion and profiling jobs
    config = WorkerConfig(
        workload_types={WorkloadType.INGESTION, WorkloadType.PROFILING},
        poll_interval=1.0,
        shutdown_timeout=30.0
    )

    # Create worker with job handler
    backend = await get_queue_backend()
    worker = Worker(backend, config, job_handler=process_job)

    # Run worker loop (blocks until shutdown signal)
    await worker.run()
"""

import asyncio
import logging
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .queue_backend import Job, QueueBackend
from .workload_routing import WorkloadType

logger = logging.getLogger(__name__)


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
    ):
        """Initialize the worker.

        Args:
            backend: The queue backend to use for dequeuing jobs.
            config: Worker configuration specifying workload types and timeouts.
            job_handler: Async function that processes a job. Should raise
                an exception if processing fails. The worker will handle
                acknowledgment/rejection based on success/failure.

        """
        self._backend = backend
        self._config = config
        self._handler = job_handler
        self._running = False
        self._current_job: Job | None = None
        self._queue_index: int = 0  # Round-robin index for fair queue polling

    def _setup_signal_handlers(self) -> None:
        """Setup graceful shutdown on SIGTERM and SIGINT.

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
        self._setup_signal_handlers()

        # Get queue names for configured workload types
        # Sort to ensure deterministic polling order across runs
        queue_names = sorted([wt.queue_name for wt in self._config.workload_types])

        logger.info(
            "Worker starting",
            extra={
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

        logger.info("Worker stopped")

    async def _dequeue_from_any(
        self,
        queue_names: list[str],
    ) -> Job | None:
        """Try to dequeue from any of the configured queues using round-robin.

        Uses round-robin polling to ensure fair processing across all queues.
        Each call starts from the next queue in rotation, preventing any single
        queue from starving others when under continuous load.

        Args:
            queue_names: List of queue names to try dequeuing from.

        Returns:
            The first job found, or None if no jobs are available.

        """
        num_queues = len(queue_names)

        for i in range(num_queues):
            idx = (self._queue_index + i) % num_queues
            queue_name = queue_names[idx]
            try:
                job = await self._backend.dequeue(queue_name)
                if job:
                    # Advance to next queue for next poll cycle
                    self._queue_index = (idx + 1) % num_queues
                    return job
            except Exception as e:
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
                        "job_id": job.id,
                        "queue": job.queue_name,
                        "attempts": job.attempts,
                        "max_attempts": job.max_attempts,
                    },
                )

        finally:
            self._current_job = None
