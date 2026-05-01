"""Tests for shu.billing.cp_client — HMAC-signed CP poller.

Coverage focus: response classification (200 / 401 / 5xx / network), wire-
format correctness of the signed GET, and the first-401-ERROR / subsequent-
WARNING log policy. Signature byte-correctness is asserted against an
independently computed HMAC so a regression in `sign_envelope` or the
canonical-string layout would break this test, not just the round-trip
unit test in test_router_envelope.py.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest

from shu.billing.cp_client import (
    BillingState,
    CpAuthFailed,
    CpClient,
    CpMalformedResponse,
    CpUnexpectedStatus,
    CpUnreachable,
)

BASE_URL = "https://cp.example.test"
TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
SHARED_SECRET = "a" * 64
EXPECTED_PATH = f"/api/v1/tenants/{TENANT_ID}/billing-state"


def _expected_signature(secret: str, timestamp: int) -> str:
    canonical = f"{timestamp}.GET.{EXPECTED_PATH}.".encode()
    digest = hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()
    return f"v1={digest}"


def _http_client_returning(response: MagicMock) -> MagicMock:
    client = MagicMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=response)
    return client


def _http_client_raising(exc: Exception) -> MagicMock:
    client = MagicMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=exc)
    return client


def _ok_response(payload: dict) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.json = MagicMock(return_value=payload)
    return response


def _error_response(status: int, body: str = "boom") -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.text = body
    response.json = MagicMock(return_value={"error": body})
    return response


def _make_client(http_client: MagicMock) -> CpClient:
    return CpClient(
        base_url=BASE_URL,
        tenant_id=TENANT_ID,
        shared_secret=SHARED_SECRET,
        http_client=http_client,
        logger=logging.getLogger("test_cp_client"),
    )


@pytest.mark.asyncio
async def test_signed_get_uses_byte_correct_hmac_and_target_url() -> None:
    http_client = _http_client_returning(
        _ok_response(
            {
                "openrouter_key_disabled": False,
                "payment_failed_at": None,
                "payment_grace_days": 0,
            }
        )
    )

    await _make_client(http_client).fetch_billing_state()

    http_client.get.assert_awaited_once()
    call = http_client.get.await_args
    assert call.args[0] == f"{BASE_URL}{EXPECTED_PATH}"
    headers = call.kwargs["headers"]
    timestamp = int(headers["X-Shu-Router-Timestamp"])
    assert headers["X-Shu-Router-Signature"] == _expected_signature(
        SHARED_SECRET, timestamp
    )


@pytest.mark.asyncio
async def test_200_with_null_payment_failed_at_returns_billing_state() -> None:
    http_client = _http_client_returning(
        _ok_response(
            {
                "openrouter_key_disabled": True,
                "payment_failed_at": None,
                "payment_grace_days": 7,
            }
        )
    )

    state = await _make_client(http_client).fetch_billing_state()

    assert state == BillingState(
        openrouter_key_disabled=True,
        payment_failed_at=None,
        payment_grace_days=7,
    )


@pytest.mark.asyncio
async def test_200_parses_iso8601_payment_failed_at_to_tz_aware_datetime() -> None:
    http_client = _http_client_returning(
        _ok_response(
            {
                "openrouter_key_disabled": True,
                "payment_failed_at": "2026-04-30T12:34:56Z",
                "payment_grace_days": 5,
            }
        )
    )

    state = await _make_client(http_client).fetch_billing_state()

    assert state.payment_failed_at == datetime(
        2026, 4, 30, 12, 34, 56, tzinfo=timezone.utc
    )


@pytest.mark.asyncio
async def test_401_raises_cp_auth_failed_and_logs_error_on_first_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    http_client = _http_client_returning(_error_response(401, "signature_invalid"))
    client = _make_client(http_client)

    with caplog.at_level(logging.ERROR, logger="test_cp_client"):
        with pytest.raises(CpAuthFailed):
            await client.fetch_billing_state()

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(error_records) == 1
    msg = error_records[0].getMessage()
    assert "SHU_TENANT_ID" in msg and "SHU_ROUTER_SHARED_SECRET" in msg


@pytest.mark.asyncio
async def test_401_logs_warning_on_subsequent_calls_per_instance(
    caplog: pytest.LogCaptureFixture,
) -> None:
    http_client = _http_client_returning(_error_response(401))
    client = _make_client(http_client)

    with caplog.at_level(logging.DEBUG, logger="test_cp_client"):
        with pytest.raises(CpAuthFailed):
            await client.fetch_billing_state()
        with pytest.raises(CpAuthFailed):
            await client.fetch_billing_state()

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(error_records) == 1, "first 401 should log ERROR exactly once"
    assert len(warning_records) == 1, "second 401 should log WARNING"


@pytest.mark.asyncio
async def test_500_raises_cp_unexpected_status_with_status_and_body() -> None:
    http_client = _http_client_returning(_error_response(500, "internal boom"))

    with pytest.raises(CpUnexpectedStatus) as exc_info:
        await _make_client(http_client).fetch_billing_state()

    assert exc_info.value.status == 500
    assert exc_info.value.body == "internal boom"


@pytest.mark.asyncio
async def test_connect_error_raises_cp_unreachable() -> None:
    http_client = _http_client_raising(httpx.ConnectError("refused"))

    with pytest.raises(CpUnreachable):
        await _make_client(http_client).fetch_billing_state()


# Malformed-200 paths: a buggy or version-skewed CP can return 200 with a
# body that doesn't match the contract. The cache only catches CpClientError,
# so without the wrap these would escape to consumers and break fail-open.


def _malformed_json_response() -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.json = MagicMock(side_effect=json.JSONDecodeError("nope", "doc", 0))
    return response


@pytest.mark.asyncio
async def test_malformed_json_raises_cp_malformed_response() -> None:
    http_client = _http_client_returning(_malformed_json_response())

    with pytest.raises(CpMalformedResponse):
        await _make_client(http_client).fetch_billing_state()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        # Missing payment_failed_at
        {"openrouter_key_disabled": False, "payment_grace_days": 0},
        # Missing openrouter_key_disabled
        {"payment_failed_at": None, "payment_grace_days": 0},
        # Missing payment_grace_days
        {"openrouter_key_disabled": False, "payment_failed_at": None},
    ],
    ids=["missing-failed-at", "missing-disabled", "missing-grace-days"],
)
async def test_missing_field_raises_cp_malformed_response(payload: dict) -> None:
    http_client = _http_client_returning(_ok_response(payload))

    with pytest.raises(CpMalformedResponse):
        await _make_client(http_client).fetch_billing_state()


@pytest.mark.asyncio
async def test_bad_iso_datetime_raises_cp_malformed_response() -> None:
    http_client = _http_client_returning(
        _ok_response(
            {
                "openrouter_key_disabled": False,
                "payment_failed_at": "not-a-date",
                "payment_grace_days": 0,
            }
        )
    )

    with pytest.raises(CpMalformedResponse):
        await _make_client(http_client).fetch_billing_state()


@pytest.mark.asyncio
async def test_list_payload_raises_cp_malformed_response() -> None:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.json = MagicMock(return_value=["not", "a", "dict"])
    http_client = _http_client_returning(response)

    with pytest.raises(CpMalformedResponse):
        await _make_client(http_client).fetch_billing_state()


# Type-drift coverage: the schema is StrictBool / NonNegativeInt /
# AwareDatetime so a CP version-skew that sends string-encoded booleans /
# ints, naive datetimes, or negative grace_days surfaces as
# CpMalformedResponse rather than silently corrupting downstream
# enforcement (e.g. truthy "false" string locking healthy users out, or
# naive timestamps crashing tz-aware arithmetic).


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        # String for bool → would be truthy if not validated
        {"openrouter_key_disabled": "false", "payment_failed_at": None, "payment_grace_days": 0},
        # Int for bool — pydantic StrictBool rejects 0/1 too
        {"openrouter_key_disabled": 0, "payment_failed_at": None, "payment_grace_days": 0},
        # String for int → would break int comparison/arithmetic
        {"openrouter_key_disabled": False, "payment_failed_at": None, "payment_grace_days": "7"},
        # Negative grace_days → contract violation
        {"openrouter_key_disabled": False, "payment_failed_at": None, "payment_grace_days": -1},
        # Naive datetime → would crash tz-aware downstream arithmetic
        {"openrouter_key_disabled": False, "payment_failed_at": "2026-04-30T12:34:56", "payment_grace_days": 0},
        # Non-string for the datetime field
        {"openrouter_key_disabled": False, "payment_failed_at": 1714478096, "payment_grace_days": 0},
    ],
    ids=[
        "string-for-bool",
        "int-for-bool",
        "string-for-int",
        "negative-grace-days",
        "naive-datetime",
        "int-for-datetime",
    ],
)
async def test_type_drift_raises_cp_malformed_response(payload: dict) -> None:
    http_client = _http_client_returning(_ok_response(payload))

    with pytest.raises(CpMalformedResponse):
        await _make_client(http_client).fetch_billing_state()
