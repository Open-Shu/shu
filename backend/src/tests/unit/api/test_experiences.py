"""
Unit tests for experience API manual-trigger guard.

Tests cover:
- Non-admin cannot manually trigger a shared experience (returns 403)
- Admin manually triggering a shared experience streams successfully
- Shared experience with inactive creator returns 403
- User-scoped experience manual trigger still works normally (regression)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.api.experiences import run_experience
from shu.core.exceptions import AuthorizationError, NotFoundError


def _mock_user(*, is_admin: bool = False, user_id: str = "user-1", is_active: bool = True):
    """Build a mock User with configurable admin status."""
    user = MagicMock()
    user.id = user_id
    user.is_active = is_active
    user.can_manage_users.return_value = is_admin
    return user


class TestManualRunSharedGuard:
    """Tests for the manual-trigger guard on shared experiences."""

    @pytest.mark.asyncio
    async def test_manual_run_shared_experience_non_admin_returns_403(self):
        """Non-admin trying to manually run a shared experience gets a 403."""
        db = AsyncMock()
        current_user = _mock_user(is_admin=False)

        with patch("shu.api.experiences.ExperienceService") as mock_svc_class:
            mock_svc = MagicMock()
            mock_svc.run = AsyncMock(
                side_effect=AuthorizationError(
                    "Shared experiences can only be triggered manually by admins.",
                    details={"code": "SHARED_EXPERIENCE_NON_ADMIN"},
                )
            )
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
    async def test_manual_run_shared_experience_admin_streams_successfully(self):
        """Admin manually running a shared experience returns a streaming response."""
        db = AsyncMock()
        current_user = _mock_user(is_admin=True, user_id="admin-1")

        with patch("shu.api.experiences.ExperienceService") as mock_svc_class, \
             patch("shu.api.experiences.create_sse_stream_generator") as mock_sse:

            mock_svc = MagicMock()
            mock_event_gen = AsyncMock()
            mock_svc.run = AsyncMock(return_value=mock_event_gen)
            mock_svc_class.return_value = mock_svc
            mock_sse.return_value = iter([])

            response = await run_experience(
                experience_id="exp-1",
                run_request=None,
                current_user=current_user,
                db=db,
            )

        assert response.status_code == 200
        assert response.media_type == "text/event-stream"
        mock_svc.run.assert_called_once_with(
            experience_id="exp-1",
            current_user=current_user,
            input_params=None,
        )

    @pytest.mark.asyncio
    async def test_manual_run_shared_experience_inactive_creator_returns_403(self):
        """Shared experience with inactive creator returns 403."""
        db = AsyncMock()
        current_user = _mock_user(is_admin=True, user_id="admin-1")

        with patch("shu.api.experiences.ExperienceService") as mock_svc_class:
            mock_svc = MagicMock()
            mock_svc.run = AsyncMock(
                side_effect=AuthorizationError(
                    "The creator of this shared experience is inactive. "
                    "Re-activate their account or reassign the experience.",
                    details={"code": "SHARED_EXPERIENCE_CREATOR_INACTIVE"},
                )
            )
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

        with patch("shu.api.experiences.ExperienceService") as mock_svc_class, \
             patch("shu.api.experiences.create_sse_stream_generator") as mock_sse:

            mock_svc = MagicMock()
            mock_event_gen = AsyncMock()
            mock_svc.run = AsyncMock(return_value=mock_event_gen)
            mock_svc_class.return_value = mock_svc
            mock_sse.return_value = iter([])

            response = await run_experience(
                experience_id="exp-1",
                run_request=None,
                current_user=current_user,
                db=db,
            )

        assert response.status_code == 200
        mock_svc.run.assert_called_once_with(
            experience_id="exp-1",
            current_user=current_user,
            input_params=None,
        )

    @pytest.mark.asyncio
    async def test_manual_run_not_found_returns_404(self):
        """Experience not found returns 404."""
        db = AsyncMock()
        current_user = _mock_user()

        with patch("shu.api.experiences.ExperienceService") as mock_svc_class:
            mock_svc = MagicMock()
            mock_svc.run = AsyncMock(
                side_effect=NotFoundError(
                    "Experience 'exp-1' not found or access denied",
                    details={"code": "EXPERIENCE_NOT_FOUND"},
                )
            )
            mock_svc_class.return_value = mock_svc

            response = await run_experience(
                experience_id="exp-1",
                run_request=None,
                current_user=current_user,
                db=db,
            )

        assert response.status_code == 404
        body = response.body.decode()
        assert "not found" in body
        assert "EXPERIENCE_NOT_FOUND" in body
