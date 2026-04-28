"""Tests for the 402 phase-1 / phase-2 seat-charge preflight on create_user + activate_user."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from shu.api.auth import CreateUserRequest, activate_user, create_user
from shu.billing.enforcement import UserLimitStatus
from shu.billing.seat_service import ProrationPreview
from shu.billing.stripe_client import StripeClientError


def _make_preview() -> ProrationPreview:
    return ProrationPreview(
        amount_usd="7.50",
        period_end=datetime(2026, 5, 1, tzinfo=UTC),
        cost_per_seat_usd="10.00",
    )


def _make_seat_service(
    *,
    preview: ProrationPreview | None = None,
    confirm_raises: Exception | None = None,
) -> MagicMock:
    svc = MagicMock()
    svc.preview_upgrade = AsyncMock(return_value=preview)
    svc.confirm_upgrade = AsyncMock(
        side_effect=confirm_raises if confirm_raises else None
    )
    return svc


def _make_user_service(*, existing_email: bool = False, mock_user=None) -> MagicMock:
    """Stub UserService that controls the duplicate-email pre-check path."""
    svc = MagicMock()
    svc.get_user_by_email = AsyncMock(return_value=MagicMock() if existing_email else None)
    svc.get_user_by_id = AsyncMock(return_value=mock_user)
    return svc


def _parse_envelope(response) -> dict:
    """Extract the JSON body from a FastAPI JSONResponse."""
    return json.loads(response.body.decode("utf-8"))


class TestCreateUserPreflight:
    @pytest.mark.asyncio
    async def test_at_limit_without_header_returns_402_with_proration(self):
        """Phase 1 — no header → 402 payload includes preview, no Stripe write."""
        request = CreateUserRequest(
            email="new@example.com", password="pw12345678", name="New", auth_method="password"
        )
        db = AsyncMock()
        current_user = MagicMock()
        seat_service = _make_seat_service(preview=_make_preview())

        with patch(
            "shu.api.auth.check_user_limit",
            return_value=UserLimitStatus(
                enforcement="hard", at_limit=True, current_count=3, user_limit=3
            ),
        ):
            response = await create_user(
                request,
                current_user=current_user,
                db=db,
                user_service=_make_user_service(),
                seat_service=seat_service,
                x_seat_charge_confirmed=None,
            )

        assert response.status_code == 402
        body = _parse_envelope(response)
        assert body["error"]["code"] == "seat_limit_reached"
        details = body["error"]["details"]
        assert details["user_limit"] == 3
        assert details["current_count"] == 3
        assert details["proration"]["amount_usd"] == "7.50"
        # "portal_url" must NOT appear — SHU-704 lockdown
        assert "portal_url" not in details
        seat_service.confirm_upgrade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_at_limit_with_confirm_header_upgrades_and_creates(self):
        """Phase 2 — header present → confirm_upgrade called, user creation proceeds."""
        request = CreateUserRequest(
            email="new@example.com", password="pw12345678", name="New", auth_method="password"
        )
        db = AsyncMock()
        current_user = MagicMock()
        seat_service = _make_seat_service()

        mock_user = MagicMock()
        mock_user.to_dict.return_value = {"email": "new@example.com"}

        with (
            patch(
                "shu.api.auth.check_user_limit",
                return_value=UserLimitStatus(
                    enforcement="hard", at_limit=True, current_count=3, user_limit=3
                ),
            ),
            patch("shu.api.auth.password_auth_service") as mock_pw,
        ):
            mock_pw.create_user = AsyncMock(return_value=mock_user)

            response = await create_user(
                request,
                current_user=current_user,
                db=db,
                user_service=_make_user_service(),
                seat_service=seat_service,
                x_seat_charge_confirmed="true",
            )

        seat_service.confirm_upgrade.assert_awaited_once_with(db)
        assert response.data["email"] == "new@example.com"

    @pytest.mark.asyncio
    async def test_within_limit_writes_no_stripe_call(self):
        """At or below limit → no preview, no confirm, no 402."""
        request = CreateUserRequest(
            email="new@example.com", password="pw12345678", name="New", auth_method="password"
        )
        db = AsyncMock()
        current_user = MagicMock()
        seat_service = _make_seat_service()

        mock_user = MagicMock()
        mock_user.to_dict.return_value = {"email": "new@example.com"}

        with (
            patch(
                "shu.api.auth.check_user_limit",
                return_value=UserLimitStatus(
                    enforcement="hard", at_limit=False, current_count=1, user_limit=3
                ),
            ),
            patch("shu.api.auth.password_auth_service") as mock_pw,
        ):
            mock_pw.create_user = AsyncMock(return_value=mock_user)

            await create_user(
                request,
                current_user=current_user,
                db=db,
                user_service=_make_user_service(),
                seat_service=seat_service,
                x_seat_charge_confirmed=None,
            )

        seat_service.preview_upgrade.assert_not_awaited()
        seat_service.confirm_upgrade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stripe_upgrade_failure_aborts_before_user_insert(self):
        """Stripe failure during confirm_upgrade → HTTPException, no user creation."""
        request = CreateUserRequest(
            email="new@example.com", password="pw12345678", name="New", auth_method="password"
        )
        db = AsyncMock()
        current_user = MagicMock()
        seat_service = _make_seat_service(confirm_raises=StripeClientError("stripe down"))

        with (
            patch(
                "shu.api.auth.check_user_limit",
                return_value=UserLimitStatus(
                    enforcement="hard", at_limit=True, current_count=3, user_limit=3
                ),
            ),
            patch("shu.api.auth.password_auth_service") as mock_pw,
        ):
            mock_pw.create_user = AsyncMock()

            with pytest.raises(HTTPException) as exc_info:
                await create_user(
                    request,
                    current_user=current_user,
                    db=db,
                    user_service=_make_user_service(),
                    seat_service=seat_service,
                    x_seat_charge_confirmed="true",
                )

        assert exc_info.value.status_code == 502
        mock_pw.create_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_preview_failure_returns_402_without_proration_block(self):
        """preview_upgrade returns None → 402 still fires, proration omitted."""
        request = CreateUserRequest(
            email="new@example.com", password="pw12345678", name="New", auth_method="password"
        )
        db = AsyncMock()
        current_user = MagicMock()
        seat_service = _make_seat_service(preview=None)  # preview failed

        with patch(
            "shu.api.auth.check_user_limit",
            return_value=UserLimitStatus(
                enforcement="hard", at_limit=True, current_count=3, user_limit=3
            ),
        ):
            response = await create_user(
                request,
                current_user=current_user,
                db=db,
                user_service=_make_user_service(),
                seat_service=seat_service,
                x_seat_charge_confirmed=None,
            )

        body = _parse_envelope(response)
        assert response.status_code == 402
        assert "proration" not in body["error"]["details"]

    @pytest.mark.asyncio
    async def test_unconfigured_billing_skips_preflight_entirely(self):
        """Self-hosted (no Stripe) deploys must still be able to create users.

        Regression: ``get_seat_service`` previously raised 503 when billing
        wasn't configured, so the FastAPI dependency for ``create_user``
        failed before the function body ran. The fix returns ``None`` from
        the dep; ``_preflight_seat_charge`` short-circuits on None.
        """
        request = CreateUserRequest(
            email="self-hosted@example.com",
            password="pw12345678",
            name="SH",
            auth_method="password",
        )
        db = AsyncMock()
        current_user = MagicMock()

        mock_user = MagicMock()
        mock_user.to_dict.return_value = {"email": "self-hosted@example.com"}

        # No check_user_limit patch — preflight must short-circuit before reading limit.
        with patch("shu.api.auth.password_auth_service") as mock_pw:
            mock_pw.create_user = AsyncMock(return_value=mock_user)
            response = await create_user(
                request,
                current_user=current_user,
                db=db,
                user_service=_make_user_service(),
                seat_service=None,  # self-hosted: dep returns None
                x_seat_charge_confirmed=None,
            )

        # Phase-2 confirm wasn't called because there's nothing to confirm against.
        assert response.data["email"] == "self-hosted@example.com"
        mock_pw.create_user.assert_awaited()

    @pytest.mark.asyncio
    async def test_duplicate_email_rejected_before_stripe_charge(self):
        """Duplicate email must 400 *before* confirm_upgrade fires.

        Charging Stripe and then 400-ing on a duplicate email leaves the
        seat orphaned at Stripe with no corresponding user — the seat-charge
        is irreversible from the admin's perspective without the cancel-release
        affordance. Pre-validate uniqueness first.
        """
        request = CreateUserRequest(
            email="dup@example.com", password="pw12345678", name="Dup", auth_method="password"
        )
        db = AsyncMock()
        current_user = MagicMock()
        seat_service = _make_seat_service(preview=_make_preview())

        with (
            patch(
                "shu.api.auth.check_user_limit",
                return_value=UserLimitStatus(
                    enforcement="hard", at_limit=True, current_count=3, user_limit=3
                ),
            ),
            patch("shu.api.auth.password_auth_service") as mock_pw,
        ):
            mock_pw.create_user = AsyncMock()

            with pytest.raises(HTTPException) as exc_info:
                await create_user(
                    request,
                    current_user=current_user,
                    db=db,
                    user_service=_make_user_service(existing_email=True),
                    seat_service=seat_service,
                    x_seat_charge_confirmed="true",
                )

        assert exc_info.value.status_code == 400
        seat_service.confirm_upgrade.assert_not_awaited()
        mock_pw.create_user.assert_not_awaited()


class TestActivateUserPreflight:
    @pytest.mark.asyncio
    async def test_activate_at_limit_same_preflight_as_create(self):
        """Activating an inactive user consumes a seat → same two-phase flow."""
        db = AsyncMock()
        current_user = MagicMock()
        seat_service = _make_seat_service(preview=_make_preview())

        mock_user = MagicMock()
        mock_user.is_active = False
        user_service = MagicMock()
        user_service.get_user_by_id = AsyncMock(return_value=mock_user)

        with patch(
            "shu.api.auth.check_user_limit",
            return_value=UserLimitStatus(
                enforcement="hard", at_limit=True, current_count=3, user_limit=3
            ),
        ):
            response = await activate_user(
                "42",
                current_user=current_user,
                db=db,
                user_service=user_service,
                seat_service=seat_service,
                x_seat_charge_confirmed=None,
            )

        assert response.status_code == 402
        body = _parse_envelope(response)
        assert body["error"]["code"] == "seat_limit_reached"

    @pytest.mark.asyncio
    async def test_activate_already_active_user_skips_preflight(self):
        """Already-active user → no preflight, no double charge on redundant click."""
        db = AsyncMock()
        current_user = MagicMock()
        seat_service = _make_seat_service()

        mock_user = MagicMock()
        mock_user.is_active = True  # redundant activation
        mock_user.to_dict.return_value = {"email": "x@y.z"}
        user_service = MagicMock()
        user_service.get_user_by_id = AsyncMock(return_value=mock_user)

        # check_user_limit should not even be consulted; patch it to blow up to verify.
        with patch("shu.api.auth.check_user_limit", side_effect=AssertionError("should not be called")):
            await activate_user(
                "42",
                current_user=current_user,
                db=db,
                user_service=user_service,
                seat_service=seat_service,
                x_seat_charge_confirmed=None,
            )

        seat_service.preview_upgrade.assert_not_awaited()
        seat_service.confirm_upgrade.assert_not_awaited()
