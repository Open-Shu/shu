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
from shu.billing.entitlements import EntitlementSet, LimitSet
from shu.core.logging import get_logger

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
    "total_grant_amount": 0,
    "remaining_grant_amount": 0,
    "seat_price_usd": 0,
    "limits": {"document_count_limit": 0, "kb_count_limit": 0},
    # SHU-774 fields. `None` is a meaningful wire value (no Shu-managed sub);
    # the contract is "always emitted," so the missing-field test below
    # exercises each one.
    "subscription_status": None,
    "current_period_start": None,
    "current_period_end": None,
    "cancel_at_period_end": False,
    "canceled_at": None,
    "usage_markup_multiplier": None,
    # SHU-813: single enforcement signal for the LLM-call cap. False here so
    # the minimal payload models a non-capped tenant; capped scenarios opt in
    # via override. CP always emits it (no default-omit), so this field is
    # required on the wire — surfaced by the missing-field test below.
    "hard_cap": False,
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
        logger=get_logger("test_cp_client"),
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
        total_grant_amount=Decimal(0),
        remaining_grant_amount=Decimal(0),
        seat_price_usd=Decimal(0),
        limits=LimitSet(),
        subscription_status=None,
        current_period_start=None,
        current_period_end=None,
        cancel_at_period_end=False,
        canceled_at=None,
        usage_markup_multiplier=None,
        hard_cap=False,
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
        "total_grant_amount",
        "remaining_grant_amount",
        "seat_price_usd",
        # SHU-774 fields: CP always emits these (None when no Shu-managed
        # sub); a missing field is a contract violation, not a default.
        "limits",
        "subscription_status",
        "current_period_start",
        "current_period_end",
        "cancel_at_period_end",
        "canceled_at",
        "usage_markup_multiplier",
        # SHU-813: required on the wire so an old CP version that doesn't
        # know about hard_cap surfaces as CpMalformedResponse rather than
        # silently bypassing the LLM-cap gate.
        "hard_cap",
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
        # hard_cap type drift — StrictBool rejects strings and ints; a
        # truthy "false" would otherwise silently disable the LLM-cap gate.
        {"hard_cap": "true"},
        {"hard_cap": 1},
        # Negative grant amount → contract violation
        {"total_grant_amount": -1},
        # Negative remaining grant
        {"remaining_grant_amount": -5},
        # Negative seat price
        {"seat_price_usd": -10},
        # SHU-774: Literal["trialing", "active", "past_due", "canceled", "unpaid"]
        # rejects anything else. Catches Stripe enum drift (e.g. "incomplete",
        # "paused") that would otherwise silently bypass the cancel gate.
        {"subscription_status": "incomplete"},
        # Capitalization / typo on CP's side — same gate-bypass risk.
        {"subscription_status": "Canceled"},
        # cancel_at_period_end is StrictBool — string for bool is type drift.
        {"cancel_at_period_end": "true"},
        # Naive datetime on the new period / canceled fields.
        {"current_period_start": "2026-05-01T00:00:00"},
        {"current_period_end": "2026-06-01T00:00:00"},
        {"canceled_at": "2026-05-15T12:00:00"},
        # Unix timestamps on the new datetime fields — rejected by the
        # same shared validator that covers payment_failed_at / trial_deadline.
        {"current_period_start": 1714478096},
        {"canceled_at": 1715785696},
        # Non-positive usage markup → Field(gt=0) violation.
        {"usage_markup_multiplier": 0},
        {"usage_markup_multiplier": -1},
    ],
    ids=[
        "string-for-bool",
        "int-for-bool",
        "string-for-int",
        "negative-grace-days",
        "naive-datetime",
        "int-for-datetime",
        "string-for-hard-cap",
        "int-for-hard-cap",
        "negative-total-grant",
        "negative-remaining-grant",
        "negative-seat-price",
        "unknown-subscription-status",
        "miscased-subscription-status",
        "string-for-cancel-at-period-end",
        "naive-current-period-start",
        "naive-current-period-end",
        "naive-canceled-at",
        "int-for-current-period-start",
        "int-for-canceled-at",
        "zero-usage-markup-multiplier",
        "negative-usage-markup-multiplier",
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
    """Round-trip wire fields + verify the derived trial accessors.

    `is_trial` and `trial_deadline` were dropped from the wire (SHU-813)
    and re-derived from `subscription_status` and `current_period_end`.
    """
    payload = _payload(
        entitlements={
            "chat": True,
            "plugins": True,
            "experiences": False,
            "provider_management": False,
            "model_config_management": True,
            "mcp_servers": False,
        },
        subscription_status="trialing",
        current_period_start="2026-05-01T00:00:00Z",
        current_period_end="2026-05-30T12:00:00Z",
        total_grant_amount="50.00",
        remaining_grant_amount="42.50",
        seat_price_usd="20.00",
        hard_cap=True,
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
    assert state.hard_cap is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_value", "expected_is_trial"),
    [
        ("trialing", True),
        ("active", False),
        ("past_due", False),
        ("canceled", False),
        ("unpaid", False),
        (None, False),
    ],
)
async def test_is_trial_property_derives_from_subscription_status(
    status_value: str | None, expected_is_trial: bool
) -> None:
    """Derived `is_trial` mirrors `subscription_status == "trialing"`.

    Pins the SHU-813 contract: the deprecated wire fields stay off the wire
    and the accessor that replaces them only treats `trialing` as a trial.
    """
    payload = _payload(subscription_status=status_value)
    http_client = _http_client_returning(_ok_response(payload))

    state = await _make_client(http_client).fetch_billing_state()

    assert state.is_trial is expected_is_trial


@pytest.mark.asyncio
async def test_trial_deadline_returns_none_outside_trialing() -> None:
    """`current_period_end` outside `trialing` is the regular cycle end, not
    a trial deadline — the derived accessor must hide it.
    """
    payload = _payload(
        subscription_status="active",
        current_period_start="2026-05-01T00:00:00Z",
        current_period_end="2026-06-01T00:00:00Z",
    )
    http_client = _http_client_returning(_ok_response(payload))

    state = await _make_client(http_client).fetch_billing_state()

    assert state.is_trial is False
    assert state.trial_deadline is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_value",
    ["trialing", "active", "past_due", "canceled", "unpaid"],
)
async def test_200_parses_each_subscription_status_literal(status_value: str) -> None:
    """Pin the Literal members. If CP starts emitting a status we accept,
    we want a positive round-trip test; if someone removes a member from
    the Literal (or Stripe drops one), this test fails loudly.
    """
    payload = _payload(
        subscription_status=status_value,
        current_period_start="2026-05-01T00:00:00Z",
        current_period_end="2026-06-01T00:00:00Z",
    )
    http_client = _http_client_returning(_ok_response(payload))

    state = await _make_client(http_client).fetch_billing_state()

    assert state.subscription_status == status_value


@pytest.mark.asyncio
async def test_200_populates_shu_774_fields_from_payload() -> None:
    """End-to-end parse of every SHU-774 wire field — period bounds,
    canceled_at, cancel_at_period_end, limits, usage_markup_multiplier.
    Locks the parse path so a wire-format change surfaces here.
    """
    payload = _payload(
        subscription_status="active",
        current_period_start="2026-05-01T00:00:00Z",
        current_period_end="2026-06-01T00:00:00Z",
        cancel_at_period_end=True,
        canceled_at="2026-05-15T08:30:00Z",
        usage_markup_multiplier="1.25",
        limits={"document_count_limit": 100, "kb_count_limit": 5},
    )
    http_client = _http_client_returning(_ok_response(payload))

    state = await _make_client(http_client).fetch_billing_state()

    assert state.subscription_status == "active"
    assert state.current_period_start == datetime(2026, 5, 1, tzinfo=timezone.utc)
    assert state.current_period_end == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert state.cancel_at_period_end is True
    assert state.canceled_at == datetime(2026, 5, 15, 8, 30, tzinfo=timezone.utc)
    assert state.usage_markup_multiplier == Decimal("1.25")
    assert state.limits == LimitSet(document_count_limit=100, kb_count_limit=5)


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


@pytest.mark.asyncio
async def test_deprecated_trial_fields_from_old_cp_are_ignored() -> None:
    """Rollout lock for the SHU-813 wire shape change.

    CP still emits `is_trial` and `trial_deadline` for the migration window
    (they were dropped from this tenant's schema but kept upstream). The
    parser must ignore both — and the derived `is_trial` / `trial_deadline`
    accessors must read from `subscription_status` / `current_period_end`,
    not the legacy wire fields. Without this lock, a CP rollback that
    re-emits a different value for those fields could silently flip the
    tenant's view of trial state.
    """
    # Wire-side mismatch: legacy `is_trial=True` but `subscription_status`
    # is `"active"`. The derived accessor must trust `subscription_status`,
    # not the legacy boolean — otherwise an old CP version's stale wire
    # field could override the new source of truth.
    payload = _payload(
        is_trial=True,
        trial_deadline="2026-05-30T12:00:00Z",
        subscription_status="active",
        current_period_start="2026-05-01T00:00:00Z",
        current_period_end="2026-06-01T00:00:00Z",
    )
    http_client = _http_client_returning(_ok_response(payload))

    state = await _make_client(http_client).fetch_billing_state()

    assert state.is_trial is False
    assert state.trial_deadline is None


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
