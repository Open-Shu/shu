"""
Property-based tests for InMemoryQueueBackend synchronous processing.

These tests verify that InMemoryQueueBackend processes jobs synchronously
within the same execution context, providing backward compatibility for
single-node deployments and tests.
"""

import asyncio

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from shu.core.queue_backend import InMemoryQueueBackend, Job
from shu.core.workload_routing import WorkloadType, enqueue_job


class TestInMemoryQueueSynchronousProcessing:
    """
    Property 15: In-Memory Queue Synchronous Processing.

    For any pipeline execution using InMemoryQueueBackend, all stages SHALL
    complete synchronously within the same execution context (no async waiting
    required beyond the job handler execution time).

    **Validates: Requirements 7.1, 11.5**
    """

    @given(
        num_jobs=st.integers(min_value=1, max_value=10),
        payload_size=st.integers(min_value=1, max_value=100),
    )
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @pytest.mark.asyncio
    async def test_property_inmemory_queue_processes_jobs_synchronously(
        self, num_jobs: int, payload_size: int
    ):
        """
        Feature: queue-ingestion-pipeline
        Property 15: In-Memory Queue Synchronous Processing

        **Validates: Requirements 7.1, 11.5**

        This property verifies that when using InMemoryQueueBackend:
        1. Jobs are immediately available for dequeue after enqueue
        2. A worker can process all jobs without async waiting
        3. All jobs complete within the same execution context
        """
        backend = InMemoryQueueBackend(cleanup_interval_seconds=0)
        processed_jobs: list[Job] = []
        processing_order: list[int] = []

        async def job_handler(job: Job) -> None:
            """Simple handler that records processed jobs."""
            processed_jobs.append(job)
            processing_order.append(job.payload.get("order", -1))

        # Enqueue multiple jobs
        for i in range(num_jobs):
            payload = {"order": i, "data": "x" * payload_size}
            await enqueue_job(
                backend,
                WorkloadType.INGESTION,
                payload=payload,
            )

        # Verify jobs are immediately available (no async waiting needed)
        queue_name = WorkloadType.INGESTION.queue_name
        queue_length = await backend.queue_length(queue_name)
        assert queue_length == num_jobs, (
            f"Expected {num_jobs} jobs in queue, got {queue_length}"
        )

        # Process all jobs with a timeout
        async def run_worker_until_done():
            """Run worker until all jobs are processed."""
            while len(processed_jobs) < num_jobs:
                # Dequeue and process one job at a time
                job = await backend.dequeue(queue_name, timeout_seconds=0)
                if job:
                    await job_handler(job)
                    await backend.acknowledge(job)
                else:
                    # No more jobs available
                    break

        # Run with a timeout to prevent hanging
        try:
            await asyncio.wait_for(run_worker_until_done(), timeout=5.0)
        except TimeoutError:
            pytest.fail(
                f"Worker timed out processing jobs. "
                f"Processed {len(processed_jobs)}/{num_jobs} jobs."
            )

        # Property assertions
        assert len(processed_jobs) == num_jobs, (
            f"Expected {num_jobs} processed jobs, got {len(processed_jobs)}"
        )

        # Verify FIFO order is preserved
        assert processing_order == list(range(num_jobs)), (
            f"Jobs processed out of order: {processing_order}"
        )

        # Verify queue is empty after processing
        final_queue_length = await backend.queue_length(queue_name)
        assert final_queue_length == 0, (
            f"Queue should be empty after processing, got {final_queue_length}"
        )

    @given(
        workload_type=st.sampled_from([
            WorkloadType.INGESTION,
            WorkloadType.INGESTION_OCR,
            WorkloadType.INGESTION_EMBED,
            WorkloadType.PROFILING,
        ]),
    )
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @pytest.mark.asyncio
    async def test_property_inmemory_queue_immediate_availability(
        self, workload_type: WorkloadType
    ):
        """
        Feature: queue-ingestion-pipeline
        Property 15: In-Memory Queue Synchronous Processing

        **Validates: Requirements 7.1, 11.5**

        This property verifies that jobs enqueued to InMemoryQueueBackend
        are immediately available for dequeue without any async waiting.
        """
        backend = InMemoryQueueBackend(cleanup_interval_seconds=0)

        # Enqueue a job
        job = await enqueue_job(
            backend,
            workload_type,
            payload={"action": "test", "workload": workload_type.value},
        )

        # Job should be immediately available (non-blocking dequeue)
        dequeued_job = await backend.dequeue(
            workload_type.queue_name,
            timeout_seconds=None,  # Non-blocking
        )

        # Property assertions
        assert dequeued_job is not None, (
            f"Job should be immediately available for {workload_type.value}"
        )
        assert dequeued_job.id == job.id, (
            f"Dequeued job ID mismatch: expected {job.id}, got {dequeued_job.id}"
        )
        assert dequeued_job.payload == job.payload, (
            "Dequeued job payload mismatch"
        )

    @pytest.mark.asyncio
    async def test_inmemory_queue_no_network_latency(self):
        """
        Unit test: InMemoryQueueBackend operations complete without network latency.

        This test verifies that enqueue and dequeue operations complete
        quickly, demonstrating synchronous in-memory behavior.
        """
        import time

        backend = InMemoryQueueBackend(cleanup_interval_seconds=0)
        queue_name = "latency_test"

        # Measure enqueue time
        start = time.perf_counter()
        job = Job(queue_name=queue_name, payload={"test": "data"})
        await backend.enqueue(job)
        enqueue_time = time.perf_counter() - start

        # Measure dequeue time
        start = time.perf_counter()
        dequeued = await backend.dequeue(queue_name, timeout_seconds=None)
        dequeue_time = time.perf_counter() - start

        # Sanity threshold: operations should complete in under 100ms.
        # This is a relaxed threshold to avoid CI flakiness; actual times
        # are typically microseconds on local machines.
        assert enqueue_time < 0.1, (
            f"Enqueue took {enqueue_time*1000:.3f}ms, expected < 100ms"
        )
        assert dequeue_time < 0.1, (
            f"Dequeue took {dequeue_time*1000:.3f}ms, expected < 100ms"
        )
        assert dequeued is not None
        assert dequeued.id == job.id

    @pytest.mark.asyncio
    async def test_inmemory_queue_pipeline_stages_complete_synchronously(self):
        """
        Unit test: Pipeline stages complete synchronously with InMemoryQueueBackend.

        This test simulates a multi-stage pipeline (OCR → EMBED → PROFILING)
        and verifies all stages complete within the same execution context.
        """
        backend = InMemoryQueueBackend(cleanup_interval_seconds=0)
        stage_completions: list[str] = []

        async def ocr_handler(job: Job) -> None:
            """Simulate OCR stage that enqueues embed job."""
            stage_completions.append("ocr")
            # Enqueue next stage
            await enqueue_job(
                backend,
                WorkloadType.INGESTION_EMBED,
                payload={"document_id": job.payload["document_id"]},
            )

        async def embed_handler(job: Job) -> None:
            """Simulate embed stage that enqueues profiling job."""
            stage_completions.append("embed")
            # Enqueue next stage
            await enqueue_job(
                backend,
                WorkloadType.PROFILING,
                payload={"document_id": job.payload["document_id"]},
            )

        async def profiling_handler(job: Job) -> None:
            """Simulate profiling stage (final stage)."""
            stage_completions.append("profiling")

        # Start the pipeline by enqueuing OCR job
        await enqueue_job(
            backend,
            WorkloadType.INGESTION_OCR,
            payload={"document_id": "test-doc-123"},
        )

        # Process all stages synchronously
        handlers = {
            WorkloadType.INGESTION_OCR.queue_name: ocr_handler,
            WorkloadType.INGESTION_EMBED.queue_name: embed_handler,
            WorkloadType.PROFILING.queue_name: profiling_handler,
        }

        # Process jobs until all stages complete
        max_iterations = 10
        iteration = 0
        while len(stage_completions) < 3 and iteration < max_iterations:
            iteration += 1
            for queue_name, handler in handlers.items():
                job = await backend.dequeue(queue_name, timeout_seconds=None)
                if job:
                    await handler(job)
                    await backend.acknowledge(job)

        # Verify all stages completed in order
        assert stage_completions == ["ocr", "embed", "profiling"], (
            f"Pipeline stages did not complete in order: {stage_completions}"
        )

        # Verify all queues are empty
        for queue_name in handlers:
            length = await backend.queue_length(queue_name)
            assert length == 0, f"Queue {queue_name} should be empty"
