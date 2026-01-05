"""
Unit tests for ExperiencesSchedulerService.

Tests cover:
- User timezone preference retrieval
- Due experiences query filtering
- schedule_next() logic for scheduled/cron triggers
- run_due_experiences() execution flow
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from shu.models.experience import Experience
from shu.services.experiences_scheduler_service import ExperiencesSchedulerService


class TestGetUserTimezone:
    """Tests for get_user_timezone method."""
    
    @pytest.fixture
    def mock_db(self):
        """Create a mock async database session."""
        db = AsyncMock()
        return db
    
    @pytest.fixture
    def service(self, mock_db):
        """Create scheduler service with mocked db."""
        return ExperiencesSchedulerService(mock_db)
    
    @pytest.mark.asyncio
    async def test_user_has_timezone_preference(self, service, mock_db):
        """Test that user timezone is returned when preferences exist."""
        mock_prefs = MagicMock()
        mock_prefs.timezone = "America/Chicago"
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_prefs
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        result = await service.get_user_timezone("user-123")
        
        assert result == "America/Chicago"
    
    @pytest.mark.asyncio
    async def test_user_no_preferences(self, service, mock_db):
        """Test that None is returned when user has no preferences."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        result = await service.get_user_timezone("user-123")
        
        assert result is None
    
    @pytest.mark.asyncio
    async def test_database_error(self, service, mock_db):
        """Test that None is returned on database error."""
        mock_db.execute = AsyncMock(side_effect=Exception("DB error"))
        
        result = await service.get_user_timezone("user-123")
        
        assert result is None


class TestScheduleNext:
    """Tests for Experience.schedule_next method."""
    
    def test_manual_trigger_sets_none(self):
        """Manual trigger type results in None next_run_at."""
        exp = Experience()
        exp.trigger_type = "manual"
        exp.trigger_config = {}
        
        exp.schedule_next()
        
        assert exp.next_run_at is None
    
    def test_scheduled_trigger_without_config_sets_none(self):
        """Scheduled trigger without scheduled_at sets next_run_at to None."""
        exp = Experience()
        exp.trigger_type = "scheduled"
        exp.trigger_config = {}  # Empty config
        
        exp.schedule_next()
        
        assert exp.next_run_at is None
    
    @patch("shu.models.experience.logger")
    def test_cron_expression_valid(self, mock_logger):
        """Cron expression computes next occurrence."""
        exp = Experience()
        exp.trigger_type = "cron"
        exp.trigger_config = {"cron": "0 8 * * *"}  # Daily at 8am
        
        exp.schedule_next()
        
        assert exp.next_run_at is not None
    
    @patch("shu.models.experience.logger")
    def test_cron_expression_invalid_falls_back(self, mock_logger):
        """Invalid cron expression falls back to 1 hour from now."""
        exp = Experience()
        exp.trigger_type = "cron"
        exp.trigger_config = {"cron": "invalid cron"}
        
        before = datetime.now(timezone.utc)
        exp.schedule_next()
        after = datetime.now(timezone.utc)
        
        # Should be approximately 1 hour from now
        assert exp.next_run_at is not None
        assert exp.next_run_at >= before + timedelta(minutes=59)
        assert exp.next_run_at <= after + timedelta(hours=1, minutes=1)
    
    def test_unknown_trigger_type_sets_none(self):
        """Unknown trigger type results in None next_run_at."""
        exp = Experience()
        exp.trigger_type = "unknown"
        exp.trigger_config = {}
        
        exp.schedule_next()
        
        assert exp.next_run_at is None

    def test_scheduled_trigger_one_time(self):
        """One-time scheduled trigger runs once then stops."""
        exp = Experience()
        exp.trigger_type = "scheduled"
        # Create a future target time
        target = datetime.now(timezone.utc) + timedelta(hours=1)
        # Remove microseconds for reliable comparison as isoformat might include them
        target = target.replace(microsecond=0)
        exp.trigger_config = {"scheduled_at": target.isoformat()}
        
        # 1. Initial schedule
        exp.schedule_next()
        assert exp.next_run_at is not None
        # Verify match
        assert exp.next_run_at == target
        
        # 2. Simulate run completion (last_run_at >= target)
        exp.last_run_at = exp.next_run_at + timedelta(seconds=1)
        
        # 3. Next schedule should be None (one-time)
        exp.schedule_next()
        assert exp.next_run_at is None

    def test_scheduled_trigger_one_time_catchup(self):
        """Past scheduled_at is still scheduled for immediate execution."""
        exp = Experience()
        exp.trigger_type = "scheduled"
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        past = past.replace(microsecond=0)
        exp.trigger_config = {"scheduled_at": past.isoformat()}
        
        exp.schedule_next()
        
        assert exp.next_run_at is not None
        assert exp.next_run_at == past


class TestGetDueExperiences:
    """Tests for get_due_experiences method."""
    
    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        return db
    
    @pytest.fixture
    def service(self, mock_db):
        return ExperiencesSchedulerService(mock_db)
    
    @pytest.mark.asyncio
    async def test_returns_due_experiences(self, service, mock_db):
        """Test that due experiences are returned."""
        mock_exp1 = MagicMock()
        mock_exp1.id = "exp-1"
        mock_exp2 = MagicMock()
        mock_exp2.id = "exp-2"
        
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_exp1, mock_exp2]
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        result = await service.get_due_experiences(limit=10)
        
        assert len(result) == 2
        assert result[0].id == "exp-1"
    
    @pytest.mark.asyncio
    async def test_returns_empty_when_none_due(self, service, mock_db):
        """Test empty list when no experiences are due."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        result = await service.get_due_experiences(limit=10)
        
        assert result == []


class TestExecuteExperience:
    """Tests for execute_experience method."""
    
    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        return db
    
    @pytest.fixture
    def service(self, mock_db):
        return ExperiencesSchedulerService(mock_db)
    
    @pytest.mark.asyncio
    async def test_user_not_found(self, service, mock_db):
        """Test that user not found returns failure."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        mock_exp = MagicMock()
        mock_exp.id = "exp-1"
        
        result = await service.execute_experience(mock_exp, "user-123")
        
        assert result["status"] == "failed"
        assert result["error"] == "user_not_found"
    
    @pytest.mark.asyncio
    @patch("shu.services.experience_executor.ExperienceExecutor")
    @patch("shu.core.config.get_config_manager")
    async def test_successful_execution(self, mock_get_config, mock_executor_class, service, mock_db):
        """Test successful experience execution."""
        # Mock user lookup
        mock_user = MagicMock()
        mock_user_result = MagicMock()
        mock_user_result.scalar_one_or_none.return_value = mock_user
        mock_db.execute = AsyncMock(return_value=mock_user_result)
        
        # Mock executor
        mock_run = MagicMock()
        mock_run.status = "succeeded"
        mock_run.id = "run-123"
        mock_run.error_message = None
        
        mock_executor = MagicMock()
        mock_executor.execute = AsyncMock(return_value=mock_run)
        mock_executor_class.return_value = mock_executor
        mock_get_config.return_value = MagicMock()
        
        mock_exp = MagicMock()
        mock_exp.id = "exp-1"
        
        result = await service.execute_experience(mock_exp, "user-123")
        
        assert result["status"] == "completed"
        assert result["run_id"] == "run-123"
    
    @pytest.mark.asyncio
    @patch("shu.services.experience_executor.ExperienceExecutor")
    @patch("shu.core.config.get_config_manager")
    async def test_failed_execution(self, mock_get_config, mock_executor_class, service, mock_db):
        """Test failed experience execution."""
        # Mock user lookup
        mock_user = MagicMock()
        mock_user_result = MagicMock()
        mock_user_result.scalar_one_or_none.return_value = mock_user
        mock_db.execute = AsyncMock(return_value=mock_user_result)
        
        # Mock executor failure
        mock_run = MagicMock()
        mock_run.status = "failed"
        mock_run.id = "run-456"
        mock_run.error_message = "Something went wrong"
        
        mock_executor = MagicMock()
        mock_executor.execute = AsyncMock(return_value=mock_run)
        mock_executor_class.return_value = mock_executor
        mock_get_config.return_value = MagicMock()
        
        mock_exp = MagicMock()
        mock_exp.id = "exp-1"
        
        result = await service.execute_experience(mock_exp, "user-123")
        
        assert result["status"] == "failed"
        assert result["error"] == "Something went wrong"


class TestRunDueExperiences:
    """Tests for run_due_experiences method."""
    
    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.commit = AsyncMock()
        return db
    
    @pytest.fixture
    def service(self, mock_db):
        return ExperiencesSchedulerService(mock_db)
    
    @pytest.mark.asyncio
    async def test_no_due_experiences(self, service):
        """Test when no experiences are due."""
        service.get_due_experiences = AsyncMock(return_value=[])
        
        result = await service.run_due_experiences(limit=10)
        
        assert result["due"] == 0
        assert result["user_runs"] == 0
        assert result["user_failures"] == 0
    
    @pytest.mark.asyncio
    async def test_no_active_users(self, service, mock_db):
        """Test when no active users exist - still advances schedule."""
        mock_exp = MagicMock()
        mock_exp.id = "exp-1"
        mock_exp.schedule_next = MagicMock()
        
        service.get_due_experiences = AsyncMock(return_value=[mock_exp])
        service.get_all_active_users = AsyncMock(return_value=[])
        
        result = await service.run_due_experiences(limit=10)
        
        assert result["due"] == 1
        assert result["no_users"] == 1
        mock_exp.schedule_next.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_runs_for_all_users(self, service, mock_db):
        """Test that experience runs for all active users."""
        mock_exp = MagicMock()
        mock_exp.id = "exp-1"
        mock_exp.name = "Test Experience"
        mock_exp.trigger_type = "scheduled"
        mock_exp.created_by = "creator-123"
        mock_exp.last_run_at = None
        mock_exp.schedule_next = MagicMock()
        
        mock_user1 = MagicMock()
        mock_user1.id = "user-1"
        mock_user2 = MagicMock()
        mock_user2.id = "user-2"
        
        service.get_due_experiences = AsyncMock(return_value=[mock_exp])
        service.get_all_active_users = AsyncMock(return_value=[mock_user1, mock_user2])
        service.get_user_timezone = AsyncMock(return_value="America/Chicago")
        service.execute_experience = AsyncMock(return_value={
            "status": "completed",
            "run_id": "run-123",
        })
        
        result = await service.run_due_experiences(limit=10)
        
        assert result["due"] == 1
        assert result["user_runs"] == 2  # Ran for both users
        assert result["user_failures"] == 0
        assert mock_exp.last_run_at is not None
        # Schedule updated once with creator's timezone
        mock_exp.schedule_next.assert_called_once_with(user_timezone="America/Chicago")
    
    @pytest.mark.asyncio
    async def test_silent_failure_for_user(self, service, mock_db):
        """Test that failures for individual users are silent and don't stop others."""
        mock_exp = MagicMock()
        mock_exp.id = "exp-1"
        mock_exp.name = "Test Experience"
        mock_exp.trigger_type = "scheduled"
        mock_exp.created_by = "creator-123"
        mock_exp.schedule_next = MagicMock()
        
        mock_user1 = MagicMock()
        mock_user1.id = "user-1"
        mock_user2 = MagicMock()
        mock_user2.id = "user-2"
        
        service.get_due_experiences = AsyncMock(return_value=[mock_exp])
        service.get_all_active_users = AsyncMock(return_value=[mock_user1, mock_user2])
        service.get_user_timezone = AsyncMock(return_value=None)
        
        # First user fails, second succeeds
        service.execute_experience = AsyncMock(side_effect=[
            {"status": "failed", "error": "Missing provider connection"},
            {"status": "completed", "run_id": "run-456"},
        ])
        
        result = await service.run_due_experiences(limit=10)
        
        assert result["user_runs"] == 1
        assert result["user_failures"] == 1
        # Both users were attempted
        assert service.execute_experience.call_count == 2

