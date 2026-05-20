"""Tests for shu.billing.billing_state_persister.

Coverage focus: round-trip preserves the dataclass shape (including
Decimal/datetime/EntitlementSet), absent key returns None, and a
schema-drift blob (older saved value missing newly-required fields)
is dropped on the floor instead of crashing the boot path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from shu.billing.billing_state_persister import PERSIST_KEY, BillingStatePersister
from shu.billing.cp_client import BillingState
from shu.billing.entitlements import EntitlementSet


def _state(**overrides: Any) -> BillingState:
    base: dict[str, Any] = {
        "openrouter_key_disabled": False,
        "payment_failed_at": None,
        "payment_grace_days": 0,
        "entitlements": EntitlementSet(plugins=True),
        "is_trial": True,
        "trial_deadline": datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc),
        "total_grant_amount": Decimal("50.00"),
        "remaining_grant_amount": Decimal("12.34"),
        "seat_price_usd": Decimal("20.00"),
    }
    base.update(overrides)
    return BillingState(**base)


class _StubSession:
    """Stand-in for AsyncSession that captures upsert/get_value calls.

    Wires into the SystemSettingsService through SQLAlchemy's session API:
    we mock the methods the service actually calls.
    """

    def __init__(self, stored: dict[str, dict[str, Any]] | None = None) -> None:
        self.stored: dict[str, dict[str, Any]] = stored if stored is not None else {}

    async def __aenter__(self) -> "_StubSession":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None


def _session_factory_for(stored: dict[str, dict[str, Any]]) -> MagicMock:
    """Build a session_factory whose sessions delegate SystemSettingsService
    operations to a shared `stored` dict.

    Patching `SystemSettingsService` directly is cleaner than reaching into
    SQLAlchemy internals for these read/write tests.
    """
    session = _StubSession(stored)
    factory = MagicMock(return_value=MagicMock(return_value=session))
    return factory


@pytest.mark.asyncio
async def test_save_then_load_round_trips_all_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    stored: dict[str, dict[str, Any]] = {}

    async def fake_upsert(self: Any, key: str, value: dict[str, Any]) -> Any:
        stored[key] = value
        return MagicMock()

    async def fake_get_value(self: Any, key: str, default: Any = None) -> Any:
        return stored.get(key, default)

    monkeypatch.setattr(
        "shu.billing.billing_state_persister.SystemSettingsService.upsert", fake_upsert
    )
    monkeypatch.setattr(
        "shu.billing.billing_state_persister.SystemSettingsService.get_value", fake_get_value
    )

    persister = BillingStatePersister(session_factory=_session_factory_for(stored))
    original = _state()

    await persister.save(original)
    assert PERSIST_KEY in stored

    restored = await persister.load()
    assert restored == original


@pytest.mark.asyncio
async def test_load_returns_none_when_key_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_value(self: Any, key: str, default: Any = None) -> Any:
        return default

    monkeypatch.setattr(
        "shu.billing.billing_state_persister.SystemSettingsService.get_value", fake_get_value
    )

    persister = BillingStatePersister(session_factory=_session_factory_for({}))
    assert await persister.load() is None


@pytest.mark.asyncio
async def test_load_returns_none_when_blob_fails_schema_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persisted blob written before a schema field was added should be
    ignored, not crash the boot path. The cache then falls back to
    HEALTHY_DEFAULT via the existing path.
    """

    # Pre-extension shape (missing entitlements, is_trial, grants, ...).
    # A real older deployment would have saved exactly this, then we'd
    # upgrade and try to load it.
    legacy_blob = {
        "openrouter_key_disabled": False,
        "payment_failed_at": None,
        "payment_grace_days": 0,
    }

    async def fake_get_value(self: Any, key: str, default: Any = None) -> Any:
        return legacy_blob

    monkeypatch.setattr(
        "shu.billing.billing_state_persister.SystemSettingsService.get_value", fake_get_value
    )

    persister = BillingStatePersister(session_factory=_session_factory_for({}))
    assert await persister.load() is None


@pytest.mark.asyncio
async def test_save_swallows_db_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Persistence is best-effort. A write failure shouldn't propagate —
    the in-memory cache value is already correct.
    """

    async def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("db blew up")

    monkeypatch.setattr(
        "shu.billing.billing_state_persister.SystemSettingsService.upsert", boom
    )

    persister = BillingStatePersister(session_factory=_session_factory_for({}))
    # Must not raise.
    await persister.save(_state())
