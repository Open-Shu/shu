"""
Integration tests for Queue Backend migrations (SHU-211).

Tests cover:
- Document profiling jobs are enqueued and can be processed
- Scheduler jobs are enqueued and can be processed
- Jobs persist in queue and can be retrieved
- Idempotency guards are preserved
"""
import asyncio
from typing import Any, Dict

import pytest
from sqlalchemy import select

from shu.core.queue_backend import get_queue_backend, reset_queue_backend, Job
from shu.core.workload_routing import WorkloadType, get_queue_name
from shu.models.document import Document
from shu.models.plugin_feed import PluginFeed
from shu.models.plugin_execution import PluginExecution, PluginExecutionStatus
from shu.services.ingestion_service import ingest_text
from shu.services.plugins_scheduler_service import PluginsSchedulerService
from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script


async def test_profiling_job_enqueued(client, db, auth_headers):
    """Test that document profiling jobs are enqueued to the queue."""
    from shu.core.config import get_settings_instance
    
    settings = get_settings_instance()
    original_profiling_enabled = settings.enable_document_profiling
    
    try:
        # Enable profiling
        settings.enable_document_profiling = True
        
        # Reset queue backend to ensure clean state
        reset_queue_backend()
        
        # Create a knowledge base
        kb_response = await client.post(
            "/api/v1/knowledge-bases",
            json={"name": "Test KB for Profiling", "description": "Test"},
            headers=auth_headers,
        )
        assert kb_response.status_code == 201
        kb_id = kb_response.json()["data"]["id"]
        
        # Ingest a document (should trigger profiling job enqueue)
        result = await ingest_text(
            db,
            kb_id,
            plugin_name="test_plugin",
            user_id="test_user",
            title="Test Document",
            content="This is a test document for profiling.",
            source_id="test_doc_1",
        )
        
        assert result["document_id"] is not None
        document_id = result["document_id"]
        
        # Check that a profiling job was enqueued
        backend = await get_queue_backend()
        queue_name = get_queue_name(WorkloadType.PROFILING)
        
        # Peek at the queue to see if job is there
        jobs = await backend.peek(queue_name, limit=10)
        
        # Find the job for our document
        profiling_job = None
        for job in jobs:
            if job.payload.get("document_id") == document_id:
                profiling_job = job
                break
        
        assert profiling_job is not None, "Profiling job should be enqueued"
        assert profiling_job.payload["action"] == "profile_document"
        assert profiling_job.payload["document_id"] == document_id
        assert profiling_job.max_attempts == 5
        assert profiling_job.visibility_timeout == 600
        
    finally:
        # Restore original setting
        settings.enable_document_profiling = original_profiling_enabled
        reset_queue_backend()


async def test_profiling_job_can_be_dequeued(client, db, auth_headers):
    """Test that profiling jobs can be dequeued and processed."""
    from shu.core.config import get_settings_instance
    
    settings = get_settings_instance()
    original_profiling_enabled = settings.enable_document_profiling
    
    try:
        # Enable profiling
        settings.enable_document_profiling = True
        
        # Reset queue backend
        reset_queue_backend()
        
        # Create a knowledge base
        kb_response = await client.post(
            "/api/v1/knowledge-bases",
            json={"name": "Test KB for Dequeue", "description": "Test"},
            headers=auth_headers,
        )
        assert kb_response.status_code == 201
        kb_id = kb_response.json()["data"]["id"]
        
        # Ingest a document
        result = await ingest_text(
            db,
            kb_id,
            plugin_name="test_plugin",
            user_id="test_user",
            title="Test Document",
            content="This is a test document.",
            source_id="test_doc_2",
        )
        
        document_id = result["document_id"]
        
        # Dequeue the job
        backend = await get_queue_backend()
        queue_name = get_queue_name(WorkloadType.PROFILING)
        
        job = await backend.dequeue(queue_name, timeout_seconds=1)
        
        assert job is not None, "Should be able to dequeue profiling job"
        assert job.payload["document_id"] == document_id
        assert job.attempts == 1  # Should be incremented after dequeue
        
        # Acknowledge the job
        ack_result = await backend.acknowledge(job)
        assert ack_result is True
        
        # Verify job is no longer in queue
        job2 = await backend.dequeue(queue_name, timeout_seconds=1)
        assert job2 is None, "Job should not be redelivered after acknowledgment"
        
    finally:
        settings.enable_document_profiling = original_profiling_enabled
        reset_queue_backend()


async def test_scheduler_jobs_enqueued(client, db, auth_headers):
    """Test that scheduler jobs are enqueued to the queue."""
    # Reset queue backend
    reset_queue_backend()
    
    # Enable test_schema plugin
    await client.post(
        "/api/v1/plugins/admin/enable",
        json={"plugin_name": "test_schema"},
        headers=auth_headers,
    )
    
    # Create a feed that's due to run
    feed_response = await client.post(
        "/api/v1/plugins/admin/feeds",
        json={
            "name": "Test Queue Feed",
            "plugin_name": "test_schema",
            "params": {"q": "test"},
            "interval_seconds": 3600,
            "enabled": True,
            "next_run_at": None,  # Due immediately
        },
        headers=auth_headers,
    )
    assert feed_response.status_code == 201
    feed_id = feed_response.json()["data"]["id"]
    
    # Run the scheduler to enqueue due schedules
    svc = PluginsSchedulerService(db)
    result = await svc.enqueue_due_schedules(limit=10)
    
    assert result["due"] >= 1
    assert result["enqueued"] >= 1
    assert result["queue_enqueued"] >= 1, "Jobs should be enqueued to queue"
    
    # Check that a job was enqueued to the queue
    backend = await get_queue_backend()
    queue_name = get_queue_name(WorkloadType.MAINTENANCE)
    
    jobs = await backend.peek(queue_name, limit=10)
    
    # Find the job for our feed
    scheduler_job = None
    for job in jobs:
        if job.payload.get("schedule_id") == feed_id:
            scheduler_job = job
            break
    
    assert scheduler_job is not None, "Scheduler job should be enqueued"
    assert scheduler_job.payload["plugin_name"] == "test_schema"
    assert scheduler_job.payload["schedule_id"] == feed_id
    assert scheduler_job.max_attempts == 3
    assert scheduler_job.visibility_timeout == 3600
    
    # Cleanup
    reset_queue_backend()


async def test_scheduler_idempotency_preserved(client, db, auth_headers):
    """Test that scheduler idempotency guards are preserved with queue backend."""
    # Reset queue backend
    reset_queue_backend()
    
    # Enable test_schema plugin
    await client.post(
        "/api/v1/plugins/admin/enable",
        json={"plugin_name": "test_schema"},
        headers=auth_headers,
    )
    
    # Create a feed
    feed_response = await client.post(
        "/api/v1/plugins/admin/feeds",
        json={
            "name": "Test Idempotency Feed",
            "plugin_name": "test_schema",
            "params": {"q": "test"},
            "interval_seconds": 3600,
            "enabled": True,
            "next_run_at": None,
        },
        headers=auth_headers,
    )
    assert feed_response.status_code == 201
    feed_id = feed_response.json()["data"]["id"]
    
    # Run the scheduler twice
    svc = PluginsSchedulerService(db)
    result1 = await svc.enqueue_due_schedules(limit=10)
    result2 = await svc.enqueue_due_schedules(limit=10)
    
    # First run should enqueue
    assert result1["enqueued"] >= 1
    
    # Second run should skip (idempotency guard)
    assert result2["skipped_already_enqueued"] >= 1, "Should skip already enqueued jobs"
    
    # Check that only one execution record exists
    exec_query = select(PluginExecution).where(
        PluginExecution.schedule_id == feed_id
    )
    exec_result = await db.execute(exec_query)
    executions = list(exec_result.scalars().all())
    
    assert len(executions) == 1, "Should only create one execution record"
    
    # Check that only one job is in the queue
    backend = await get_queue_backend()
    queue_name = get_queue_name(WorkloadType.MAINTENANCE)
    queue_length = await backend.queue_length(queue_name)
    
    # Count jobs for this feed
    jobs = await backend.peek(queue_name, limit=100)
    feed_jobs = [j for j in jobs if j.payload.get("schedule_id") == feed_id]
    
    assert len(feed_jobs) == 1, "Should only have one job in queue for this feed"
    
    # Cleanup
    reset_queue_backend()


async def test_scheduler_job_can_be_dequeued(client, db, auth_headers):
    """Test that scheduler jobs can be dequeued and contain correct payload."""
    # Reset queue backend
    reset_queue_backend()
    
    # Enable test_schema plugin
    await client.post(
        "/api/v1/plugins/admin/enable",
        json={"plugin_name": "test_schema"},
        headers=auth_headers,
    )
    
    # Create a feed
    feed_response = await client.post(
        "/api/v1/plugins/admin/feeds",
        json={
            "name": "Test Dequeue Feed",
            "plugin_name": "test_schema",
            "params": {"q": "dequeue_test"},
            "interval_seconds": 3600,
            "enabled": True,
            "next_run_at": None,
        },
        headers=auth_headers,
    )
    assert feed_response.status_code == 201
    feed_id = feed_response.json()["data"]["id"]
    
    # Enqueue the job
    svc = PluginsSchedulerService(db)
    result = await svc.enqueue_due_schedules(limit=10)
    assert result["enqueued"] >= 1
    
    # Dequeue the job
    backend = await get_queue_backend()
    queue_name = get_queue_name(WorkloadType.MAINTENANCE)
    
    job = await backend.dequeue(queue_name, timeout_seconds=1)
    
    assert job is not None, "Should be able to dequeue scheduler job"
    assert job.payload["schedule_id"] == feed_id
    assert job.payload["plugin_name"] == "test_schema"
    assert job.payload["params"]["q"] == "dequeue_test"
    assert "execution_id" in job.payload
    assert job.attempts == 1
    
    # Verify execution record exists and is PENDING
    exec_id = job.payload["execution_id"]
    exec_query = select(PluginExecution).where(PluginExecution.id == exec_id)
    exec_result = await db.execute(exec_query)
    execution = exec_result.scalar_one()
    
    assert execution.status == PluginExecutionStatus.PENDING
    assert execution.schedule_id == feed_id
    
    # Acknowledge the job
    await backend.acknowledge(job)
    
    # Cleanup
    reset_queue_backend()


class QueueMigrationsIntegrationSuite(BaseIntegrationTestSuite):
    """Integration test suite for queue backend migrations."""
    
    @classmethod
    def get_test_functions(cls):
        return [
            test_profiling_job_enqueued,
            test_profiling_job_can_be_dequeued,
            test_scheduler_jobs_enqueued,
            test_scheduler_idempotency_preserved,
            test_scheduler_job_can_be_dequeued,
        ]
    
    def get_suite_description(self) -> str:
        return "Integration tests for queue backend migrations (profiling and scheduler)"


# Allow running this file directly
if __name__ == "__main__":
    create_test_runner_script(QueueMigrationsIntegrationSuite, globals())
