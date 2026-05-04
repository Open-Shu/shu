"""Tests for POST /billing/seats/release and POST /billing/seats/cancel-release."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from shu.billing.enforcement import UserLimitStatus
from shu.billing.router import cancel_pending_release, release_open_seat
from shu.billing.seat_service import SeatMinimumError
from shu.billing.stripe_client import StripeClientError


def _make_seat_service(
    *,
    result: UserLimitStatus | None = None,
    raises: Exception | None = None,
) -> MagicMock:
    svc = MagicMock()
    if raises is not None:
        svc.release_open_seat = AsyncMock(side_effect=raises)
    else:
        svc.release_open_seat = AsyncMock(
            return_value=result
            or UserLimitStatus(
                enforcement="hard", at_limit=False, current_count=2, user_limit=3
            )
        )
    return svc


def _parse(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


class TestReleaseOpenSeatEndpoint:
    @pytest.mark.asyncio
    async def test_happy_path_returns_fresh_user_limit_status(self):
        db = AsyncMock()
        seat_service = _make_seat_service(
            result=UserLimitStatus(
                enforcement="hard", at_limit=False, current_count=2, user_limit=3
            )
        )

        response = await release_open_seat(db=db, seat_service=seat_service)

        body = _parse(response)
        assert body["data"]["user_limit"] == 3
        assert body["data"]["user_count"] == 2
        assert body["data"]["user_limit_enforcement"] == "hard"
        assert body["data"]["at_user_limit"] is False

    @pytest.mark.asyncio
    async def test_below_one_minimum_maps_to_400_with_code(self):
        seat_service = _make_seat_service(raises=SeatMinimumError("below 1"))

        response = await release_open_seat(db=AsyncMock(), seat_service=seat_service)

        body = _parse(response)
        assert response.status_code == 400
        assert body["error"]["code"] == "cannot_release_below_minimum"

    @pytest.mark.asyncio
    async def test_stripe_error_maps_to_502(self):
        seat_service = _make_seat_service(raises=StripeClientError("stripe down"))

        with pytest.raises(HTTPException) as exc:
            await release_open_seat(db=AsyncMock(), seat_service=seat_service)
        assert exc.value.status_code == 502


def _make_cancel_seat_service(
    *,
    result: UserLimitStatus | None = None,
    raises: Exception | None = None,
) -> MagicMock:
    svc = MagicMock()
    if raises is not None:
        svc.cancel_pending_release = AsyncMock(side_effect=raises)
    else:
        svc.cancel_pending_release = AsyncMock(
            return_value=result
            or UserLimitStatus(
                enforcement="hard", at_limit=False, current_count=4, user_limit=5
            )
        )
    return svc


class TestCancelPendingReleaseEndpoint:
    @pytest.mark.asyncio
    async def test_happy_path_returns_fresh_user_limit_status(self):
        seat_service = _make_cancel_seat_service(
            result=UserLimitStatus(
                enforcement="hard", at_limit=False, current_count=4, user_limit=5
            )
        )
        response = await cancel_pending_release(db=AsyncMock(), seat_service=seat_service)

        body = _parse(response)
        assert body["data"]["user_limit"] == 5
        assert body["data"]["user_count"] == 4
        seat_service.cancel_pending_release.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stripe_error_maps_to_502(self):
        seat_service = _make_cancel_seat_service(raises=StripeClientError("stripe down"))

        with pytest.raises(HTTPException) as exc:
            await cancel_pending_release(db=AsyncMock(), seat_service=seat_service)
        assert exc.value.status_code == 502
