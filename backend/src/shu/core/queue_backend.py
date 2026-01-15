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

import asyncio
import json
import logging
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable

import redis.asyncio as redis

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


# =============================================================================
# InMemoryQueueBackend Implementation
# =============================================================================


class InMemoryQueueBackend:
    """In-memory queue implementation for single-node deployments.
    
    Thread-safe implementation using asyncio primitives for blocking
    operations and threading locks for data structure access. Implements
    the Competing Consumers pattern for in-process workers.
    
    This backend is suitable for:
        - Local development environments
        - Bare-metal single-node installs
        - Testing and CI/CD pipelines
        - Simple deployments without Redis dependency
    
    Limitations:
        - Data is NOT shared across processes: Each process has its own
          isolated queue state. Multiple processes cannot compete for jobs.
        - Data is LOST on process restart: All queued jobs are lost when
          the process terminates. No persistence to disk.
        - NOT suitable for horizontal scaling: Cannot distribute work across
          multiple worker replicas running in separate processes/containers.
        - Only works within a single process: Workers must be coroutines
          or threads within the same Python process.
    
    For production deployments requiring horizontal scaling or persistence,
    use RedisQueueBackend instead.
    
    Thread Safety:
        All operations are protected by a reentrant lock (RLock) to ensure
        thread-safe access from multiple coroutines and threads.
    
    Visibility Timeout:
        When a job is dequeued, it is moved to a processing set with an
        expiration timestamp. If not acknowledged within the visibility
        timeout, the job is automatically restored to the queue for
        reprocessing. Expired jobs are checked and restored during
        dequeue operations and periodic cleanup.
    
    Example:
        backend = InMemoryQueueBackend()
        
        # Enqueue a job
        job = Job(queue_name="tasks", payload={"action": "process"})
        await backend.enqueue(job)
        
        # Dequeue and process
        job = await backend.dequeue("tasks", timeout_seconds=5)
        if job:
            try:
                await process_job(job)
                await backend.acknowledge(job)
            except Exception:
                await backend.reject(job, requeue=True)
    
    Attributes:
        cleanup_interval_seconds: How often to run cleanup of expired
            visibility timeouts. Set to 0 to disable periodic cleanup
            (cleanup still happens during dequeue operations).
    """
    
    def __init__(self, cleanup_interval_seconds: int = 60):
        """Initialize the in-memory queue backend.
        
        Args:
            cleanup_interval_seconds: Interval for periodic cleanup of
                expired visibility timeouts. Default is 60 seconds.
                Set to 0 to disable periodic cleanup.
        """
        # queue_name -> list of Job JSON strings (FIFO order)
        self._queues: Dict[str, List[str]] = defaultdict(list)
        
        # queue_name -> {job_id: (job_json, expiry_timestamp)}
        # Tracks jobs that have been dequeued but not yet acknowledged
        self._processing: Dict[str, Dict[str, Tuple[str, float]]] = defaultdict(dict)
        
        # queue_name -> [(execute_at_timestamp, job_json), ...]
        # Sorted list of scheduled jobs
        self._scheduled: Dict[str, List[Tuple[float, str]]] = defaultdict(list)
        
        # queue_name -> asyncio.Event for signaling new jobs
        # Used for blocking dequeue operations
        self._events: Dict[str, asyncio.Event] = {}
        
        # Reentrant lock for thread-safe operations
        self._lock = threading.RLock()
        
        # Cleanup configuration
        self._cleanup_interval = cleanup_interval_seconds
        self._last_cleanup = time.time()
    
    def _get_event(self, queue_name: str) -> asyncio.Event:
        """Get or create an asyncio.Event for the specified queue.
        
        Events are used to signal waiting dequeue operations when new
        jobs are enqueued.
        
        Args:
            queue_name: The queue to get the event for.
        
        Returns:
            The asyncio.Event for the queue.
        """
        if queue_name not in self._events:
            self._events[queue_name] = asyncio.Event()
        return self._events[queue_name]
    
    def _restore_expired_jobs(self, queue_name: str) -> int:
        """Move expired processing jobs back to the queue.
        
        Jobs that have exceeded their visibility timeout are restored
        to the front of the queue for reprocessing.
        
        This method must be called with self._lock held.
        
        Args:
            queue_name: The queue to check for expired jobs.
        
        Returns:
            Number of jobs restored.
        """
        now = time.time()
        expired = []
        
        for job_id, (job_json, expiry) in list(self._processing[queue_name].items()):
            if now > expiry:
                expired.append((job_id, job_json))
        
        for job_id, job_json in expired:
            del self._processing[queue_name][job_id]
            # Add to front of queue for priority reprocessing
            self._queues[queue_name].insert(0, job_json)
            logger.debug(
                "Restored expired job to queue",
                extra={"job_id": job_id, "queue_name": queue_name}
            )
        
        return len(expired)
    
    def _move_scheduled_jobs(self, queue_name: str) -> int:
        """Move scheduled jobs that are ready to the main queue.
        
        Jobs whose execute_at timestamp has passed are moved from the
        scheduled list to the main queue.
        
        This method must be called with self._lock held.
        
        Args:
            queue_name: The queue to check for ready scheduled jobs.
        
        Returns:
            Number of jobs moved.
        """
        now = time.time()
        ready = []
        remaining = []
        
        for execute_at, job_json in self._scheduled[queue_name]:
            if now >= execute_at:
                ready.append(job_json)
            else:
                remaining.append((execute_at, job_json))
        
        self._scheduled[queue_name] = remaining
        
        for job_json in ready:
            self._queues[queue_name].append(job_json)
            logger.debug(
                "Moved scheduled job to queue",
                extra={"queue_name": queue_name}
            )
        
        return len(ready)
    
    def _maybe_cleanup(self) -> None:
        """Perform periodic cleanup if interval has elapsed.
        
        This method must be called with self._lock held.
        """
        if self._cleanup_interval <= 0:
            return
        
        now = time.time()
        if now - self._last_cleanup >= self._cleanup_interval:
            self._last_cleanup = now
            for queue_name in list(self._processing.keys()):
                self._restore_expired_jobs(queue_name)
            for queue_name in list(self._scheduled.keys()):
                self._move_scheduled_jobs(queue_name)
    
    async def enqueue(self, job: Job) -> bool:
        """Add a job to the queue.
        
        Places the job at the end of the specified queue and signals
        any waiting dequeue operations.
        
        Args:
            job: The job to enqueue.
        
        Returns:
            True if the job was successfully enqueued.
        
        Raises:
            QueueOperationError: If the job cannot be serialized.
        """
        try:
            job_json = job.to_json()
        except JobSerializationError as e:
            raise QueueOperationError(
                f"Failed to enqueue job: {e.message}",
                details={"job_id": job.id, "error": str(e)}
            ) from e
        
        with self._lock:
            self._queues[job.queue_name].append(job_json)
            event = self._get_event(job.queue_name)
        
        # Signal waiting dequeue operations (outside lock to avoid deadlock)
        event.set()
        
        logger.debug(
            "Job enqueued",
            extra={"job_id": job.id, "queue_name": job.queue_name}
        )
        return True
    
    async def dequeue(
        self,
        queue_name: str,
        timeout_seconds: Optional[int] = None,
    ) -> Optional[Job]:
        """Remove and return the next job from the queue.
        
        Supports blocking wait for jobs using asyncio.Event. When a job
        is dequeued, it is moved to the processing set with a visibility
        timeout. The job's attempts counter is incremented.
        
        Args:
            queue_name: The queue to dequeue from.
            timeout_seconds: How long to wait for a job.
                - If None, returns immediately (non-blocking).
                - If 0, blocks indefinitely until a job is available.
                - If positive, blocks for up to that many seconds.
        
        Returns:
            The next job with attempts incremented, or None if no job
            is available within the timeout.
        """
        deadline = None
        if timeout_seconds is not None and timeout_seconds > 0:
            deadline = time.time() + timeout_seconds
        
        while True:
            with self._lock:
                # Perform periodic cleanup
                self._maybe_cleanup()
                
                # Check for expired visibility timeouts
                self._restore_expired_jobs(queue_name)
                
                # Move ready scheduled jobs
                self._move_scheduled_jobs(queue_name)
                
                # Try to get a job from the queue
                if self._queues[queue_name]:
                    job_json = self._queues[queue_name].pop(0)
                    job = Job.from_json(job_json)
                    job.attempts += 1
                    
                    # Add to processing set with visibility timeout
                    expiry = time.time() + job.visibility_timeout
                    self._processing[queue_name][job.id] = (job.to_json(), expiry)
                    
                    logger.debug(
                        "Job dequeued",
                        extra={
                            "job_id": job.id,
                            "queue_name": queue_name,
                            "attempts": job.attempts,
                        }
                    )
                    return job
                
                # Get event for waiting
                event = self._get_event(queue_name)
            
            # No job available - check if we should wait
            if timeout_seconds is None:
                # Non-blocking mode
                return None
            
            # Calculate remaining wait time
            if deadline is not None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
            else:
                # timeout_seconds == 0 means wait indefinitely
                remaining = None
            
            # Clear event and wait for signal
            event.clear()
            try:
                await asyncio.wait_for(event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                return None
    
    async def acknowledge(self, job: Job) -> bool:
        """Acknowledge successful processing of a job.
        
        Removes the job from the processing set, preventing it from
        being redelivered after visibility timeout expires.
        
        Args:
            job: The job to acknowledge.
        
        Returns:
            True if the job was acknowledged, False if not found
            (e.g., already acknowledged or visibility timeout expired).
        """
        with self._lock:
            if job.id in self._processing[job.queue_name]:
                del self._processing[job.queue_name][job.id]
                logger.debug(
                    "Job acknowledged",
                    extra={"job_id": job.id, "queue_name": job.queue_name}
                )
                return True
        
        logger.debug(
            "Job not found for acknowledgment",
            extra={"job_id": job.id, "queue_name": job.queue_name}
        )
        return False
    
    async def reject(
        self,
        job: Job,
        requeue: bool = True,
    ) -> bool:
        """Reject a job, optionally requeueing it for retry.
        
        Removes the job from the processing set. If requeue is True and
        the job hasn't exceeded max_attempts, it is returned to the queue.
        
        Args:
            job: The job to reject.
            requeue: If True, the job is returned to the queue
                (if under max_attempts). If False, the job is discarded.
        
        Returns:
            True if the operation succeeded.
        """
        with self._lock:
            # Remove from processing set
            if job.id in self._processing[job.queue_name]:
                del self._processing[job.queue_name][job.id]
            
            if requeue and job.attempts < job.max_attempts:
                # Requeue the job
                self._queues[job.queue_name].append(job.to_json())
                event = self._get_event(job.queue_name)
                logger.debug(
                    "Job rejected and requeued",
                    extra={
                        "job_id": job.id,
                        "queue_name": job.queue_name,
                        "attempts": job.attempts,
                    }
                )
            else:
                event = None
                if requeue:
                    logger.warning(
                        "Job rejected and discarded (max attempts exceeded)",
                        extra={
                            "job_id": job.id,
                            "queue_name": job.queue_name,
                            "attempts": job.attempts,
                            "max_attempts": job.max_attempts,
                        }
                    )
                else:
                    logger.debug(
                        "Job rejected and discarded",
                        extra={"job_id": job.id, "queue_name": job.queue_name}
                    )
        
        # Signal waiting dequeue operations if job was requeued
        if event is not None:
            event.set()
        
        return True
    
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
        """
        with self._lock:
            # First restore any expired jobs and move scheduled jobs
            self._restore_expired_jobs(queue_name)
            self._move_scheduled_jobs(queue_name)
            
            jobs = []
            for job_json in self._queues[queue_name][:limit]:
                try:
                    jobs.append(Job.from_json(job_json))
                except JobSerializationError:
                    # Skip malformed jobs
                    continue
            
            return jobs
    
    async def queue_length(self, queue_name: str) -> int:
        """Get the number of jobs waiting in the queue.
        
        Returns the count of jobs that are ready to be dequeued. Does not
        include jobs that are currently being processed (in-flight).
        
        Args:
            queue_name: The queue to check.
        
        Returns:
            Number of jobs in the queue (not including processing jobs).
        """
        with self._lock:
            # First restore any expired jobs and move scheduled jobs
            self._restore_expired_jobs(queue_name)
            self._move_scheduled_jobs(queue_name)
            
            return len(self._queues[queue_name])
    
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
            ValueError: If delay_seconds is not positive.
        """
        if delay_seconds <= 0:
            raise ValueError("delay_seconds must be positive")
        
        try:
            job_json = job.to_json()
        except JobSerializationError as e:
            raise QueueOperationError(
                f"Failed to schedule job: {e.message}",
                details={"job_id": job.id, "error": str(e)}
            ) from e
        
        execute_at = time.time() + delay_seconds
        
        with self._lock:
            # Insert in sorted order by execute_at
            scheduled_list = self._scheduled[job.queue_name]
            insert_idx = 0
            for i, (ts, _) in enumerate(scheduled_list):
                if execute_at < ts:
                    break
                insert_idx = i + 1
            scheduled_list.insert(insert_idx, (execute_at, job_json))
        
        logger.debug(
            "Job scheduled",
            extra={
                "job_id": job.id,
                "queue_name": job.queue_name,
                "delay_seconds": delay_seconds,
            }
        )
        return True


# =============================================================================
# RedisQueueBackend Implementation
# =============================================================================


class RedisQueueBackend:
    """Redis-backed queue implementation for horizontally-scaled deployments.
    
    Uses Redis lists (LPUSH/BRPOP) for the main queue, enabling multiple
    worker replicas to compete for jobs. A processing sorted set tracks
    jobs that have been dequeued but not yet acknowledged, with scores
    representing visibility timeout expiration timestamps.
    
    Queue Structure:
        - queue:{name} - Redis list for pending jobs (FIFO)
        - queue:{name}:processing - Sorted set for in-flight jobs
          (score = visibility timeout expiration timestamp)
        - queue:{name}:scheduled - Sorted set for delayed jobs
          (score = execute_at timestamp)
        - queue:{name}:job:{id} - Hash storing job data while in processing
    
    This backend is suitable for:
        - Multi-node deployments with multiple worker replicas
        - Horizontal scaling with competing consumers
        - Production containerized environments
        - Deployments requiring job persistence across restarts
    
    Features:
        - BRPOP for efficient blocking wait without polling
        - Atomic operations prevent race conditions
        - Visibility timeout with automatic redelivery
        - Scheduled job support with sorted sets
    
    Thread Safety:
        Redis handles concurrency natively. Multiple workers can safely
        compete for jobs from the same queue.
    
    Example:
        # Preferred: Use the factory function
        from shu.core.queue_backend import get_queue_backend
        
        backend = await get_queue_backend()
        job = Job(queue_name="tasks", payload={"action": "process"})
        await backend.enqueue(job)
        
        # Worker dequeues and processes
        job = await backend.dequeue("tasks", timeout_seconds=5)
        if job:
            try:
                await process_job(job)
                await backend.acknowledge(job)
            except Exception:
                await backend.reject(job, requeue=True)
    
    Note:
        The Redis client is managed internally by this module. Use
        `get_queue_backend()` to obtain a properly configured backend
        instance rather than constructing RedisQueueBackend directly.
    """
    
    def __init__(self, redis_client: Any):
        """Initialize with an existing Redis client.
        
        Args:
            redis_client: An async Redis client instance. This is typically
                created internally by `get_queue_backend()`. External code
                should use the factory function instead of constructing
                this class directly.
        """
        self._client = redis_client
    
    def _queue_key(self, queue_name: str) -> str:
        """Get the Redis key for the main queue list."""
        return f"queue:{queue_name}"
    
    def _processing_key(self, queue_name: str) -> str:
        """Get the Redis key for the processing sorted set."""
        return f"queue:{queue_name}:processing"
    
    def _scheduled_key(self, queue_name: str) -> str:
        """Get the Redis key for the scheduled sorted set."""
        return f"queue:{queue_name}:scheduled"
    
    def _job_key(self, queue_name: str, job_id: str) -> str:
        """Get the Redis key for storing job data while in processing."""
        return f"queue:{queue_name}:job:{job_id}"
    
    async def _restore_expired_jobs(self, queue_name: str) -> int:
        """Move expired processing jobs back to the queue.
        
        Jobs that have exceeded their visibility timeout are restored
        to the front of the queue for reprocessing.
        
        Args:
            queue_name: The queue to check for expired jobs.
        
        Returns:
            Number of jobs restored.
        """
        now = time.time()
        processing_key = self._processing_key(queue_name)
        queue_key = self._queue_key(queue_name)
        
        try:
            # Get all expired jobs (score <= now)
            expired_job_ids = await self._client.zrangebyscore(
                processing_key,
                "-inf",
                now,
            )
            
            if not expired_job_ids:
                return 0
            
            restored_count = 0
            for job_id in expired_job_ids:
                # Get the job data
                job_key = self._job_key(queue_name, job_id)
                job_json = await self._client.get(job_key)
                
                if job_json:
                    # Use RPUSH to add to end (will be dequeued first with RPOP)
                    # This gives expired jobs priority reprocessing
                    await self._client.rpush(queue_key, job_json)
                    restored_count += 1
                    logger.debug(
                        "Restored expired job to queue",
                        extra={"job_id": job_id, "queue_name": queue_name}
                    )
                
                # Remove from processing set and delete job data
                await self._client.zrem(processing_key, job_id)
                await self._client.delete(job_key)
            
            return restored_count
            
        except Exception as e:
            logger.error(
                f"Failed to restore expired jobs: {e}",
                extra={"queue_name": queue_name, "error": str(e)}
            )
            return 0
    
    async def _move_scheduled_jobs(self, queue_name: str) -> int:
        """Move scheduled jobs that are ready to the main queue.
        
        Jobs whose execute_at timestamp has passed are moved from the
        scheduled sorted set to the main queue.
        
        Args:
            queue_name: The queue to check for ready scheduled jobs.
        
        Returns:
            Number of jobs moved.
        """
        now = time.time()
        scheduled_key = self._scheduled_key(queue_name)
        queue_key = self._queue_key(queue_name)
        
        try:
            # Get all ready jobs (score <= now)
            ready_jobs = await self._client.zrangebyscore(
                scheduled_key,
                "-inf",
                now,
            )
            
            if not ready_jobs:
                return 0
            
            moved_count = 0
            for job_json in ready_jobs:
                # Add to main queue
                await self._client.lpush(queue_key, job_json)
                # Remove from scheduled set
                await self._client.zrem(scheduled_key, job_json)
                moved_count += 1
                logger.debug(
                    "Moved scheduled job to queue",
                    extra={"queue_name": queue_name}
                )
            
            return moved_count
            
        except Exception as e:
            logger.error(
                f"Failed to move scheduled jobs: {e}",
                extra={"queue_name": queue_name, "error": str(e)}
            )
            return 0
    
    async def enqueue(self, job: Job) -> bool:
        """Add a job to the queue.
        
        Places the job at the end of the specified queue using LPUSH.
        Jobs are dequeued from the other end using BRPOP (FIFO order).
        
        Args:
            job: The job to enqueue.
        
        Returns:
            True if the job was successfully enqueued.
        
        Raises:
            QueueConnectionError: If the Redis server is unreachable.
            QueueOperationError: If the job cannot be serialized.
        """
        try:
            job_json = job.to_json()
        except JobSerializationError as e:
            raise QueueOperationError(
                f"Failed to enqueue job: {e.message}",
                details={"job_id": job.id, "error": str(e)}
            ) from e
        
        queue_key = self._queue_key(job.queue_name)
        
        try:
            await self._client.lpush(queue_key, job_json)
            logger.debug(
                "Job enqueued",
                extra={"job_id": job.id, "queue_name": job.queue_name}
            )
            return True
        except Exception as e:
            logger.error(
                f"Redis LPUSH failed for queue '{job.queue_name}': {e}",
                extra={"job_id": job.id, "queue_name": job.queue_name, "error": str(e)}
            )
            raise QueueConnectionError(
                f"Failed to enqueue job to queue '{job.queue_name}'",
                details={"job_id": job.id, "queue_name": job.queue_name, "error": str(e)}
            ) from e
    
    async def dequeue(
        self,
        queue_name: str,
        timeout_seconds: Optional[int] = None,
    ) -> Optional[Job]:
        """Remove and return the next job from the queue.
        
        Uses BRPOP for efficient blocking wait. When a job is dequeued,
        it is added to the processing sorted set with a score equal to
        the visibility timeout expiration timestamp.
        
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
            QueueConnectionError: If the Redis server is unreachable.
        """
        queue_key = self._queue_key(queue_name)
        processing_key = self._processing_key(queue_name)
        
        try:
            # First, restore any expired jobs and move scheduled jobs
            await self._restore_expired_jobs(queue_name)
            await self._move_scheduled_jobs(queue_name)
            
            # Dequeue using BRPOP or RPOP
            if timeout_seconds is None:
                # Non-blocking: use RPOP
                result = await self._client.rpop(queue_key)
            elif timeout_seconds == 0:
                # Block indefinitely: use BRPOP with 0 timeout
                result = await self._client.brpop(queue_key, timeout=0)
                if result:
                    result = result[1]  # BRPOP returns (key, value)
            else:
                # Block with timeout: use BRPOP
                result = await self._client.brpop(queue_key, timeout=timeout_seconds)
                if result:
                    result = result[1]  # BRPOP returns (key, value)
            
            if not result:
                return None
            
            # Parse the job
            try:
                job = Job.from_json(result)
            except JobSerializationError as e:
                logger.error(
                    f"Failed to deserialize job from queue: {e}",
                    extra={"queue_name": queue_name, "error": str(e)}
                )
                # Skip this malformed job
                return None
            
            # Increment attempts
            job.attempts += 1
            
            # Add to processing set with visibility timeout
            expiry = time.time() + job.visibility_timeout
            await self._client.zadd(processing_key, {job.id: expiry})
            
            # Store job data for potential redelivery
            job_key = self._job_key(queue_name, job.id)
            await self._client.set(
                job_key,
                job.to_json(),
                ex=job.visibility_timeout + 60  # Extra buffer for cleanup
            )
            
            logger.debug(
                "Job dequeued",
                extra={
                    "job_id": job.id,
                    "queue_name": queue_name,
                    "attempts": job.attempts,
                }
            )
            return job
            
        except Exception as e:
            logger.error(
                f"Redis dequeue failed for queue '{queue_name}': {e}",
                extra={"queue_name": queue_name, "error": str(e)}
            )
            raise QueueConnectionError(
                f"Failed to dequeue from queue '{queue_name}'",
                details={"queue_name": queue_name, "error": str(e)}
            ) from e
    
    async def acknowledge(self, job: Job) -> bool:
        """Acknowledge successful processing of a job.
        
        Removes the job from the processing set and deletes the stored
        job data, preventing it from being redelivered after visibility
        timeout expires.
        
        Args:
            job: The job to acknowledge.
        
        Returns:
            True if the job was acknowledged, False if not found
            (e.g., already acknowledged or visibility timeout expired).
        
        Raises:
            QueueConnectionError: If the Redis server is unreachable.
        """
        processing_key = self._processing_key(job.queue_name)
        job_key = self._job_key(job.queue_name, job.id)
        
        try:
            # Remove from processing set
            removed = await self._client.zrem(processing_key, job.id)
            
            # Delete stored job data
            await self._client.delete(job_key)
            
            if removed > 0:
                logger.debug(
                    "Job acknowledged",
                    extra={"job_id": job.id, "queue_name": job.queue_name}
                )
                return True
            
            logger.debug(
                "Job not found for acknowledgment",
                extra={"job_id": job.id, "queue_name": job.queue_name}
            )
            return False
            
        except Exception as e:
            logger.error(
                f"Redis acknowledge failed for job '{job.id}': {e}",
                extra={"job_id": job.id, "queue_name": job.queue_name, "error": str(e)}
            )
            raise QueueConnectionError(
                f"Failed to acknowledge job '{job.id}'",
                details={"job_id": job.id, "queue_name": job.queue_name, "error": str(e)}
            ) from e
    
    async def reject(
        self,
        job: Job,
        requeue: bool = True,
    ) -> bool:
        """Reject a job, optionally requeueing it for retry.
        
        Removes the job from the processing set. If requeue is True and
        the job hasn't exceeded max_attempts, it is returned to the queue.
        
        Args:
            job: The job to reject.
            requeue: If True, the job is returned to the queue
                (if under max_attempts). If False, the job is discarded.
        
        Returns:
            True if the operation succeeded.
        
        Raises:
            QueueConnectionError: If the Redis server is unreachable.
        """
        processing_key = self._processing_key(job.queue_name)
        job_key = self._job_key(job.queue_name, job.id)
        queue_key = self._queue_key(job.queue_name)
        
        try:
            # Remove from processing set
            await self._client.zrem(processing_key, job.id)
            
            # Delete stored job data
            await self._client.delete(job_key)
            
            if requeue and job.attempts < job.max_attempts:
                # Requeue the job
                await self._client.lpush(queue_key, job.to_json())
                logger.debug(
                    "Job rejected and requeued",
                    extra={
                        "job_id": job.id,
                        "queue_name": job.queue_name,
                        "attempts": job.attempts,
                    }
                )
            else:
                if requeue:
                    logger.warning(
                        "Job rejected and discarded (max attempts exceeded)",
                        extra={
                            "job_id": job.id,
                            "queue_name": job.queue_name,
                            "attempts": job.attempts,
                            "max_attempts": job.max_attempts,
                        }
                    )
                else:
                    logger.debug(
                        "Job rejected and discarded",
                        extra={"job_id": job.id, "queue_name": job.queue_name}
                    )
            
            return True
            
        except Exception as e:
            logger.error(
                f"Redis reject failed for job '{job.id}': {e}",
                extra={"job_id": job.id, "queue_name": job.queue_name, "error": str(e)}
            )
            raise QueueConnectionError(
                f"Failed to reject job '{job.id}'",
                details={"job_id": job.id, "queue_name": job.queue_name, "error": str(e)}
            ) from e
    
    async def peek(
        self,
        queue_name: str,
        limit: int = 10,
    ) -> List[Job]:
        """View jobs in the queue without removing them.
        
        Returns jobs from the front of the queue without affecting their
        state. Uses LRANGE to peek at the queue contents.
        
        Args:
            queue_name: The queue to peek.
            limit: Maximum number of jobs to return. Default is 10.
        
        Returns:
            List of jobs (may be empty if queue is empty).
        
        Raises:
            QueueConnectionError: If the Redis server is unreachable.
        """
        queue_key = self._queue_key(queue_name)
        
        try:
            # First restore any expired jobs and move scheduled jobs
            await self._restore_expired_jobs(queue_name)
            await self._move_scheduled_jobs(queue_name)
            
            # LRANGE returns elements from start to end (inclusive)
            # For FIFO queue with LPUSH/RPOP, newest items are at index 0
            # We want oldest items (at the end), so we use negative indices
            # -limit to -1 gives us the last 'limit' items
            job_jsons = await self._client.lrange(queue_key, -limit, -1)
            
            jobs = []
            for job_json in job_jsons:
                try:
                    jobs.append(Job.from_json(job_json))
                except JobSerializationError:
                    # Skip malformed jobs
                    continue
            
            return jobs
            
        except Exception as e:
            logger.error(
                f"Redis peek failed for queue '{queue_name}': {e}",
                extra={"queue_name": queue_name, "error": str(e)}
            )
            raise QueueConnectionError(
                f"Failed to peek queue '{queue_name}'",
                details={"queue_name": queue_name, "error": str(e)}
            ) from e
    
    async def queue_length(self, queue_name: str) -> int:
        """Get the number of jobs waiting in the queue.
        
        Returns the count of jobs that are ready to be dequeued. Does not
        include jobs that are currently being processed (in-flight).
        
        Args:
            queue_name: The queue to check.
        
        Returns:
            Number of jobs in the queue (not including processing jobs).
        
        Raises:
            QueueConnectionError: If the Redis server is unreachable.
        """
        queue_key = self._queue_key(queue_name)
        
        try:
            # First restore any expired jobs and move scheduled jobs
            await self._restore_expired_jobs(queue_name)
            await self._move_scheduled_jobs(queue_name)
            
            length = await self._client.llen(queue_key)
            return length
            
        except Exception as e:
            logger.error(
                f"Redis queue_length failed for queue '{queue_name}': {e}",
                extra={"queue_name": queue_name, "error": str(e)}
            )
            raise QueueConnectionError(
                f"Failed to get length of queue '{queue_name}'",
                details={"queue_name": queue_name, "error": str(e)}
            ) from e
    
    async def schedule(
        self,
        job: Job,
        delay_seconds: int,
    ) -> bool:
        """Schedule a job to be enqueued after a delay.
        
        The job is added to a scheduled sorted set with a score equal to
        the execute_at timestamp. A background process or the dequeue
        operation moves ready jobs to the main queue.
        
        Args:
            job: The job to schedule.
            delay_seconds: Seconds to wait before enqueueing. Must be
                positive.
        
        Returns:
            True if the job was scheduled.
        
        Raises:
            QueueConnectionError: If the Redis server is unreachable.
            ValueError: If delay_seconds is not positive.
        """
        if delay_seconds <= 0:
            raise ValueError("delay_seconds must be positive")
        
        try:
            job_json = job.to_json()
        except JobSerializationError as e:
            raise QueueOperationError(
                f"Failed to schedule job: {e.message}",
                details={"job_id": job.id, "error": str(e)}
            ) from e
        
        scheduled_key = self._scheduled_key(job.queue_name)
        execute_at = time.time() + delay_seconds
        
        try:
            await self._client.zadd(scheduled_key, {job_json: execute_at})
            logger.debug(
                "Job scheduled",
                extra={
                    "job_id": job.id,
                    "queue_name": job.queue_name,
                    "delay_seconds": delay_seconds,
                }
            )
            return True
            
        except Exception as e:
            logger.error(
                f"Redis schedule failed for job '{job.id}': {e}",
                extra={"job_id": job.id, "queue_name": job.queue_name, "error": str(e)}
            )
            raise QueueConnectionError(
                f"Failed to schedule job '{job.id}'",
                details={"job_id": job.id, "queue_name": job.queue_name, "error": str(e)}
            ) from e


# =============================================================================
# Global State for Singleton Pattern
# =============================================================================

# Global queue backend instance (singleton)
_queue_backend: Optional["QueueBackend"] = None


# =============================================================================
# Redis Client Management
# =============================================================================


async def _get_shared_redis_client() -> Any:
    """Get the shared Redis client from cache_backend.
    
    This reuses the same Redis client singleton that cache_backend uses,
    avoiding duplicate connections to Redis.
    
    Returns:
        An async Redis client instance.
        
    Raises:
        QueueConnectionError: If Redis connection fails.
    """
    try:
        from .cache_backend import _get_redis_client, CacheConnectionError
        return await _get_redis_client()
    except CacheConnectionError as e:
        # Convert CacheConnectionError to QueueConnectionError
        raise QueueConnectionError(
            f"Redis connection failed: {e.message}",
            details=e.details
        ) from e


# =============================================================================
# Queue Backend Factory
# =============================================================================


async def get_queue_backend() -> QueueBackend:
    """Get the configured queue backend (singleton).
    
    Selection logic:
    1. If SHU_REDIS_URL is set and Redis is reachable -> RedisQueueBackend
    2. If SHU_REDIS_URL is set but unreachable and fallback enabled -> InMemoryQueueBackend (with warning)
    3. If SHU_REDIS_URL is not set -> InMemoryQueueBackend
    
    This function is suitable for use in background tasks, schedulers, and
    other non-FastAPI code. For FastAPI endpoints, prefer using
    get_queue_backend_dependency() with Depends().
    
    Returns:
        The configured QueueBackend instance.
        
    Raises:
        QueueConnectionError: If Redis is required but unavailable.
    
    Example:
        backend = await get_queue_backend()
        job = Job(queue_name="tasks", payload={"action": "process"})
        await backend.enqueue(job)
    """
    global _queue_backend
    
    if _queue_backend is not None:
        return _queue_backend
    
    from .config import get_settings_instance
    
    settings = get_settings_instance()
    
    # Check if Redis URL is configured
    redis_url = settings.redis_url
    if not redis_url or redis_url == "redis://localhost:6379":
        # Check if this is a default/unconfigured value
        # If redis_required is False and no explicit URL, use in-memory
        if not settings.redis_required:
            logger.info("No Redis URL configured, using InMemoryQueueBackend")
            _queue_backend = InMemoryQueueBackend()
            return _queue_backend
    
    # Try to connect to Redis
    try:
        redis_client = await _get_shared_redis_client()
        _queue_backend = RedisQueueBackend(redis_client)
        logger.info("Using RedisQueueBackend")
        return _queue_backend
        
    except QueueConnectionError as e:
        if settings.redis_required:
            logger.error("Redis is required but connection failed", extra={
                "redis_url": settings.redis_url,
                "error": str(e)
            })
            raise QueueConnectionError(
                f"Redis is required but connection failed: {e}. "
                f"Please ensure Redis is running and accessible at {settings.redis_url}"
            ) from e
        
        if not settings.redis_fallback_enabled:
            logger.error("Redis fallback is disabled and Redis connection failed", extra={
                "redis_url": settings.redis_url,
                "error": str(e)
            })
            raise QueueConnectionError(
                f"Redis connection failed and fallback is disabled: {e}. "
                f"Please enable Redis fallback or ensure Redis is running at {settings.redis_url}"
            ) from e
        
        # Fall back to in-memory
        logger.warning(
            "Redis connection failed, falling back to InMemoryQueueBackend",
            extra={"redis_url": settings.redis_url, "error": str(e)}
        )
        _queue_backend = InMemoryQueueBackend()
        return _queue_backend


def get_queue_backend_dependency() -> QueueBackend:
    """Dependency injection function for QueueBackend.
    
    Use this in FastAPI endpoints for better testability and loose coupling.
    This follows the same pattern as get_cache_backend_dependency().
    
    Note: This returns a new InMemoryQueueBackend instance for each call
    when Redis is not available. For production use with Redis, the
    RedisQueueBackend wraps a shared Redis client.
    
    Example:
        from fastapi import Depends
        from shu.core.queue_backend import get_queue_backend_dependency, QueueBackend
        
        async def my_endpoint(
            queue: QueueBackend = Depends(get_queue_backend_dependency)
        ):
            job = Job(queue_name="tasks", payload={"action": "process"})
            await queue.enqueue(job)
    
    Returns:
        A QueueBackend instance.
    """
    # For dependency injection, we check if we already have a cached backend
    # This allows for easier testing and follows DEVELOPMENT_STANDARDS.md
    global _queue_backend
    
    if _queue_backend is not None:
        return _queue_backend
    
    # If no cached backend, return InMemoryQueueBackend
    # The async get_queue_backend() should be called during app startup
    # to initialize the proper backend
    logger.debug("get_queue_backend_dependency called before async initialization, using InMemoryQueueBackend")
    return InMemoryQueueBackend()


async def initialize_queue_backend() -> QueueBackend:
    """Initialize the queue backend during application startup.
    
    This should be called during FastAPI application startup to ensure
    the queue backend is properly initialized before handling requests.
    
    Example:
        @app.on_event("startup")
        async def startup():
            await initialize_queue_backend()
    
    Returns:
        The initialized QueueBackend instance.
    """
    return await get_queue_backend()


def reset_queue_backend() -> None:
    """Reset the queue backend singleton (for testing only).
    
    This function is intended for use in tests to reset the global state
    between test cases.
    
    Note: This does NOT reset the shared Redis client from cache_backend.
    Use cache_backend.reset_cache_backend() if you need to reset that too.
    """
    global _queue_backend
    _queue_backend = None
