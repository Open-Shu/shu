"""
Worker Entrypoint Module.

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
        - SHU_WORKER_MODE=dedicated
    
    worker-llm:
      image: shu:latest
      command: python -m shu.worker --workload-types=LLM_WORKFLOW,PROFILING
      replicas: 2
      environment:
        - SHU_REDIS_URL=redis://redis:6379
        - SHU_WORKER_MODE=dedicated
"""

import argparse
import asyncio
import sys
from typing import Set

from .core.config import get_settings_instance
from .core.logging import setup_logging, get_logger
from .core.worker import Worker, WorkerConfig
from .core.workload_routing import WorkloadType
from .core.queue_backend import get_queue_backend
from .core.database import init_db


logger = get_logger(__name__)


def parse_workload_types(workload_types_str: str) -> Set[WorkloadType]:
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
        except KeyError:
            valid_types = [wt.name for wt in WorkloadType]
            raise ValueError(
                f"Invalid workload type: {name}. "
                f"Valid types are: {', '.join(valid_types)}"
            )
    
    if not workload_types:
        raise ValueError("At least one workload type must be specified")
    
    return workload_types


async def process_job(job):
    """Process a job based on its workload type and payload.
    
    Routes jobs to appropriate handlers based on the queue name (workload type).
    
    Args:
        job: The job to process.
    
    Raises:
        ValueError: If job has unknown workload type or invalid payload.
        Exception: For transient errors that should trigger retry.
    """
    from .core.workload_routing import WorkloadType, get_queue_name
    
    # Determine workload type from queue name
    workload_type = None
    for wt in WorkloadType:
        if job.queue_name == get_queue_name(wt):
            workload_type = wt
            break
    
    if workload_type is None:
        raise ValueError(f"Unknown queue name: {job.queue_name}")
    
    # Route to appropriate handler
    if workload_type == WorkloadType.PROFILING:
        # TODO: Implement in task 11.1 (profiling migration)
        logger.warning(
            "PROFILING workload handler not yet implemented",
            extra={
                "job_id": job.id,
                "queue": job.queue_name,
            }
        )
    
    elif workload_type == WorkloadType.MAINTENANCE:
        # TODO: Implement in task 11.2 (scheduler migration)
        logger.warning(
            "MAINTENANCE workload handler not yet implemented",
            extra={
                "job_id": job.id,
                "queue": job.queue_name,
            }
        )
        # For now, just acknowledge to avoid blocking
    
    elif workload_type == WorkloadType.INGESTION:
        # Placeholder for future ingestion jobs
        logger.warning(
            "INGESTION workload handler not yet implemented",
            extra={
                "job_id": job.id,
                "queue": job.queue_name,
            }
        )
    
    elif workload_type == WorkloadType.LLM_WORKFLOW:
        # Placeholder for future LLM workflow jobs
        logger.warning(
            "LLM_WORKFLOW workload handler not yet implemented",
            extra={
                "job_id": job.id,
                "queue": job.queue_name,
            }
        )
    
    else:
        raise ValueError(f"Unsupported workload type: {workload_type}")


async def run_worker(
    workload_types: Set[WorkloadType],
    poll_interval: float = 1.0,
    shutdown_timeout: float = 30.0,
) -> None:
    """Run the worker loop.
    
    Args:
        workload_types: Set of workload types to consume.
        poll_interval: Seconds between dequeue attempts when idle.
        shutdown_timeout: Seconds to wait for current job on shutdown.
    """
    # Initialize database connection
    try:
        await init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}", exc_info=True)
        sys.exit(1)
    
    # Get queue backend
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
    
    # Create and run worker
    worker = Worker(backend, config, job_handler=process_job)
    
    logger.info(
        "Starting dedicated worker",
        extra={
            "workload_types": [wt.value for wt in workload_types],
            "poll_interval": poll_interval,
            "shutdown_timeout": shutdown_timeout,
        }
    )
    
    try:
        await worker.run()
    except KeyboardInterrupt:
        logger.info("Worker interrupted by user")
    except Exception as e:
        logger.error(f"Worker error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("Worker shutdown complete")


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
        """
    )
    
    parser.add_argument(
        "--workload-types",
        type=str,
        default=None,
        help="Comma-separated list of workload types to consume (e.g., INGESTION,PROFILING). "
             "If not specified, consumes all workload types."
    )
    
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Seconds between dequeue attempts when idle (default: 1.0)"
    )
    
    parser.add_argument(
        "--shutdown-timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for current job on shutdown (default: 30.0)"
    )
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging()
    
    # Get settings
    settings = get_settings_instance()
    
    logger.info(
        "Worker entrypoint starting",
        extra={
            "version": settings.version,
            "environment": settings.environment,
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
            )
        )
    except Exception as e:
        logger.error(f"Worker failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
