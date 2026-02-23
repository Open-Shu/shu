"""
Unit tests for the unified SchedulerService.

Tests cover:
- PluginFeedSource delegates to PluginsSchedulerService
- ExperienceSource queries due experiences with locking
- ExperienceSource fans out one job per user per experience
- ExperienceSource advances schedule after enqueue
- ExperienceSource handles no-users case
- AttachmentCleanupSource delegates to AttachmentCleanupService
- UnifiedSchedulerService.tick() iterates all sources
- Source errors don't block other sources
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.services.scheduler_service import (
    AttachmentCleanupSource,
    ExperienceSource,
    PluginFeedSource,
    UnifiedSchedulerService,
)


class TestPluginFeedSource:
    """Tests for PluginFeedSource delegation."""

    def test_name(self):
        source = PluginFeedSource()
        assert source.name == "plugin_feeds"

    @pytest.mark.asyncio
    @patch("shu.services.plugins_scheduler_service.PluginsSchedulerService")
    async def test_cleanup_stale_delegates(self, mock_svc_class):
        mock_svc = MagicMock()
        mock_svc.cleanup_stale_executions = AsyncMock(return_value=3)
        mock_svc_class.return_value = mock_svc

        source = PluginFeedSource()
        db = AsyncMock()
        result = await source.cleanup_stale(db)

        assert result == 3
        mock_svc.cleanup_stale_executions.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("shu.services.plugins_scheduler_service.PluginsSchedulerService")
    async def test_enqueue_due_delegates(self, mock_svc_class):
        mock_svc = MagicMock()
        mock_svc.enqueue_due_schedules = AsyncMock(
            return_value={"due": 2, "enqueued": 2, "queue_enqueued": 2}
        )
        mock_svc_class.return_value = mock_svc

        source = PluginFeedSource()
        db = AsyncMock()
        queue = AsyncMock()
        result = await source.enqueue_due(db, queue, limit=10)

        assert result["enqueued"] == 2
        mock_svc.enqueue_due_schedules.assert_awaited_once_with(limit=10)



class TestExperienceSource:
    """Tests for ExperienceSource."""

    def test_name(self):
        source = ExperienceSource()
        assert source.name == "experiences"

    @pytest.mark.asyncio
    async def test_cleanup_stale_returns_zero(self):
        """Experiences don't have stale cleanup yet."""
        source = ExperienceSource()
        db = AsyncMock()
        result = await source.cleanup_stale(db)
        assert result == 0

    @pytest.mark.asyncio
    async def test_enqueue_due_no_experiences(self):
        """When no experiences are due, returns zeros."""
        source = ExperienceSource()
        db = AsyncMock()
        queue = AsyncMock()

        # Mock: no due experiences
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=mock_result)

        result = await source.enqueue_due(db, queue, limit=10)

        assert result["due"] == 0
        assert result["enqueued"] == 0
        assert result["queue_enqueued"] == 0

    @pytest.mark.asyncio
    async def test_enqueue_due_no_users(self):
        """When no active users exist, advances schedules and returns no_users=1."""
        source = ExperienceSource()
        db = AsyncMock()
        db.commit = AsyncMock()
        queue = AsyncMock()

        # Mock experience
        mock_exp = MagicMock()
        mock_exp.id = "exp-1"
        mock_exp.trigger_type = "cron"
        mock_exp.created_by = None
        mock_exp.schedule_next = MagicMock()

        # First call returns experiences, second returns no users
        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # Due experiences query
                result.scalars.return_value.all.return_value = [mock_exp]
            else:
                # Active users query
                result.scalars.return_value.all.return_value = []
            return result

        db.execute = mock_execute

        result = await source.enqueue_due(db, queue, limit=10)

        assert result["due"] == 1
        assert result["enqueued"] == 0
        assert result["no_users"] == 1
        mock_exp.schedule_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_enqueue_due_fans_out_per_user(self):
        """Each due experience enqueues one job per active user with run_id."""
        source = ExperienceSource()
        db = AsyncMock()
        db.commit = AsyncMock()
        db.flush = AsyncMock()
        db.add = MagicMock()
        queue = AsyncMock()

        # Mock experience
        mock_exp = MagicMock()
        mock_exp.id = "exp-1"
        mock_exp.trigger_type = "cron"
        mock_exp.created_by = "creator-1"
        mock_exp.model_configuration_id = None
        mock_exp.schedule_next = MagicMock()

        # Mock users
        mock_user1 = MagicMock()
        mock_user1.id = "user-1"
        mock_user2 = MagicMock()
        mock_user2.id = "user-2"

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalars.return_value.all.return_value = [mock_exp]
            elif call_count == 2:
                result.scalars.return_value.all.return_value = [mock_user1, mock_user2]
            else:
                # UserPreferences query for timezone
                result.scalar_one_or_none.return_value = None
            return result

        db.execute = mock_execute

        # Mock enqueue_job
        mock_job = MagicMock()
        mock_job.id = "job-1"

        with patch(
            "shu.core.workload_routing.enqueue_job",
            new_callable=AsyncMock,
            return_value=mock_job,
        ) as mock_enqueue:
            # Patch ExperienceRun so db.add receives a mock with an id
            with patch(
                "shu.models.experience.ExperienceRun",
            ) as mock_run_cls:
                run_counter = 0

                def make_run(**kwargs):
                    nonlocal run_counter
                    run_counter += 1
                    run = MagicMock()
                    run.id = f"run-{run_counter}"
                    run.status = "queued"
                    for k, v in kwargs.items():
                        setattr(run, k, v)
                    return run

                mock_run_cls.side_effect = make_run
                result = await source.enqueue_due(db, queue, limit=10)

        assert result["due"] == 1
        assert result["enqueued"] == 1
        assert result["queue_enqueued"] == 2  # One per user

        # Verify enqueue was called with correct payloads including run_id
        assert mock_enqueue.call_count == 2
        calls = mock_enqueue.call_args_list

        payload_1 = calls[0].kwargs["payload"]

        assert payload_1["action"] == "experience_execution"
        assert payload_1["experience_id"] == "exp-1"
        assert "run_id" in payload_1

        # Schedule advanced once
        mock_exp.schedule_next.assert_called_once()
        assert mock_exp.last_run_at is not None


class TestAttachmentCleanupSource:
    """Tests for AttachmentCleanupSource delegation."""

    def test_name(self):
        source = AttachmentCleanupSource()
        assert source.name == "attachment_cleanup"

    @pytest.mark.asyncio
    @patch("shu.services.attachment_cleanup.AttachmentCleanupService")
    async def test_cleanup_stale_delegates(self, mock_svc_class):
        """cleanup_stale delegates to AttachmentCleanupService.cleanup_expired_attachments."""
        mock_svc = MagicMock()
        mock_svc.cleanup_expired_attachments = AsyncMock(return_value=5)
        mock_svc_class.return_value = mock_svc

        source = AttachmentCleanupSource()
        db = AsyncMock()
        result = await source.cleanup_stale(db)

        assert result == 5
        mock_svc_class.assert_called_once_with(db)
        mock_svc.cleanup_expired_attachments.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_enqueue_due_returns_zero(self):
        """enqueue_due returns zero since all work happens in cleanup_stale."""
        source = AttachmentCleanupSource()
        db = AsyncMock()
        queue = AsyncMock()

        result = await source.enqueue_due(db, queue, limit=10)

        assert result == {"enqueued": 0}


class TestUnifiedSchedulerService:
    """Tests for UnifiedSchedulerService.tick()."""

    @pytest.mark.asyncio
    async def test_tick_iterates_all_sources(self):
        """Tick calls cleanup_stale and enqueue_due on each source."""
        source1 = MagicMock()
        source1.name = "source_a"
        source1.cleanup_stale = AsyncMock(return_value=0)
        source1.enqueue_due = AsyncMock(
            return_value={"due": 1, "enqueued": 1}
        )

        source2 = MagicMock()
        source2.name = "source_b"
        source2.cleanup_stale = AsyncMock(return_value=2)
        source2.enqueue_due = AsyncMock(
            return_value={"due": 3, "enqueued": 3}
        )

        db = AsyncMock()
        queue = AsyncMock()
        svc = UnifiedSchedulerService(db, queue, [source1, source2])

        results = await svc.tick(limit=10)

        assert "source_a" in results
        assert "source_b" in results
        assert results["source_a"]["enqueued"] == 1
        assert results["source_b"]["enqueued"] == 3
        assert results["source_b"]["stale_cleaned"] == 2

    @pytest.mark.asyncio
    async def test_tick_source_error_doesnt_block_others(self):
        """If one source fails, the other still runs."""
        source1 = MagicMock()
        source1.name = "failing"
        source1.cleanup_stale = AsyncMock(side_effect=Exception("DB down"))

        source2 = MagicMock()
        source2.name = "working"
        source2.cleanup_stale = AsyncMock(return_value=0)
        source2.enqueue_due = AsyncMock(
            return_value={"due": 1, "enqueued": 1}
        )

        db = AsyncMock()
        queue = AsyncMock()
        svc = UnifiedSchedulerService(db, queue, [source1, source2])

        results = await svc.tick(limit=10)

        assert "error" in results["failing"]
        assert results["working"]["enqueued"] == 1
