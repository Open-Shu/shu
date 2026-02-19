"""Unit tests for the plugin execution heartbeat and stale-execution cleanup.

Covers:
- Heartbeat cancellation on successful execution
- Heartbeat cancellation when execution raises an exception
- cleanup_stale_executions uses updated_at (not started_at) for the cutoff
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rec(status="running", started_at=None, updated_at=None):
    rec = MagicMock()
    rec.id = "exec-1"
    rec.plugin_name = "test_plugin"
    rec.status = status
    rec.started_at = started_at or datetime.now(UTC) - timedelta(minutes=10)
    rec.updated_at = updated_at or datetime.now(UTC) - timedelta(minutes=10)
    rec.error = None
    rec.completed_at = None
    return rec


def _make_job(attempts=1, max_attempts=3):
    job = MagicMock()
    job.id = "job-1"
    job.payload = {"execution_id": "exec-1", "plugin_name": "test_plugin"}
    job.attempts = attempts
    job.max_attempts = max_attempts
    return job


def _make_session(rec):
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=rec))
    )
    mock_session.commit = AsyncMock()
    return mock_session


# ---------------------------------------------------------------------------
# Heartbeat cancellation tests
# ---------------------------------------------------------------------------

class TestHeartbeatCancellation:
    """Verify the heartbeat asyncio.Task is always cancelled after execution."""

    @pytest.mark.asyncio
    async def test_heartbeat_cancelled_on_success(self):
        """Heartbeat task is done (cancelled) after execute_plugin_record succeeds."""
        created_tasks = []

        async def fake_execute(session, rec, settings):
            rec.status = "completed"

        rec = _make_rec(status="pending")
        job = _make_job()
        mock_session = _make_session(rec)

        with (
            patch("shu.core.database.get_async_session_local", return_value=lambda: mock_session),
            patch("shu.services.plugin_execution_runner.execute_plugin_record", new=fake_execute),
            patch("shu.core.config.get_settings_instance", return_value=MagicMock()),
        ):
            from shu import worker as worker_mod

            real_create_task = asyncio.create_task

            def capturing_create_task(coro, **kwargs):
                t = real_create_task(coro, **kwargs)
                created_tasks.append(t)
                return t

            with patch.object(asyncio, "create_task", side_effect=capturing_create_task):
                await worker_mod._handle_plugin_execution_job(job)

        assert created_tasks, "No asyncio task was created (heartbeat missing)"
        heartbeat_task = created_tasks[0]
        assert heartbeat_task.done(), "Heartbeat task was not done after handler returned"
        assert heartbeat_task.cancelled(), "Heartbeat task was not cancelled on success"

    @pytest.mark.asyncio
    async def test_heartbeat_cancelled_on_exception(self):
        """Heartbeat task is cancelled even when execute_plugin_record raises."""
        created_tasks = []

        async def fake_execute_raises(session, rec, settings):
            raise RuntimeError("plugin exploded")

        rec = _make_rec(status="pending")
        job = _make_job(attempts=3, max_attempts=3)
        mock_session = _make_session(rec)

        with (
            patch("shu.core.database.get_async_session_local", return_value=lambda: mock_session),
            patch("shu.services.plugin_execution_runner.execute_plugin_record", new=fake_execute_raises),
            patch("shu.core.config.get_settings_instance", return_value=MagicMock()),
        ):
            from shu import worker as worker_mod

            real_create_task = asyncio.create_task

            def capturing_create_task(coro, **kwargs):
                t = real_create_task(coro, **kwargs)
                created_tasks.append(t)
                return t

            with patch.object(asyncio, "create_task", side_effect=capturing_create_task):
                with pytest.raises(RuntimeError, match="plugin exploded"):
                    await worker_mod._handle_plugin_execution_job(job)

        assert created_tasks, "No asyncio task was created (heartbeat missing)"
        heartbeat_task = created_tasks[0]
        assert heartbeat_task.done(), "Heartbeat task was not done after handler returned"
        assert heartbeat_task.cancelled(), "Heartbeat task was not cancelled after exception"


# ---------------------------------------------------------------------------
# cleanup_stale_executions uses updated_at
# ---------------------------------------------------------------------------

class TestCleanupStaleUsesUpdatedAt:
    """cleanup_stale_executions must use updated_at as the stale cutoff."""

    @pytest.mark.asyncio
    async def test_stale_query_cutoff_is_updated_at(self):
        """The WHERE clause cutoff in cleanup_stale_executions uses updated_at <=."""
        from shu.services.plugins_scheduler_service import PluginsSchedulerService

        captured_sql = []

        class CapturingResult:
            def scalars(self):
                return MagicMock(all=MagicMock(return_value=[]))

        async def capturing_execute(query, *args, **kwargs):
            try:
                from sqlalchemy.dialects import postgresql
                sql = str(
                    query.compile(
                        dialect=postgresql.dialect(),
                        compile_kwargs={"literal_binds": True},
                    )
                )
                captured_sql.append(sql)
            except Exception:
                captured_sql.append(str(query))
            return CapturingResult()

        mock_db = AsyncMock()
        mock_db.execute = capturing_execute
        mock_db.commit = AsyncMock()

        settings = MagicMock()
        settings.plugins_scheduler_running_timeout_seconds = 300

        with patch("shu.services.plugins_scheduler_service.get_settings_instance", return_value=settings):
            svc = PluginsSchedulerService(mock_db)
            await svc.cleanup_stale_executions()

        assert captured_sql, "No query was executed"
        sql = captured_sql[0].lower()
        assert "updated_at <=" in sql, (
            f"Expected 'updated_at <=' as the stale cutoff, got:\n{sql}"
        )

    @pytest.mark.asyncio
    async def test_fresh_heartbeat_not_marked_stale(self):
        """Query returns no rows when updated_at is recent — nothing marked stale."""
        from shu.services.plugins_scheduler_service import PluginsSchedulerService

        class EmptyResult:
            def scalars(self):
                return MagicMock(all=MagicMock(return_value=[]))

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=EmptyResult())
        mock_db.commit = AsyncMock()

        settings = MagicMock()
        settings.plugins_scheduler_running_timeout_seconds = 300

        with patch("shu.services.plugins_scheduler_service.get_settings_instance", return_value=settings):
            svc = PluginsSchedulerService(mock_db)
            count = await svc.cleanup_stale_executions()

        assert count == 0

    @pytest.mark.asyncio
    async def test_silent_worker_marked_stale(self):
        """A record returned by the query (updated_at expired) is marked FAILED."""
        from shu.models.plugin_execution import PluginExecutionStatus
        from shu.services.plugins_scheduler_service import PluginsSchedulerService

        rec = _make_rec(
            status=PluginExecutionStatus.RUNNING,
            started_at=datetime.now(UTC) - timedelta(hours=2),
            updated_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        rec.status = PluginExecutionStatus.RUNNING

        class StaleResult:
            def scalars(self):
                return MagicMock(all=MagicMock(return_value=[rec]))

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=StaleResult())
        mock_db.commit = AsyncMock()

        settings = MagicMock()
        settings.plugins_scheduler_running_timeout_seconds = 300

        with patch("shu.services.plugins_scheduler_service.get_settings_instance", return_value=settings):
            svc = PluginsSchedulerService(mock_db)
            count = await svc.cleanup_stale_executions()

        assert count == 1
        assert rec.status == PluginExecutionStatus.FAILED
        assert rec.error == "stale_timeout"
