"""
Unit tests for experience API manual-trigger guard.

Tests cover:
- Non-admin cannot manually trigger a shared experience (returns 403)
- Admin manually triggering a shared experience creates a run with user_id=None
- User-scoped experience manual trigger still works normally (regression)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.api.experiences import run_experience


def _mock_user(*, is_admin: bool = False, user_id: str = "user-1", is_active: bool = True):
    """Build a mock User with configurable admin status."""
    user = MagicMock()
    user.id = user_id
    user.is_active = is_active
    user.can_manage_users.return_value = is_admin
    return user


def _mock_experience_response(*, scope: str = "user"):
    """Build a mock ExperienceResponse returned by ExperienceService.get_experience."""
    resp = MagicMock()
    resp.scope = scope
    return resp


def _mock_experience_model(*, scope: str = "user", creator=None):
    """Build a mock Experience ORM model returned by the raw DB query."""
    model = MagicMock()
    model.scope = scope
    model.id = "exp-1"
    model.steps = []
    model.prompt = None
    model.creator = creator
    return model


class TestManualRunSharedGuard:
    """Tests for the manual-trigger guard on shared experiences."""

    @pytest.mark.asyncio
    async def test_manual_run_shared_experience_non_admin_returns_403(self):
        """Non-admin trying to manually run a shared experience gets a 403."""
        db = AsyncMock()
        current_user = _mock_user(is_admin=False)
        experience_resp = _mock_experience_response(scope="shared")
        experience_model = _mock_experience_model(scope="shared")

        # Mock the raw DB query (select(Experience)...) that returns the ORM model
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = experience_model
        db.execute.return_value = mock_result

        with patch(
            "shu.api.experiences.ExperienceService"
        ) as mock_svc_class:
            mock_svc = MagicMock()
            mock_svc.get_experience = AsyncMock(return_value=experience_resp)
            mock_svc_class.return_value = mock_svc

            response = await run_experience(
                experience_id="exp-1",
                run_request=None,
                current_user=current_user,
                db=db,
            )

        assert response.status_code == 403
        body = response.body.decode()
        assert "Shared" in body
        assert "SHARED_EXPERIENCE_NON_ADMIN" in body

    @pytest.mark.asyncio
    async def test_manual_run_shared_experience_admin_creates_shared_run(self):
        """Admin manually running a shared experience passes user_id=None and creator as current_user."""
        db = AsyncMock()
        current_user = _mock_user(is_admin=True, user_id="admin-1")
        mock_creator = _mock_user(user_id="creator-1")
        experience_resp = _mock_experience_response(scope="shared")
        experience_model = _mock_experience_model(scope="shared", creator=mock_creator)

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = experience_model
        db.execute.return_value = mock_result

        with patch("shu.api.experiences.ExperienceService") as mock_svc_class, \
             patch("shu.api.experiences.get_config_manager") as mock_get_cm, \
             patch("shu.api.experiences.ExperienceExecutor") as mock_exec_class, \
             patch("shu.api.experiences.create_sse_stream_generator") as mock_sse:

            mock_svc = MagicMock()
            mock_svc.get_experience = AsyncMock(return_value=experience_resp)
            mock_svc_class.return_value = mock_svc
            mock_get_cm.return_value = MagicMock()

            mock_executor = MagicMock()
            mock_event_gen = AsyncMock()
            mock_executor.execute_streaming.return_value = mock_event_gen
            mock_exec_class.return_value = mock_executor
            mock_sse.return_value = iter([])

            response = await run_experience(
                experience_id="exp-1",
                run_request=None,
                current_user=current_user,
                db=db,
            )

        # Verify executor was called with user_id=None (run ownership) and creator as current_user (execution identity)
        mock_executor.execute_streaming.assert_called_once()
        call_kwargs = mock_executor.execute_streaming.call_args
        kw = call_kwargs.kwargs or {}
        assert kw.get("user_id") is None, f"Expected user_id=None, got {kw.get('user_id')}"
        assert kw.get("current_user") is mock_creator, f"Expected creator as current_user, got {kw.get('current_user')}"

    @pytest.mark.asyncio
    async def test_manual_run_shared_experience_inactive_creator_returns_403(self):
        """Shared experience with inactive creator returns 403."""
        db = AsyncMock()
        current_user = _mock_user(is_admin=True, user_id="admin-1")
        inactive_creator = _mock_user(user_id="creator-1", is_active=False)
        experience_resp = _mock_experience_response(scope="shared")
        experience_model = _mock_experience_model(scope="shared", creator=inactive_creator)

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = experience_model
        db.execute.return_value = mock_result

        with patch("shu.api.experiences.ExperienceService") as mock_svc_class:
            mock_svc = MagicMock()
            mock_svc.get_experience = AsyncMock(return_value=experience_resp)
            mock_svc_class.return_value = mock_svc

            response = await run_experience(
                experience_id="exp-1",
                run_request=None,
                current_user=current_user,
                db=db,
            )

        assert response.status_code == 403
        body = response.body.decode()
        assert "inactive" in body
        assert "SHARED_EXPERIENCE_CREATOR_INACTIVE" in body

    @pytest.mark.asyncio
    async def test_manual_run_user_experience_unchanged(self):
        """Regression: user-scoped experience still runs normally with user's ID."""
        db = AsyncMock()
        current_user = _mock_user(is_admin=False, user_id="user-42")
        experience_resp = _mock_experience_response(scope="user")
        experience_model = _mock_experience_model(scope="user")

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = experience_model
        db.execute.return_value = mock_result

        with patch("shu.api.experiences.ExperienceService") as mock_svc_class, \
             patch("shu.api.experiences.get_config_manager") as mock_get_cm, \
             patch("shu.api.experiences.ExperienceExecutor") as mock_exec_class, \
             patch("shu.api.experiences.create_sse_stream_generator") as mock_sse:

            mock_svc = MagicMock()
            mock_svc.get_experience = AsyncMock(return_value=experience_resp)
            mock_svc_class.return_value = mock_svc
            mock_get_cm.return_value = MagicMock()

            mock_executor = MagicMock()
            mock_event_gen = AsyncMock()
            mock_executor.execute_streaming.return_value = mock_event_gen
            mock_exec_class.return_value = mock_executor
            mock_sse.return_value = iter([])

            response = await run_experience(
                experience_id="exp-1",
                run_request=None,
                current_user=current_user,
                db=db,
            )

        # Verify executor was called with the user's actual ID
        mock_executor.execute_streaming.assert_called_once()
        call_kwargs = mock_executor.execute_streaming.call_args
        kw = call_kwargs.kwargs or {}
        assert kw.get("user_id") == "user-42" or call_kwargs[1].get("user_id") == "user-42"
