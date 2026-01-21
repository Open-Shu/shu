"""Worker Entrypoint Module.

This module provides the command-line entrypoint for running dedicated worker
processes. Workers can be configured to consume specific workload types and
run independently from the API process.

Usage:
    # Run worker consuming all workload types
    python -m shu.worker

    # Run worker consuming specific workload types
    python -m shu.worker --workload-types=INGESTION,PROFILING

    # Run worker with custom poll interval
    python -m shu.worker --workload-types=LLM_WORKFLOW --poll-interval=0.5

Example Docker Compose:
    worker-ingestion:
      image: shu:latest
      command: python -m shu.worker --workload-types=INGESTION
      replicas: 5
      environment:
        - SHU_REDIS_URL=redis://redis:6379

    worker-llm:
      image: shu:latest
      command: python -m shu.worker --workload-types=LLM_WORKFLOW,PROFILING
      replicas: 2
      environment:
        - SHU_REDIS_URL=redis://redis:6379

    # Note: When using dedicated workers, set SHU_WORKERS_ENABLED=false on the API
    # to prevent duplicate job processing.
"""

import argparse
import asyncio
import sys

from .core.config import get_settings_instance
from .core.database import init_db
from .core.logging import get_logger, setup_logging
from .core.queue_backend import get_queue_backend
from .core.worker import Worker, WorkerConfig
from .core.workload_routing import WorkloadType

logger = get_logger(__name__)


def parse_workload_types(workload_types_str: str) -> set[WorkloadType]:
    """Parse comma-separated workload types string into a set of WorkloadType enums.

    Args:
        workload_types_str: Comma-separated string of workload type names
            (e.g., "INGESTION,PROFILING" or "ingestion,profiling").

    Returns:
        Set of WorkloadType enum values.

    Raises:
        ValueError: If any workload type name is invalid.

    Example:
        types = parse_workload_types("INGESTION,PROFILING")
        # Returns: {WorkloadType.INGESTION, WorkloadType.PROFILING}

    """
    if not workload_types_str.strip():
        raise ValueError("Workload types cannot be empty")

    workload_types = set()
    for name in workload_types_str.split(","):
        name = name.strip().upper()
        if not name:
            continue

        try:
            workload_type = WorkloadType[name]
            workload_types.add(workload_type)
        except KeyError as err:
            valid_types = [wt.name for wt in WorkloadType]
            raise ValueError(f"Invalid workload type: {name}. " f"Valid types are: {', '.join(valid_types)}") from err

    if not workload_types:
        raise ValueError("At least one workload type must be specified")

    return workload_types


async def _handle_profiling_job(job) -> None:
    """Handle a PROFILING workload job.

    Runs the profiling orchestrator for the specified document.

    Args:
        job: The job containing document_id in payload.

    Raises:
        ValueError: If document_id is missing from payload.
        Exception: If profiling fails (triggers retry).
    """
    document_id = job.payload.get("document_id")
    if not document_id:
        raise ValueError("PROFILING job missing document_id in payload")

    logger.info(
        "Processing profiling job",
        extra={"job_id": job.id, "document_id": document_id}
    )

    from .core.database import get_async_session_local
    from .core.config import get_config_manager, get_settings_instance
    from .services.side_call_service import SideCallService
    from .services.profiling_orchestrator import ProfilingOrchestrator

    settings = get_settings_instance()
    session_local = get_async_session_local()

    async with session_local() as session:
        config_manager = get_config_manager()
        side_call_service = SideCallService(session, config_manager)
        orchestrator = ProfilingOrchestrator(session, settings, side_call_service)

        result = await orchestrator.run_for_document(document_id)

        if result.success:
            logger.info(
                "Profiling job completed successfully",
                extra={
                    "job_id": job.id,
                    "document_id": document_id,
                    "profiling_mode": result.profiling_mode.value if result.profiling_mode else None,
                    "tokens_used": result.tokens_used,
                    "duration_ms": result.duration_ms,
                }
            )
        else:
            # Log error but raise exception to trigger retry
            logger.error(
                "Profiling job failed",
                extra={
                    "job_id": job.id,
                    "document_id": document_id,
                    "error": result.error,
                }
            )
            raise Exception(f"Profiling failed for document {document_id}: {result.error}")


async def process_job(job):
    """Process a job based on its workload type and payload.

    Routes jobs to appropriate handlers based on the queue name (workload type).

    Args:
        job: The job to process.

    Raises:
        ValueError: If job has unknown workload type or invalid payload.
        Exception: For transient errors that should trigger retry.

    """
    from .core.workload_routing import WorkloadType

    # Determine workload type from queue name
    workload_type = None
    for wt in WorkloadType:
        if job.queue_name == wt.queue_name:
            workload_type = wt
            break

    if workload_type is None:
        raise ValueError(f"Unknown queue name: {job.queue_name}")

    # Route to appropriate handler
    if workload_type == WorkloadType.PROFILING:
        await _handle_profiling_job(job)
    
    elif workload_type == WorkloadType.MAINTENANCE:
        # TODO: Implement in task 11.2 (scheduler migration)
        logger.warning(
            "MAINTENANCE workload handler not yet implemented",
            extra={
                "job_id": job.id,
                "queue": job.queue_name,
            },
        )
        # For now, just acknowledge to avoid blocking

    elif workload_type == WorkloadType.INGESTION:
        # Placeholder for future ingestion jobs
        logger.warning(
            "INGESTION workload handler not yet implemented",
            extra={
                "job_id": job.id,
                "queue": job.queue_name,
            },
        )

    elif workload_type == WorkloadType.LLM_WORKFLOW:
        # Placeholder for future LLM workflow jobs
        logger.warning(
            "LLM_WORKFLOW workload handler not yet implemented",
            extra={
                "job_id": job.id,
                "queue": job.queue_name,
            },
        )

    else:
        raise ValueError(f"Unsupported workload type: {workload_type}")


async def run_worker(
    workload_types: set[WorkloadType],
    poll_interval: float = 1.0,
    shutdown_timeout: float = 30.0,
    concurrency: int = 1,
) -> None:
    """Run the worker loop with configurable concurrency.

    Args:
        workload_types: Set of workload types to consume.
        poll_interval: Seconds between dequeue attempts when idle.
        shutdown_timeout: Seconds to wait for current job on shutdown.
        concurrency: Number of concurrent worker tasks to run.
    """
    # Initialize database connection
    try:
        await init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}", exc_info=True)
        sys.exit(1)

    # Get queue backend (shared by all workers)
    try:
        backend = await get_queue_backend()
        logger.info("Queue backend initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize queue backend: {e}", exc_info=True)
        sys.exit(1)


    # Create worker configuration
    config = WorkerConfig(
        workload_types=workload_types,
        poll_interval=poll_interval,
        shutdown_timeout=shutdown_timeout,
    )

    # Create N concurrent workers
    concurrency = max(1, concurrency)
    workers = []
    for i in range(concurrency):
        worker_id = f"{i + 1}/{concurrency}"
        worker = Worker(backend, config, job_handler=process_job, worker_id=worker_id)
        workers.append(worker)

    logger.info(
        "Starting dedicated workers",
        extra={
            "concurrency": concurrency,
            "workload_types": [wt.value for wt in workload_types],
            "poll_interval": poll_interval,
            "shutdown_timeout": shutdown_timeout,
        },
    )


    try:
        # Run all workers concurrently
        await asyncio.gather(*[w.run() for w in workers])
    except KeyboardInterrupt:
        logger.info("Workers interrupted by user")
    except Exception as e:
        logger.error(f"Worker error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("Workers shutdown complete")


def main() -> None:
    """Main entrypoint for the worker process."""
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Shu dedicated worker process",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run worker consuming all workload types
  python -m shu.worker

  # Run worker consuming specific workload types
  python -m shu.worker --workload-types=INGESTION,PROFILING

  # Run worker with custom poll interval
  python -m shu.worker --workload-types=LLM_WORKFLOW --poll-interval=0.5

Valid workload types:
  INGESTION    - Document ingestion and indexing
  LLM_WORKFLOW - LLM-based workflows and chat
  MAINTENANCE  - Scheduled tasks and cleanup
  PROFILING    - Document profiling with LLM calls
        """,
    )

    parser.add_argument(
        "--workload-types",
        type=str,
        default=None,
        help="Comma-separated list of workload types to consume (e.g., INGESTION,PROFILING). "
        "If not specified, consumes all workload types.",
    )

    # Get settings first so we can use them as defaults
    settings = get_settings_instance()

    parser.add_argument(
        "--poll-interval",
        type=float,
        default=settings.worker_poll_interval,
        help=f"Seconds between dequeue attempts when idle (default: {settings.worker_poll_interval})",
    )

    parser.add_argument(
        "--shutdown-timeout",
        type=float,
        default=settings.worker_shutdown_timeout,
        help=f"Seconds to wait for current job on shutdown (default: {settings.worker_shutdown_timeout})"
    )

    parser.add_argument(
        "--concurrency",
        type=int,
        default=settings.worker_concurrency,
        help=f"Number of concurrent worker tasks (default: {settings.worker_concurrency})"
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging()


    logger.info(
        "Worker entrypoint starting",
        extra={
            "version": settings.version,
            "environment": settings.environment,
            "concurrency": args.concurrency,
        }
    )


    # Parse workload types
    try:
        if args.workload_types:
            workload_types = parse_workload_types(args.workload_types)
        else:
            # Default to all workload types
            workload_types = set(WorkloadType)
            logger.info("No workload types specified, consuming all types")
    except ValueError as e:
        logger.error(f"Invalid workload types: {e}")
        sys.exit(1)


    # Run worker
    try:
        asyncio.run(
            run_worker(
                workload_types=workload_types,
                poll_interval=args.poll_interval,
                shutdown_timeout=args.shutdown_timeout,
                concurrency=args.concurrency,
            )
        )
    except Exception as e:
        logger.error(f"Worker failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
