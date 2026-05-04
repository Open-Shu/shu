"""Tests for DELETE /auth/users/{id} — verifies no Stripe side effects.

B5 removed the `asyncio.create_task(trigger_quantity_sync())` fire-and-forget
at user delete. Under SHU-730, seat downgrades are admin-scheduled via
SeatService; deleting a user no longer touches Stripe.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.api.auth import delete_user


class TestDeleteUserNoStripeCall:
    @pytest.mark.asyncio
    async def test_delete_user_does_not_touch_stripe(self):
        """Nothing in auth.delete_user should reference the StripeClient."""
        db = AsyncMock()
        current_user = MagicMock()
        user_service = MagicMock()
        user_service.delete_user = AsyncMock()

        # Patch the StripeClient constructor to catch any sneaky reads.
        with patch("shu.billing.stripe_client.StripeClient") as mock_stripe_cls:
            await delete_user(
                "42",
                current_user=current_user,
                db=db,
                user_service=user_service,
            )

        mock_stripe_cls.assert_not_called()
        user_service.delete_user.assert_awaited_once_with("42", current_user.id, db)
