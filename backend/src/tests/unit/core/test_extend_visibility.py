"""Unit tests for QueueBackend.extend_visibility.

Covers InMemoryQueueBackend behaviour:
- Successful extension returns True and advances the expiry.
- Extension on an already-acked job returns False.
- Extension on an expired (and restored) job returns False.

Redis behaviour is tested via mocks to avoid a live Redis dependency.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.core.queue_backend import InMemoryQueueBackend, Job, RedisQueueBackend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job(queue_name: str = "test_queue") -> Job:
    return Job(queue_name=queue_name, payload={"action": "test"})


# ---------------------------------------------------------------------------
# InMemoryQueueBackend tests
# ---------------------------------------------------------------------------


class TestInMemoryExtendVisibility:
    @pytest.mark.asyncio
    async def test_extend_returns_true_for_inflight_job(self):
        """extend_visibility returns True and pushes the expiry forward."""
        backend = InMemoryQueueBackend()
        job = _make_job()
        await backend.enqueue(job)
        dequeued = await backend.dequeue(job.queue_name)
        assert dequeued is not None

        before = backend._processing[job.queue_name][dequeued.id][1]
        result = await backend.extend_visibility(dequeued, additional_seconds=120)

        assert result is True
        after = backend._processing[job.queue_name][dequeued.id][1]
        assert after > before

    @pytest.mark.asyncio
    async def test_extend_returns_false_after_acknowledge(self):
        """extend_visibility returns False once the job has been acknowledged."""
        backend = InMemoryQueueBackend()
        job = _make_job()
        await backend.enqueue(job)
        dequeued = await backend.dequeue(job.queue_name)
        assert dequeued is not None

        await backend.acknowledge(dequeued)
        result = await backend.extend_visibility(dequeued, additional_seconds=120)

        assert result is False

    @pytest.mark.asyncio
    async def test_extend_returns_false_for_expired_job(self):
        """extend_visibility returns False when the job has already expired
        and been restored to the queue (no longer in the processing set)."""
        backend = InMemoryQueueBackend(cleanup_interval_seconds=0)
        job = _make_job()
        await backend.enqueue(job)
        dequeued = await backend.dequeue(job.queue_name)
        assert dequeued is not None

        # Manually expire the job by backdating its expiry timestamp
        with backend._lock:
            job_json, _ = backend._processing[job.queue_name][dequeued.id]
            backend._processing[job.queue_name][dequeued.id] = (job_json, time.time() - 1)

        # Trigger expiry restoration without re-dequeueing the restored job
        with backend._lock:
            backend._restore_expired_jobs(job.queue_name)

        # Job is no longer in the processing set
        result = await backend.extend_visibility(dequeued, additional_seconds=120)
        assert result is False

    @pytest.mark.asyncio
    async def test_extend_new_expiry_is_at_least_additional_seconds_from_now(self):
        """The new expiry is at least `additional_seconds` from now."""
        backend = InMemoryQueueBackend()
        job = _make_job()
        await backend.enqueue(job)
        dequeued = await backend.dequeue(job.queue_name)
        assert dequeued is not None

        additional = 300
        before_call = time.time()
        await backend.extend_visibility(dequeued, additional_seconds=additional)
        new_expiry = backend._processing[job.queue_name][dequeued.id][1]

        assert new_expiry >= before_call + additional


# ---------------------------------------------------------------------------
# RedisQueueBackend tests (mocked)
# ---------------------------------------------------------------------------


class TestRedisExtendVisibility:
    def _make_backend(self):
        client = MagicMock()
        client.zadd = AsyncMock(return_value=1)
        client.expire = AsyncMock(return_value=True)
        return RedisQueueBackend(redis_client=client), client

    @pytest.mark.asyncio
    async def test_extend_returns_true_when_job_found(self):
        """Returns True when zadd with xx=True reports an update."""
        backend, client = self._make_backend()
        job = _make_job()

        result = await backend.extend_visibility(job, additional_seconds=120)

        assert result is True
        client.zadd.assert_awaited_once()
        # Verify xx=True was passed
        _, kwargs = client.zadd.call_args
        assert kwargs.get("xx") is True
        client.expire.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_extend_returns_false_when_job_not_found(self):
        """Returns False when zadd with xx=True reports no update (job gone)."""
        backend, client = self._make_backend()
        client.zadd = AsyncMock(return_value=0)
        job = _make_job()

        result = await backend.extend_visibility(job, additional_seconds=120)

        assert result is False
        # expire should NOT be called when the job wasn't found
        client.expire.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_extend_raises_on_redis_error(self):
        """Propagates QueueConnectionError when Redis raises."""
        from shu.core.queue_backend import QueueConnectionError

        backend, client = self._make_backend()
        client.zadd = AsyncMock(side_effect=Exception("connection refused"))
        job = _make_job()

        with pytest.raises(QueueConnectionError):
            await backend.extend_visibility(job, additional_seconds=120)
