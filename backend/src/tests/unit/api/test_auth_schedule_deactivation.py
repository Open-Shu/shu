"""Tests for schedule-deactivation endpoints (POST/DELETE)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from shu.api.auth import schedule_user_deactivation, unschedule_user_deactivation
from shu.billing.seat_service import (
    SeatMinimumError,
    UserNotFoundError,
    UserStateError,
)
from shu.billing.stripe_client import StripeClientError


def _make_seat_service(raise_on: str | None = None, exc: Exception | None = None) -> MagicMock:
    svc = MagicMock()
    svc.flag_user = AsyncMock(side_effect=exc if raise_on == "flag" else None)
    svc.unflag_user = AsyncMock(side_effect=exc if raise_on == "unflag" else None)
    return svc


def _make_user_service(user=None) -> MagicMock:
    svc = MagicMock()
    svc.get_user_by_id = AsyncMock(return_value=user)
    return svc


class TestScheduleUserDeactivation:
    @pytest.mark.asyncio
    async def test_happy_path_flags_and_returns_updated_user(self):
        db = AsyncMock()
        current_user = MagicMock()
        seat_service = _make_seat_service()
        mock_user = MagicMock()
        mock_user.to_dict.return_value = {"email": "u@x.com", "deactivation_scheduled_at": "..."}
        user_service = _make_user_service(mock_user)

        response = await schedule_user_deactivation(
            "7",
            _current_user=current_user,
            db=db,
            user_service=user_service,
            seat_service=seat_service,
        )

        seat_service.flag_user.assert_awaited_once_with(db, "7")
        assert response.data["email"] == "u@x.com"

    @pytest.mark.asyncio
    async def test_unknown_user_from_service_maps_to_404(self):
        db = AsyncMock()
        seat_service = _make_seat_service(raise_on="flag", exc=UserNotFoundError("no user"))
        user_service = _make_user_service(None)

        with pytest.raises(HTTPException) as exc:
            await schedule_user_deactivation(
                "99",
                _current_user=MagicMock(),
                db=db,
                user_service=user_service,
                seat_service=seat_service,
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_already_flagged_maps_to_409(self):
        seat_service = _make_seat_service(raise_on="flag", exc=UserStateError("already flagged"))
        with pytest.raises(HTTPException) as exc:
            await schedule_user_deactivation(
                "7",
                _current_user=MagicMock(),
                db=AsyncMock(),
                user_service=_make_user_service(MagicMock()),
                seat_service=seat_service,
            )
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_seat_minimum_maps_to_400(self):
        seat_service = _make_seat_service(raise_on="flag", exc=SeatMinimumError("below min"))
        with pytest.raises(HTTPException) as exc:
            await schedule_user_deactivation(
                "7",
                _current_user=MagicMock(),
                db=AsyncMock(),
                user_service=_make_user_service(MagicMock()),
                seat_service=seat_service,
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_stripe_error_maps_to_502(self):
        seat_service = _make_seat_service(raise_on="flag", exc=StripeClientError("stripe down"))
        with pytest.raises(HTTPException) as exc:
            await schedule_user_deactivation(
                "7",
                _current_user=MagicMock(),
                db=AsyncMock(),
                user_service=_make_user_service(MagicMock()),
                seat_service=seat_service,
            )
        assert exc.value.status_code == 502


class TestUnscheduleUserDeactivation:
    @pytest.mark.asyncio
    async def test_happy_path_unflags(self):
        db = AsyncMock()
        seat_service = _make_seat_service()
        mock_user = MagicMock()
        mock_user.to_dict.return_value = {"email": "u@x.com"}

        response = await unschedule_user_deactivation(
            "7",
            _current_user=MagicMock(),
            db=db,
            user_service=_make_user_service(mock_user),
            seat_service=seat_service,
        )

        seat_service.unflag_user.assert_awaited_once_with(db, "7")
        assert response.data["email"] == "u@x.com"

    @pytest.mark.asyncio
    async def test_not_flagged_maps_to_409(self):
        seat_service = _make_seat_service(raise_on="unflag", exc=UserStateError("not flagged"))
        with pytest.raises(HTTPException) as exc:
            await unschedule_user_deactivation(
                "7",
                _current_user=MagicMock(),
                db=AsyncMock(),
                user_service=_make_user_service(MagicMock()),
                seat_service=seat_service,
            )
        assert exc.value.status_code == 409
