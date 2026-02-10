"""Workload Type Routing for Queue Backend.

This module provides workload-based routing for the queue system, enabling
jobs to be enqueued by logical workload type rather than hardcoded queue names.
This abstraction enables independent scaling of different workload types.

Example usage:
    from shu.core.workload_routing import WorkloadType, enqueue_job
    from shu.core.queue_backend import get_queue_backend

    backend = await get_queue_backend()

    # Enqueue a profiling job
    job = await enqueue_job(
        backend,
        WorkloadType.PROFILING,
        payload={"document_id": "doc123", "action": "profile"}
    )

    # Enqueue an ingestion job
    job = await enqueue_job(
        backend,
        WorkloadType.INGESTION,
        payload={"source": "gdrive", "file_id": "abc123"}
    )
"""

from enum import Enum
from typing import Any

from .queue_backend import Job, QueueBackend


class WorkloadType(Enum):
    """Categories of background work.

    Each workload type maps to a dedicated queue, enabling independent
    scaling of workers per workload type. For example, you can scale
    ingestion workers independently from LLM workflow workers.

    Attributes:
        INGESTION: Plugin feed ingestion operations.
            Examples: Running plugin feeds (Gmail, Google Drive, Outlook, etc.)
            to pull data from external sources into knowledge bases.

        INGESTION_OCR: OCR/text extraction stage of document pipeline.
            Examples: Running OCR on PDFs, extracting text from images,
            parsing document formats. First stage of async ingestion.

        INGESTION_EMBED: Embedding stage of document pipeline.
            Examples: Chunking extracted text, generating embeddings,
            storing vectors. Second stage of async ingestion.

        LLM_WORKFLOW: LLM-based workflows and experience execution.
            Examples: Scheduled experience execution (Morning Briefing,
            Inbox Triage, Project Pulse), chat message processing,
            workflow execution, prompt generation.

        MAINTENANCE: Scheduled maintenance and cleanup operations.
            Examples: Cache cleanup, database maintenance,
            expired session cleanup.

        PROFILING: Document profiling with LLM calls.
            Examples: Generating document summaries, extracting metadata,
            analyzing document content, creating document profiles.

    Example:
        # Scale ingestion workers independently
        # docker-compose.yml:
        # worker-ingestion:
        #   command: python -m shu.worker --workload-types=INGESTION
        #   replicas: 5
        #
        # worker-llm:
        #   command: python -m shu.worker --workload-types=LLM_WORKFLOW,PROFILING
        #   replicas: 2

    """

    INGESTION = "ingestion"
    INGESTION_OCR = "ingestion_ocr"
    INGESTION_EMBED = "ingestion_embed"
    LLM_WORKFLOW = "llm_workflow"
    MAINTENANCE = "maintenance"
    PROFILING = "profiling"

    @property
    def queue_name(self) -> str:
        """Get the queue name for this workload type.

        All queue names are prefixed with "shu:" for namespacing.

        Returns:
            The queue name (e.g., "shu:ingestion").

        Example:
            queue_name = WorkloadType.INGESTION.queue_name
            # Returns: "shu:ingestion"

        """
        return f"shu:{self.value}"


async def enqueue_job(
    backend: QueueBackend,
    workload_type: WorkloadType,
    payload: dict[str, Any],
    **job_kwargs: Any,
) -> Job:
    """Enqueue a job for the specified workload type.

    This is the preferred way to enqueue jobs in business logic. Instead
    of hardcoding queue names, use WorkloadType to route jobs to the
    appropriate queue.

    Args:
        backend: The queue backend to use (typically from dependency injection).
        workload_type: The type of workload (e.g., WorkloadType.INGESTION).
        payload: Job payload data as a JSON-serializable dictionary.
        **job_kwargs: Additional Job constructor arguments (e.g., max_attempts,
            visibility_timeout). These are passed directly to the Job constructor.

    Returns:
        The enqueued Job instance with generated ID and timestamps.

    Raises:
        QueueConnectionError: If the backend is unreachable.
        QueueOperationError: If the job cannot be serialized or enqueued.

    Example:
        from fastapi import Depends
        from shu.core.queue_backend import get_queue_backend_dependency, QueueBackend
        from shu.core.workload_routing import WorkloadType, enqueue_job

        async def trigger_profiling(
            document_id: str,
            queue: QueueBackend = Depends(get_queue_backend_dependency)
        ):
            job = await enqueue_job(
                queue,
                WorkloadType.PROFILING,
                payload={
                    "document_id": document_id,
                    "action": "generate_profile"
                },
                max_attempts=5,
                visibility_timeout=600  # 10 minutes
            )
            return {"job_id": job.id}

    """
    job = Job(queue_name=workload_type.queue_name, payload=payload, **job_kwargs)
    await backend.enqueue(job)
    return job
