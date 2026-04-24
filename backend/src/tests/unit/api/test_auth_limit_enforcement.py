"""Tests that user-limit HTTPExceptions propagate as 403, not 500.

Regression test: the try/except Exception in register_user and create_user
previously swallowed HTTPException and converted it into a 500.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status

from shu.api.auth import CreateUserRequest, RegisterRequest, create_user, register_user
from shu.billing.enforcement import UserLimitStatus


class TestRegisterUserLimitEnforcement:
    """The 403 from a hard limit must reach the client unchanged."""

    @pytest.mark.asyncio
    async def test_hard_limit_raises_403_not_500(self):
        """Hard limit should propagate as 403, not be swallowed by the blanket except."""
        request = RegisterRequest(
            email="new@example.com",
            password="password123!",
            name="New User",
        )
        db = AsyncMock()
        user_service = MagicMock()
        user_service.is_first_user = AsyncMock(return_value=False)

        with patch("shu.api.auth.check_user_limit") as mock_check:
            mock_check.return_value = UserLimitStatus(
                enforcement="hard",
                at_limit=True,
                current_count=5,
                user_limit=5,
            )

            with pytest.raises(HTTPException) as exc_info:
                await register_user(request, db=db, user_service=user_service)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert "User limit (5) reached" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_first_user_bypasses_limit(self):
        """First user bootstrap should not trigger limit check."""
        request = RegisterRequest(
            email="first@example.com",
            password="password123!",
            name="First User",
        )
        db = AsyncMock()
        user_service = MagicMock()
        user_service.is_first_user = AsyncMock(return_value=True)
        user_service.determine_user_role = MagicMock()
        user_service.determine_user_role.return_value.value = "admin"
        mock_user = MagicMock(email="first@example.com")

        # determine_user_role should return UserRole.ADMIN comparison result
        from shu.auth import UserRole
        user_service.determine_user_role.return_value = UserRole.ADMIN

        with (
            patch("shu.api.auth.check_user_limit") as mock_check,
            patch("shu.api.auth.password_auth_service") as mock_pw,
        ):
            mock_pw.create_user = AsyncMock(return_value=mock_user)

            await register_user(request, db=db, user_service=user_service)

            # Limit check should NOT be called for first user
            mock_check.assert_not_called()


class TestCreateUserLimitEnforcement:
    """Admin create_user must also propagate 403 from hard limits."""

    @pytest.mark.asyncio
    async def test_hard_limit_raises_403_not_500(self):
        """Admin create hitting hard limit should 403, not 500."""
        request = CreateUserRequest(
            email="new@example.com",
            password="password123!",
            name="New User",
            role="regular_user",
            auth_method="password",
        )
        db = AsyncMock()
        current_user = MagicMock()

        with patch("shu.api.auth.check_user_limit") as mock_check:
            mock_check.return_value = UserLimitStatus(
                enforcement="hard",
                at_limit=True,
                current_count=5,
                user_limit=5,
            )

            with pytest.raises(HTTPException) as exc_info:
                await create_user(request, current_user=current_user, db=db)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert "User limit (5) reached" in str(exc_info.value.detail)
