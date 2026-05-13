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
from decimal import Decimal
from typing import Any
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
from shu.billing.entitlements import EntitlementSet

BASE_URL = "https://cp.example.test"
TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
SHARED_SECRET = "a" * 64
EXPECTED_PATH = f"/api/v1/tenants/{TENANT_ID}/billing-state"


# Minimal valid CP wire payload. Tests start from here and override the
# fields they exercise; without a baseline the type-drift parametrized
# tests would fail for "missing entitlements" rather than the drift they
# actually target.
_MIN_PAYLOAD: dict[str, Any] = {
    "openrouter_key_disabled": False,
    "payment_failed_at": None,
    "payment_grace_days": 0,
    "entitlements": {
        "chat": True,
        "plugins": False,
        "experiences": False,
        "provider_management": False,
        "model_config_management": False,
        "mcp_servers": False,
    },
    "is_trial": False,
    "trial_deadline": None,
    "total_grant_amount": 0,
    "remaining_grant_amount": 0,
    "seat_price_usd": 0,
}


def _payload(**overrides: Any) -> dict[str, Any]:
    return {**_MIN_PAYLOAD, **overrides}


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
    http_client = _http_client_returning(_ok_response(_payload()))

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
        _ok_response(_payload(openrouter_key_disabled=True, payment_grace_days=7))
    )

    state = await _make_client(http_client).fetch_billing_state()

    assert state == BillingState(
        openrouter_key_disabled=True,
        payment_failed_at=None,
        payment_grace_days=7,
        entitlements=EntitlementSet(),
        is_trial=False,
        trial_deadline=None,
        total_grant_amount=Decimal(0),
        remaining_grant_amount=Decimal(0),
        seat_price_usd=Decimal(0),
    )


@pytest.mark.asyncio
async def test_200_parses_iso8601_payment_failed_at_to_tz_aware_datetime() -> None:
    http_client = _http_client_returning(
        _ok_response(
            _payload(
                openrouter_key_disabled=True,
                payment_failed_at="2026-04-30T12:34:56Z",
                payment_grace_days=5,
            )
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
    "missing_field",
    [
        "openrouter_key_disabled",
        "payment_failed_at",
        "payment_grace_days",
        "entitlements",
        "is_trial",
        "trial_deadline",
        "total_grant_amount",
        "remaining_grant_amount",
        "seat_price_usd",
    ],
)
async def test_missing_field_raises_cp_malformed_response(missing_field: str) -> None:
    payload = _payload()
    del payload[missing_field]
    http_client = _http_client_returning(_ok_response(payload))

    with pytest.raises(CpMalformedResponse):
        await _make_client(http_client).fetch_billing_state()


@pytest.mark.asyncio
async def test_bad_iso_datetime_raises_cp_malformed_response() -> None:
    http_client = _http_client_returning(
        _ok_response(_payload(payment_failed_at="not-a-date"))
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
    "overrides",
    [
        # String for bool → would be truthy if not validated
        {"openrouter_key_disabled": "false"},
        # Int for bool — pydantic StrictBool rejects 0/1 too
        {"openrouter_key_disabled": 0},
        # String for int → would break int comparison/arithmetic
        {"payment_grace_days": "7"},
        # Negative grace_days → contract violation
        {"payment_grace_days": -1},
        # Naive datetime → would crash tz-aware downstream arithmetic
        {"payment_failed_at": "2026-04-30T12:34:56"},
        # Non-string for the datetime field
        {"payment_failed_at": 1714478096},
        # is_trial type drift
        {"is_trial": "true"},
        # Naive trial_deadline
        {"trial_deadline": "2026-05-30T12:34:56"},
        # Unix timestamp for trial_deadline
        {"trial_deadline": 1714478096},
        # Negative grant amount → contract violation
        {"total_grant_amount": -1},
        # Negative remaining grant
        {"remaining_grant_amount": -5},
        # Negative seat price
        {"seat_price_usd": -10},
    ],
    ids=[
        "string-for-bool",
        "int-for-bool",
        "string-for-int",
        "negative-grace-days",
        "naive-datetime",
        "int-for-datetime",
        "string-for-is-trial",
        "naive-trial-deadline",
        "int-for-trial-deadline",
        "negative-total-grant",
        "negative-remaining-grant",
        "negative-seat-price",
    ],
)
async def test_type_drift_raises_cp_malformed_response(overrides: dict) -> None:
    http_client = _http_client_returning(_ok_response(_payload(**overrides)))

    with pytest.raises(CpMalformedResponse):
        await _make_client(http_client).fetch_billing_state()


# Tier/trial payload coverage: lock down the parse path for the new fields
# end-to-end, so a CP wire-format change to any of them surfaces here rather
# than as an unexplained 0/None downstream.


@pytest.mark.asyncio
async def test_200_populates_entitlements_and_trial_fields_from_payload() -> None:
    payload = _payload(
        entitlements={
            "chat": True,
            "plugins": True,
            "experiences": False,
            "provider_management": False,
            "model_config_management": True,
            "mcp_servers": False,
        },
        is_trial=True,
        trial_deadline="2026-05-30T12:00:00Z",
        total_grant_amount="50.00",
        remaining_grant_amount="42.50",
        seat_price_usd="20.00",
    )
    http_client = _http_client_returning(_ok_response(payload))

    state = await _make_client(http_client).fetch_billing_state()

    assert state.entitlements == EntitlementSet(
        chat=True,
        plugins=True,
        model_config_management=True,
    )
    assert state.is_trial is True
    assert state.trial_deadline == datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    assert state.total_grant_amount == Decimal("50.00")
    assert state.remaining_grant_amount == Decimal("42.50")
    assert state.seat_price_usd == Decimal("20.00")


@pytest.mark.asyncio
async def test_unknown_extra_field_is_ignored_for_forward_compat() -> None:
    """Forward-compatibility lock: if CP adds a new field the tenant has
    not learned about yet, the parser must accept the payload — otherwise
    every CP additive release would break existing tenants. ``extra="ignore"``
    on the dataclass is what guarantees this; this test pins the contract.
    """
    payload = _payload(future_field_we_dont_know_about="something")
    http_client = _http_client_returning(_ok_response(payload))

    state = await _make_client(http_client).fetch_billing_state()

    assert state.openrouter_key_disabled is False
    assert not hasattr(state, "future_field_we_dont_know_about")


# Trial-action POST endpoints — `post_upgrade_now` and `post_cancel_subscription`
# share `_send_signed` + `_classify_trial_action_response`. Both currently
# require a trialing subscription on CP side and emit 409 otherwise, so
# the 409 → `CpNoActiveTrial` branch is exercised against both.


_UPGRADE_NOW_PATH = f"/api/v1/tenants/{TENANT_ID}/upgrade-now"
_CANCEL_SUBSCRIPTION_PATH = f"/api/v1/tenants/{TENANT_ID}/cancel-subscription"


def _http_post_client_returning(response: MagicMock) -> MagicMock:
    client = MagicMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(return_value=response)
    return client


def _http_post_client_raising(exc: Exception) -> MagicMock:
    client = MagicMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(side_effect=exc)
    return client


def _expected_post_signature(secret: str, timestamp: int, path: str) -> str:
    """HMAC over the POST canonical string for a given path + empty body."""
    canonical = f"{timestamp}.POST.{path}.".encode()
    digest = hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()
    return f"v1={digest}"


_POST_METHODS = [
    ("post_upgrade_now", _UPGRADE_NOW_PATH),
    ("post_cancel_subscription", _CANCEL_SUBSCRIPTION_PATH),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name, expected_path", _POST_METHODS)
async def test_signed_post_uses_byte_correct_hmac_and_target_url(
    method_name: str, expected_path: str
) -> None:
    """Signature is over the canonical POST string for the right path
    with an empty body. Pins the wire format so a regression in
    `sign_envelope` or a typo in the constructed path surfaces here.
    """
    http_client = _http_post_client_returning(_error_response(200, ""))
    client = _make_client(http_client)

    await getattr(client, method_name)()

    http_client.post.assert_awaited_once()
    call = http_client.post.await_args
    assert call.args[0] == f"{BASE_URL}{expected_path}"
    headers = call.kwargs["headers"]
    timestamp = int(headers["X-Shu-Router-Timestamp"])
    assert headers["X-Shu-Router-Signature"] == _expected_post_signature(
        SHARED_SECRET, timestamp, expected_path
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name, _path", _POST_METHODS)
async def test_post_2xx_returns_none(method_name: str, _path: str) -> None:
    """A 2xx response means CP accepted the action. Methods return None —
    the next billing-state poll surfaces the new state to the tenant.
    """
    http_client = _http_post_client_returning(_error_response(200, ""))
    client = _make_client(http_client)

    result = await getattr(client, method_name)()
    assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name, _path", _POST_METHODS)
async def test_post_401_raises_cp_auth_failed(method_name: str, _path: str) -> None:
    http_client = _http_post_client_returning(_error_response(401, "signature_invalid"))
    client = _make_client(http_client)

    with pytest.raises(CpAuthFailed):
        await getattr(client, method_name)()


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name, _path", _POST_METHODS)
async def test_post_5xx_raises_cp_unexpected_status(method_name: str, _path: str) -> None:
    http_client = _http_post_client_returning(_error_response(500, "internal boom"))
    client = _make_client(http_client)

    with pytest.raises(CpUnexpectedStatus) as exc_info:
        await getattr(client, method_name)()

    assert exc_info.value.status == 500
    assert exc_info.value.body == "internal boom"


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name, _path", _POST_METHODS)
async def test_post_network_error_raises_cp_unreachable(method_name: str, _path: str) -> None:
    http_client = _http_post_client_raising(httpx.ConnectError("refused"))
    client = _make_client(http_client)

    with pytest.raises(CpUnreachable):
        await getattr(client, method_name)()


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name, _path", _POST_METHODS)
async def test_post_409_raises_cp_no_active_trial(method_name: str, _path: str) -> None:
    """Both CP trial-action endpoints require a trialing subscription and
    emit 409 otherwise. Typed as `CpNoActiveTrial` so the admin router can
    re-emit 409 with a clear message instead of collapsing to 502.
    """
    from shu.billing.cp_client import CpNoActiveTrial

    http_client = _http_post_client_returning(_error_response(409, "no active trial"))
    client = _make_client(http_client)

    with pytest.raises(CpNoActiveTrial) as exc_info:
        await getattr(client, method_name)()

    assert exc_info.value.status_code == 409
