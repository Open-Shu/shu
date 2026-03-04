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
        """Each due experience enqueues one job per active user."""
        source = ExperienceSource()
        db = AsyncMock()
        db.commit = AsyncMock()
        queue = AsyncMock()

        # Mock experience
        mock_exp = MagicMock()
        mock_exp.id = "exp-1"
        mock_exp.trigger_type = "cron"
        mock_exp.created_by = "creator-1"
        mock_exp.model_configuration_id = None
        mock_exp.scope = "user"
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
            elif call_count == 3:
                # Active user-scoped runs query — no active runs
                result.__iter__ = MagicMock(return_value=iter([]))
            else:
                # UserPreferences query for timezone
                result.scalar_one_or_none.return_value = None
            return result

        db.execute = mock_execute

        with patch(
            "shu.services.scheduler_service._enqueue_experience_run",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_enqueue_run:
            result = await source.enqueue_due(db, queue, limit=10)

        assert result["due"] == 1
        assert result["enqueued"] == 1
        assert result["queue_enqueued"] == 2  # One per user
        assert result["skipped_active_user_runs"] == 0

        # Verify enqueue was called once per user with correct user_ids
        assert mock_enqueue_run.call_count == 2
        user_ids = [c.kwargs["user_id"] for c in mock_enqueue_run.call_args_list]
        assert set(user_ids) == {"user-1", "user-2"}

        # Schedule advanced once
        mock_exp.schedule_next.assert_called_once()
        assert mock_exp.last_run_at is not None

    @pytest.mark.asyncio
    async def test_enqueue_due_global_scope_enqueues_single_shared_job(self):
        """Global experiences enqueue exactly one shared run/job, not per-user fan-out."""
        source = ExperienceSource()
        db = AsyncMock()
        db.commit = AsyncMock()
        queue = AsyncMock()

        # Mock global experience
        mock_exp = MagicMock()
        mock_exp.id = "exp-global-1"
        mock_exp.trigger_type = "cron"
        mock_exp.created_by = None
        mock_exp.model_configuration_id = None
        mock_exp.scope = "global"
        mock_exp.schedule_next = MagicMock()

        # Mock users (should not affect global fan-out count)
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
                # Due experiences
                result.scalars.return_value.all.return_value = [mock_exp]
            elif call_count == 2:
                # Active users
                result.scalars.return_value.all.return_value = [mock_user1, mock_user2]
            elif call_count == 3:
                # Batch active-runs query — no active runs
                result.__iter__ = MagicMock(return_value=iter([]))
            else:
                result.scalar_one_or_none.return_value = None
            return result

        db.execute = mock_execute

        with patch(
            "shu.services.scheduler_service._enqueue_experience_run",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_enqueue_run:
            result = await source.enqueue_due(db, queue, limit=10)

        assert result["due"] == 1
        assert result["enqueued"] == 1
        assert result["queue_enqueued"] == 1
        assert mock_enqueue_run.call_count == 1
        assert mock_enqueue_run.call_args.kwargs["user_id"] is None
        mock_exp.schedule_next.assert_called_once()
        assert mock_exp.last_run_at is not None

    @pytest.mark.asyncio
    async def test_enqueue_due_global_scope_skips_when_active_global_run_exists(self):
        """Global experiences are skipped when a queued/running global run already exists."""
        source = ExperienceSource()
        db = AsyncMock()
        db.commit = AsyncMock()
        queue = AsyncMock()

        mock_exp = MagicMock()
        mock_exp.id = "exp-global-1"
        mock_exp.trigger_type = "cron"
        mock_exp.created_by = None
        mock_exp.model_configuration_id = None
        mock_exp.scope = "global"
        mock_exp.schedule_next = MagicMock()

        mock_user = MagicMock()
        mock_user.id = "user-1"

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # Due experiences
                result.scalars.return_value.all.return_value = [mock_exp]
            elif call_count == 2:
                # Active users
                result.scalars.return_value.all.return_value = [mock_user]
            elif call_count == 3:
                # Batch active-runs query — global run exists (user_id=None)
                active_row = MagicMock()
                active_row.experience_id = "exp-global-1"
                active_row.user_id = None
                result.__iter__ = MagicMock(return_value=iter([active_row]))
            else:
                result.scalar_one_or_none.return_value = None
            return result

        db.execute = mock_execute

        with patch(
            "shu.services.scheduler_service._enqueue_experience_run",
            new_callable=AsyncMock,
        ) as mock_enqueue_run:
            result = await source.enqueue_due(db, queue, limit=10)

        assert result["due"] == 1
        assert result["enqueued"] == 0
        assert result["queue_enqueued"] == 0
        assert result["skipped_active_user_runs"] == 1
        mock_enqueue_run.assert_not_called()
        mock_exp.schedule_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_enqueue_due_skips_active_user_run_pairs(self):
        """User-scoped experience skips users that already have a queued/running run."""
        source = ExperienceSource()
        db = AsyncMock()
        db.commit = AsyncMock()
        queue = AsyncMock()

        mock_exp = MagicMock()
        mock_exp.id = "exp-1"
        mock_exp.trigger_type = "cron"
        mock_exp.created_by = None
        mock_exp.model_configuration_id = None
        mock_exp.scope = "user"
        mock_exp.schedule_next = MagicMock()

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
            elif call_count == 3:
                # Active runs: user-1 already has an active run for exp-1
                active_row = MagicMock()
                active_row.experience_id = "exp-1"
                active_row.user_id = "user-1"
                result.__iter__ = MagicMock(return_value=iter([active_row]))
            else:
                result.scalar_one_or_none.return_value = None
            return result

        db.execute = mock_execute

        with patch(
            "shu.services.scheduler_service._enqueue_experience_run",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_enqueue_run:
            result = await source.enqueue_due(db, queue, limit=10)

        assert result["queue_enqueued"] == 1
        assert result["skipped_active_user_runs"] == 1
        assert result["enqueued"] == 1
        assert mock_enqueue_run.call_count == 1
        # Only user-2 should have been enqueued
        assert mock_enqueue_run.call_args.kwargs["user_id"] == "user-2"
        mock_exp.schedule_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_enqueue_due_all_user_pairs_active_enqueues_none(self):
        """When all users already have active runs, no jobs are enqueued but schedule still advances."""
        source = ExperienceSource()
        db = AsyncMock()
        db.commit = AsyncMock()
        queue = AsyncMock()

        mock_exp = MagicMock()
        mock_exp.id = "exp-1"
        mock_exp.trigger_type = "cron"
        mock_exp.created_by = None
        mock_exp.model_configuration_id = None
        mock_exp.scope = "user"
        mock_exp.schedule_next = MagicMock()

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
            elif call_count == 3:
                # Both users have active runs
                row1 = MagicMock()
                row1.experience_id = "exp-1"
                row1.user_id = "user-1"
                row2 = MagicMock()
                row2.experience_id = "exp-1"
                row2.user_id = "user-2"
                result.__iter__ = MagicMock(return_value=iter([row1, row2]))
            else:
                result.scalar_one_or_none.return_value = None
            return result

        db.execute = mock_execute

        with patch(
            "shu.services.scheduler_service._enqueue_experience_run",
            new_callable=AsyncMock,
        ) as mock_enqueue_run:
            result = await source.enqueue_due(db, queue, limit=10)

        assert result["queue_enqueued"] == 0
        assert result["skipped_active_user_runs"] == 2
        assert result["enqueued"] == 1  # Schedule still advances
        mock_enqueue_run.assert_not_called()
        mock_exp.schedule_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_enqueue_due_no_active_pairs_enqueues_all(self):
        """When no active runs exist, all users are enqueued (baseline behavior)."""
        source = ExperienceSource()
        db = AsyncMock()
        db.commit = AsyncMock()
        queue = AsyncMock()

        mock_exp = MagicMock()
        mock_exp.id = "exp-1"
        mock_exp.trigger_type = "cron"
        mock_exp.created_by = None
        mock_exp.model_configuration_id = None
        mock_exp.scope = "user"
        mock_exp.schedule_next = MagicMock()

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
            elif call_count == 3:
                # No active runs
                result.__iter__ = MagicMock(return_value=iter([]))
            else:
                result.scalar_one_or_none.return_value = None
            return result

        db.execute = mock_execute

        with patch(
            "shu.services.scheduler_service._enqueue_experience_run",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_enqueue_run:
            result = await source.enqueue_due(db, queue, limit=10)

        assert result["queue_enqueued"] == 2
        assert result["skipped_active_user_runs"] == 0
        assert mock_enqueue_run.call_count == 2

    @pytest.mark.asyncio
    async def test_enqueue_due_global_scope_unaffected_by_user_dedup(self):
        """Global experience uses the same batch query but checks (exp_id, None) key."""
        source = ExperienceSource()
        db = AsyncMock()
        db.commit = AsyncMock()
        queue = AsyncMock()

        mock_exp = MagicMock()
        mock_exp.id = "exp-global-1"
        mock_exp.trigger_type = "cron"
        mock_exp.created_by = None
        mock_exp.model_configuration_id = None
        mock_exp.scope = "global"
        mock_exp.schedule_next = MagicMock()

        mock_user = MagicMock()
        mock_user.id = "user-1"

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalars.return_value.all.return_value = [mock_exp]
            elif call_count == 2:
                result.scalars.return_value.all.return_value = [mock_user]
            elif call_count == 3:
                # Batch active-runs query — no active runs
                result.__iter__ = MagicMock(return_value=iter([]))
            else:
                result.scalar_one_or_none.return_value = None
            return result

        db.execute = mock_execute

        with patch(
            "shu.services.scheduler_service._enqueue_experience_run",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_enqueue_run:
            result = await source.enqueue_due(db, queue, limit=10)

        # Global experience enqueues one shared job
        assert result["queue_enqueued"] == 1
        assert result["skipped_active_user_runs"] == 0
        assert mock_enqueue_run.call_count == 1
        assert mock_enqueue_run.call_args.kwargs["user_id"] is None


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
