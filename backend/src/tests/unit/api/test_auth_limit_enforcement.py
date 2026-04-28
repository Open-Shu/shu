"""Tests that user-limit handling on /register doesn't get swallowed by 500.

Regression: the try/except Exception in register_user previously converted
HTTPException into 500. Under SHU-730, registration creates inactive users
(active-count enforcement skips them), so the 403 path is essentially
unreachable in practice — but the propagation guarantee still matters for
any future scenario where it does fire.

Admin create_user no longer 403s on hard-limit — under SHU-730 it returns
a 402 with a proration preview via the inline seat-charge preflight.
That path is covered by test_auth_seat_preflight.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status

from shu.api.auth import RegisterRequest, register_user
from shu.billing.enforcement import UserLimitStatus


class TestRegisterUserLimitEnforcement:
    """If a 403 ever does fire on /register, it must reach the client unchanged."""

    @pytest.mark.asyncio
    async def test_hard_limit_403_propagates_when_check_returns_at_limit(self):
        """Forced at_limit=True must produce 403, not 500.

        This is a propagation guarantee, not a real-world scenario: under
        SHU-730 active-count enforcement, registering an inactive user
        cannot push current_count >= user_limit. We mock check_user_limit
        to bypass that and verify the catch-all in register_user still
        lets HTTPException through.
        """
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
