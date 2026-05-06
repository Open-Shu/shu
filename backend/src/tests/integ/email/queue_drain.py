"""Test utility for draining the EMAIL workload queue inline.

`EmailService.send` always queues — there is no public sync send method on
the service. Tests that need deterministic dispatch (verification flow,
password reset, integration tests for SHU-507 / SHU-745) call
`process_email_queue_now()` after enqueueing to run the worker handler
synchronously instead of waiting for a real worker process.

This is a test-only helper. Production dispatch happens via the worker
process registered in `shu/worker.py`. See SHU-508 Phase C.
"""

from __future__ import annotations

import logging

from shu.core.queue_backend import QueueBackend
from shu.core.workload_routing import WorkloadType
from shu.email_handler import handle_email_job

logger = logging.getLogger(__name__)


async def process_email_queue_now(
    queue: QueueBackend,
    *,
    max_jobs: int = 100,
) -> int:
    """Drain the EMAIL queue, calling the production handler for each job.

    Loops until the queue is empty or `max_jobs` have been processed
    (defensive cap so a misbehaving test cannot spin forever). Each job
    is acknowledged on success, rejected without requeue on failure —
    tests want failures to surface immediately, not retry-loop.

    Returns the number of jobs processed.
    """
    queue_name = WorkloadType.EMAIL.queue_name
    processed = 0
    drained_to_empty = False

    for _ in range(max_jobs):
        job = await queue.dequeue(queue_name)
        if job is None:
            drained_to_empty = True
            break

        try:
            await handle_email_job(job)
        except Exception:
            # Test mode: do not requeue. Re-raise so the test fails loudly
            # with the actual handler exception rather than seeing a "job
            # disappeared" mystery.
            await queue.reject(job, requeue=False)
            raise

        await queue.acknowledge(job)
        processed += 1

    # Only error when the cap was hit AND the queue still had jobs left;
    # a legitimate run of exactly max_jobs jobs that drained to empty
    # should NOT raise. Peek once more to disambiguate.
    if not drained_to_empty:
        next_job = await queue.dequeue(queue_name)
        if next_job is not None:
            # Put it back-ish: reject without-requeue so the test fails on
            # the cap rather than dropping a real job.
            await queue.reject(next_job, requeue=False)
            raise RuntimeError(
                f"process_email_queue_now hit the {max_jobs}-job safety cap "
                "and the queue still had jobs left. A handler is likely "
                "re-enqueueing jobs."
            )

    return processed
