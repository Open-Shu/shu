"""
Unified Queue Backend Interface.

This module defines the QueueBackend protocol that provides a unified interface
for work queue operations implementing the Competing Consumers pattern. It supports
two interchangeable implementations:
- RedisQueueBackend: For horizontally-scaled deployments with multiple worker replicas
- InMemoryQueueBackend: For single-node/development deployments

Backend selection is automatic based on the SHU_REDIS_URL configuration.

Example usage:
    # In FastAPI endpoints (preferred - dependency injection):
    from shu.core.queue_backend import get_queue_backend_dependency, QueueBackend
    
    async def my_endpoint(
        queue: QueueBackend = Depends(get_queue_backend_dependency)
    ):
        job = Job(queue_name="tasks", payload={"task": "process"})
        await queue.enqueue(job)
    
    # In background tasks or non-FastAPI code:
    from shu.core.queue_backend import get_queue_backend
    
    backend = await get_queue_backend()
    job = Job(queue_name="tasks", payload={"task": "process"})
    await backend.enqueue(job)
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# =============================================================================
# Exception Hierarchy
# =============================================================================


class QueueError(Exception):
    """Base exception for queue operations.
    
    All queue-related exceptions inherit from this class, allowing
    consumers to catch all queue errors with a single except clause.
    
    Attributes:
        message: Human-readable error description.
        details: Optional dictionary with additional error context.
    """
    
    def __init__(
        self,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)


class QueueConnectionError(QueueError):
    """Raised when the queue backend is unreachable.
    
    This exception indicates a connectivity issue with the underlying
    queue storage (e.g., Redis server is down, network timeout).
    
    Example:
        try:
            await backend.enqueue(job)
        except QueueConnectionError as e:
            logger.warning(f"Queue unavailable: {e.message}")
            # Handle gracefully or retry
    """
    pass


class QueueOperationError(QueueError):
    """Raised when a queue operation fails.
    
    This exception indicates that the queue backend was reachable but
    the operation itself failed (e.g., invalid job data, queue full).
    
    Example:
        try:
            await backend.dequeue("my_queue")
        except QueueOperationError as e:
            logger.error(f"Queue operation failed: {e.message}")
    """
    pass


class JobSerializationError(QueueError):
    """Raised when job serialization/deserialization fails.
    
    This exception indicates that a Job object could not be converted
    to/from JSON format.
    
    Example:
        try:
            job = Job.from_json(data)
        except JobSerializationError as e:
            logger.error(f"Failed to deserialize job: {e.message}")
    """
    pass


# =============================================================================
# Job Dataclass
# =============================================================================


@dataclass
class Job:
    """A unit of work to be processed by a worker.
    
    Jobs are the fundamental unit of work in the queue system. Each job
    contains a payload (the actual work data) and metadata for tracking
    processing state, retries, and visibility timeout.
    
    Attributes:
        queue_name: The queue this job belongs to. Used for routing.
        payload: JSON-serializable dictionary containing job data.
        id: Unique identifier for the job. Auto-generated if not provided.
        created_at: Timestamp when the job was created (UTC).
        attempts: Number of times this job has been attempted.
        max_attempts: Maximum number of retry attempts before giving up.
        visibility_timeout: Seconds the job is hidden after dequeue.
            During this time, other consumers cannot see the job.
            If not acknowledged within this time, the job becomes
            visible again for reprocessing.
    
    Example:
        # Create a job for document processing
        job = Job(
            queue_name="shu:ingestion",
            payload={"document_id": "doc123", "action": "index"},
            max_attempts=5,
            visibility_timeout=600,  # 10 minutes
        )
        
        # Serialize for storage
        json_str = job.to_json()
        
        # Deserialize from storage
        restored_job = Job.from_json(json_str)
    """
    
    queue_name: str
    payload: Dict[str, Any]
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    attempts: int = 0
    max_attempts: int = 3
    visibility_timeout: int = 300  # 5 minutes default
    
    def to_json(self) -> str:
        """Serialize job to JSON string.
        
        Converts the Job instance to a JSON string suitable for storage
        in Redis or other backends.
        
        Returns:
            JSON string representation of the job.
        
        Raises:
            JobSerializationError: If the payload is not JSON-serializable.
        
        Example:
            job = Job(queue_name="tasks", payload={"key": "value"})
            json_str = job.to_json()
            # '{"id": "...", "queue_name": "tasks", ...}'
        """
        try:
            return json.dumps({
                "id": self.id,
                "queue_name": self.queue_name,
                "payload": self.payload,
                "created_at": self.created_at.isoformat(),
                "attempts": self.attempts,
                "max_attempts": self.max_attempts,
                "visibility_timeout": self.visibility_timeout,
            })
        except (TypeError, ValueError) as e:
            raise JobSerializationError(
                f"Failed to serialize job {self.id}: {e}",
                details={"job_id": self.id, "error": str(e)}
            ) from e
    
    @classmethod
    def from_json(cls, data: str) -> "Job":
        """Deserialize job from JSON string.
        
        Reconstructs a Job instance from its JSON representation.
        
        Args:
            data: JSON string containing job data.
        
        Returns:
            Reconstructed Job instance.
        
        Raises:
            JobSerializationError: If the JSON is invalid or missing
                required fields.
        
        Example:
            json_str = '{"id": "abc", "queue_name": "tasks", ...}'
            job = Job.from_json(json_str)
        """
        try:
            obj = json.loads(data)
            return cls(
                id=obj["id"],
                queue_name=obj["queue_name"],
                payload=obj["payload"],
                created_at=datetime.fromisoformat(obj["created_at"]),
                attempts=obj.get("attempts", 0),
                max_attempts=obj.get("max_attempts", 3),
                visibility_timeout=obj.get("visibility_timeout", 300),
            )
        except json.JSONDecodeError as e:
            raise JobSerializationError(
                f"Failed to parse job JSON: {e}",
                details={"error": str(e), "data_preview": data[:100] if data else None}
            ) from e
        except KeyError as e:
            raise JobSerializationError(
                f"Missing required field in job JSON: {e}",
                details={"missing_field": str(e), "data_preview": data[:100] if data else None}
            ) from e
        except (TypeError, ValueError) as e:
            raise JobSerializationError(
                f"Invalid job data: {e}",
                details={"error": str(e), "data_preview": data[:100] if data else None}
            ) from e


# =============================================================================
# QueueBackend Protocol
# =============================================================================


@runtime_checkable
class QueueBackend(Protocol):
    """Protocol defining the queue backend interface.
    
    All implementations must provide these async methods for work queue
    operations with support for the competing consumers pattern.
    
    This protocol uses structural typing via @runtime_checkable, allowing
    any class that implements these methods to be used as a QueueBackend
    without explicit inheritance.
    
    Thread Safety:
        All implementations must be safe for concurrent access from
        multiple workers/coroutines.
    
    Visibility Timeout:
        When a job is dequeued, it becomes invisible to other consumers
        for the visibility_timeout duration. If not acknowledged within
        this time, the job becomes available for reprocessing.
    
    Competing Consumers:
        Multiple workers can call dequeue on the same queue, and each job
        will be delivered to exactly one worker. This enables horizontal
        scaling of job processing.
    
    Example:
        class MyQueueBackend:
            async def enqueue(self, job: Job) -> bool: ...
            async def dequeue(self, queue_name: str, ...) -> Optional[Job]: ...
            # ... other methods
        
        backend: QueueBackend = MyQueueBackend()  # Type checks pass
    """
    
    async def enqueue(self, job: Job) -> bool:
        """Add a job to the queue.
        
        Places a job at the end of the specified queue. The job will be
        available for dequeue by any worker consuming from that queue.
        
        Args:
            job: The job to enqueue. Must have a valid queue_name.
        
        Returns:
            True if the job was successfully enqueued.
        
        Raises:
            QueueConnectionError: If the backend is unreachable.
            QueueOperationError: If the operation fails.
        
        Example:
            job = Job(queue_name="tasks", payload={"action": "process"})
            success = await backend.enqueue(job)
        """
        ...
    
    async def dequeue(
        self,
        queue_name: str,
        timeout_seconds: Optional[int] = None,
    ) -> Optional[Job]:
        """Remove and return the next job from the queue.
        
        This operation supports the competing consumers pattern - multiple
        workers can call dequeue on the same queue, and each job will be
        delivered to exactly one worker.
        
        When a job is dequeued, it becomes invisible to other consumers
        for the job's visibility_timeout duration. The job's attempts
        counter is incremented.
        
        Args:
            queue_name: The queue to dequeue from.
            timeout_seconds: How long to wait for a job.
                - If None, returns immediately (non-blocking).
                - If 0, blocks indefinitely until a job is available.
                - If positive, blocks for up to that many seconds.
        
        Returns:
            The next job with attempts incremented, or None if no job
            is available within the timeout.
        
        Raises:
            QueueConnectionError: If the backend is unreachable.
            QueueOperationError: If the operation fails.
        
        Example:
            # Non-blocking dequeue
            job = await backend.dequeue("tasks")
            
            # Blocking dequeue with 5 second timeout
            job = await backend.dequeue("tasks", timeout_seconds=5)
        """
        ...
    
    async def acknowledge(self, job: Job) -> bool:
        """Acknowledge successful processing of a job.
        
        This removes the job from the processing set, preventing it from
        being redelivered after visibility timeout expires. Call this
        after successfully processing a job.
        
        Args:
            job: The job to acknowledge.
        
        Returns:
            True if the job was acknowledged, False if not found
            (e.g., already acknowledged or visibility timeout expired).
        
        Raises:
            QueueConnectionError: If the backend is unreachable.
        
        Example:
            job = await backend.dequeue("tasks")
            if job:
                try:
                    await process_job(job)
                    await backend.acknowledge(job)
                except Exception:
                    await backend.reject(job, requeue=True)
        """
        ...
    
    async def reject(
        self,
        job: Job,
        requeue: bool = True,
    ) -> bool:
        """Reject a job, optionally requeueing it for retry.
        
        Call this when job processing fails. If requeue is True and the
        job hasn't exceeded max_attempts, it will be returned to the
        queue for another attempt.
        
        Args:
            job: The job to reject.
            requeue: If True, the job is returned to the queue with
                incremented attempts (if under max_attempts).
                If False, the job is discarded.
        
        Returns:
            True if the operation succeeded.
        
        Raises:
            QueueConnectionError: If the backend is unreachable.
        
        Example:
            job = await backend.dequeue("tasks")
            if job:
                try:
                    await process_job(job)
                    await backend.acknowledge(job)
                except TransientError:
                    # Retry later
                    await backend.reject(job, requeue=True)
                except PermanentError:
                    # Don't retry
                    await backend.reject(job, requeue=False)
        """
        ...
    
    async def peek(
        self,
        queue_name: str,
        limit: int = 10,
    ) -> List[Job]:
        """View jobs in the queue without removing them.
        
        Returns jobs from the front of the queue without affecting their
        state. Useful for monitoring and debugging.
        
        Args:
            queue_name: The queue to peek.
            limit: Maximum number of jobs to return. Default is 10.
        
        Returns:
            List of jobs (may be empty if queue is empty).
        
        Raises:
            QueueConnectionError: If the backend is unreachable.
        
        Example:
            # Check what's waiting in the queue
            pending_jobs = await backend.peek("tasks", limit=5)
            for job in pending_jobs:
                print(f"Pending: {job.id} - {job.payload}")
        """
        ...
    
    async def queue_length(self, queue_name: str) -> int:
        """Get the number of jobs waiting in the queue.
        
        Returns the count of jobs that are ready to be dequeued. Does not
        include jobs that are currently being processed (in-flight).
        
        Args:
            queue_name: The queue to check.
        
        Returns:
            Number of jobs in the queue (not including processing jobs).
        
        Raises:
            QueueConnectionError: If the backend is unreachable.
        
        Example:
            length = await backend.queue_length("tasks")
            if length > 100:
                logger.warning(f"Queue backlog: {length} jobs")
        """
        ...
    
    async def schedule(
        self,
        job: Job,
        delay_seconds: int,
    ) -> bool:
        """Schedule a job to be enqueued after a delay.
        
        The job will not be visible to consumers until the delay has
        elapsed. Useful for implementing retry backoff or scheduled tasks.
        
        Args:
            job: The job to schedule.
            delay_seconds: Seconds to wait before enqueueing. Must be
                positive.
        
        Returns:
            True if the job was scheduled.
        
        Raises:
            QueueConnectionError: If the backend is unreachable.
            ValueError: If delay_seconds is not positive.
        
        Example:
            # Retry with exponential backoff
            delay = 2 ** job.attempts  # 1, 2, 4, 8, ... seconds
            await backend.schedule(job, delay_seconds=delay)
        """
        ...
