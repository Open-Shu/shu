"""Integration tests for GET /api/v1/billing/usage/me (SHU-844).

Verifies the per-user "My Usage" endpoint:
- scopes strictly to the requesting user (one user never sees another's usage),
- excludes BYOK (is_system_managed=False) providers,
- falls back to the current UTC calendar month when neither CP nor Stripe
  supplies a billing period (so the response always has a resolved period),
- buckets the by_day series per UTC day per model.

Framework: the custom async runner (NOT pytest). Test functions take
(client, db, auth_headers). Run a single suite in-container, e.g.:
    docker exec shu-api-dev sh -lc \
      "cd /app/src && SHU_WORKERS_ENABLED=false python -m tests.integ.test_billing_usage_me_integration"
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.helpers.auth import cleanup_test_user, create_active_user_with_id
from integ.response_utils import extract_data
from shu.core.logging import get_logger
from shu.core.tenant import tenant_context_for_tenant_id

logger = get_logger(__name__)

# A fixed billing period; seeded usage created_at values fall inside it.
PERIOD_START = datetime(2026, 6, 1, tzinfo=UTC)
PERIOD_END = datetime(2026, 7, 1, tzinfo=UTC)
IN_PERIOD = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)


def _patch_period():
    """Patch the router's period resolver to a fixed, known period.

    Patching the resolver directly keeps the seeding tests deterministic and
    bypasses the CP -> Stripe -> calendar-month fallback chain (Stripe is
    configured in the dev container, so the real resolver would otherwise hit
    it)."""

    async def _fake_period(settings):
        return PERIOD_START, PERIOD_END

    return patch("shu.billing.router._resolve_usage_period", _fake_period)


async def _seed_provider(db, *, system_managed: bool) -> str:
    """Create an LLM provider (billable or BYOK). Returns provider_id.

    Providers are global (not tenant-scoped); name carries 'test' so the
    framework cleanup reclaims it between tests.
    """
    from shu.models.llm_provider import LLMProvider

    async with tenant_context_for_tenant_id(None):
        provider = LLMProvider(
            name=f"shu844-test-provider-{uuid.uuid4().hex[:8]}",
            provider_type="openai",
            is_active=True,
            is_system_managed=system_managed,
        )
        db.add(provider)
        await db.commit()
        await db.refresh(provider)
        return provider.id


async def _seed_usage(
    db,
    *,
    user_id: str,
    provider_id: str,
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    total_cost: str,
    created_at: datetime,
) -> None:
    """Seed one llm_usage row attributed to a user (tenant stamped via context)."""
    from shu.models.llm_provider import LLMUsage

    async with tenant_context_for_tenant_id(None):
        db.add(
            LLMUsage(
                user_id=str(user_id),
                provider_id=provider_id,
                model_id=None,
                provider_name="shu844-test",
                model_name=model_name,
                request_type="chat",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                total_cost=Decimal(total_cost),
                success=True,
                created_at=created_at,
            )
        )
        await db.commit()


async def _cleanup_seeded(db) -> None:
    """Remove rows this suite seeded.

    The API can't delete Shu-managed (is_system_managed) providers, so the
    framework cleanup 403s on them; sweep our markers directly. llm_usage is
    RLS-scoped, so the DELETE runs inside tenant context.
    """
    from sqlalchemy import text

    async with tenant_context_for_tenant_id(None):
        await db.execute(text("DELETE FROM llm_usage WHERE provider_name = 'shu844-test'"))
        await db.execute(text("DELETE FROM llm_providers WHERE name LIKE 'shu844-test-provider-%'"))
        await db.commit()


async def test_usage_me_scopes_to_caller(client, db, auth_headers):
    """A fresh user sees ONLY their own billable usage — another user's rows never leak in.

    Uses the framework admin as the "other user" so only one new seat is taken
    (the dev tenant enforces a seat limit). The fresh user has no prior usage,
    so asserting their exact totals proves the user_id scoping holds.
    """
    a_headers, a_id = await create_active_user_with_id(client, auth_headers)
    admin_id = auth_headers["_user_id"]  # the framework's existing admin user
    try:
        provider_id = await _seed_provider(db, system_managed=True)
        # Fresh user A's usage.
        await _seed_usage(
            db,
            user_id=a_id,
            provider_id=provider_id,
            model_name="model-a",
            input_tokens=100,
            output_tokens=50,
            total_cost="0.002",
            created_at=IN_PERIOD,
        )
        # A DIFFERENT user's usage (admin), same tenant/provider/period — must be excluded from A's view.
        await _seed_usage(
            db,
            user_id=admin_id,
            provider_id=provider_id,
            model_name="model-admin",
            input_tokens=200,
            output_tokens=100,
            total_cost="0.004",
            created_at=IN_PERIOD,
        )

        with _patch_period():
            resp_a = await client.get("/api/v1/billing/usage/me", headers=a_headers)

        assert resp_a.status_code == 200, resp_a.text
        data_a = extract_data(resp_a)

        # A sees exactly their own row — not the admin's 200/100, not their sum.
        assert data_a["current_period_unknown"] is False
        assert data_a["total_input_tokens"] == 100, data_a
        assert data_a["total_output_tokens"] == 50, data_a
        assert float(data_a["total_cost_usd"]) == 0.002, data_a
        assert data_a["request_count"] == 1, data_a
        model_names = [m.get("model_name") for m in data_a["by_model"]]
        assert "model-admin" not in model_names, data_a
    finally:
        await cleanup_test_user(client, auth_headers, a_id)
        await _cleanup_seeded(db)


async def test_usage_me_excludes_byok(client, db, auth_headers):
    """BYOK (is_system_managed=False) usage is excluded; only billable usage counts."""
    headers, user_id = await create_active_user_with_id(client, auth_headers)
    try:
        billable = await _seed_provider(db, system_managed=True)
        byok = await _seed_provider(db, system_managed=False)
        await _seed_usage(
            db,
            user_id=user_id,
            provider_id=billable,
            model_name="billable-model",
            input_tokens=100,
            output_tokens=50,
            total_cost="0.002",
            created_at=IN_PERIOD,
        )
        await _seed_usage(
            db,
            user_id=user_id,
            provider_id=byok,
            model_name="byok-model",
            input_tokens=300,
            output_tokens=200,
            total_cost="0.010",
            created_at=IN_PERIOD,
        )

        with _patch_period():
            resp = await client.get("/api/v1/billing/usage/me", headers=headers)

        assert resp.status_code == 200, resp.text
        data = extract_data(resp)
        # Only the billable row counts; the BYOK row is filtered out.
        assert data["total_input_tokens"] == 100, data
        assert data["total_output_tokens"] == 50, data
        model_names = [m.get("model_name") for m in data["by_model"]]
        assert "byok-model" not in model_names, data
        assert "billable-model" in model_names, data
    finally:
        await cleanup_test_user(client, auth_headers, user_id)
        await _cleanup_seeded(db)


async def test_usage_me_defaults_to_calendar_month(client, db, auth_headers):
    """When neither CP nor Stripe supplies a period, fall back to the current UTC calendar month."""
    headers, user_id = await create_active_user_with_id(client, auth_headers)

    async def _no_cp_state():
        return SimpleNamespace(current_period_start=None, current_period_end=None)

    async def _no_stripe_period(settings):
        return None

    try:
        # CP has no period and the Stripe fallback is unavailable -> calendar-month default.
        with (
            patch("shu.billing.router.get_current_billing_state", _no_cp_state),
            patch("shu.billing.router._stripe_subscription_period", _no_stripe_period),
        ):
            resp = await client.get("/api/v1/billing/usage/me", headers=headers)
        assert resp.status_code == 200, resp.text
        data = extract_data(resp)
        # Period is always resolved now (never "unknown") — to the 1st of the current UTC month.
        assert data["current_period_unknown"] is False, data
        # Structural invariants of the calendar-month start, parsed from the
        # response — avoids a re-sampled now() racing the request across a UTC
        # month boundary.
        period_start = datetime.fromisoformat(data["period_start"])
        assert period_start.day == 1, data
        assert period_start.hour == period_start.minute == period_start.second == period_start.microsecond == 0, data
        assert period_start.utcoffset() == timedelta(0), data  # UTC
        # Fresh user has no usage in that window.
        assert data["total_input_tokens"] == 0, data
        assert data["total_cost_usd"] == 0.0, data
        assert data["by_model"] == [], data
    finally:
        await cleanup_test_user(client, auth_headers, user_id)
        await _cleanup_seeded(db)


async def test_usage_me_by_day_buckets(client, db, auth_headers):
    """by_day buckets per UTC day per model; aggregated by_model matches the daily sum."""
    headers, user_id = await create_active_user_with_id(client, auth_headers)
    try:
        provider_id = await _seed_provider(db, system_managed=True)
        day1 = datetime(2026, 6, 5, 2, 0, 0, tzinfo=UTC)
        day1_late = datetime(2026, 6, 5, 20, 0, 0, tzinfo=UTC)
        day2 = datetime(2026, 6, 6, 3, 0, 0, tzinfo=UTC)
        # Day 1: two rows on model-1 (same UTC day → one bucket) + one on model-2.
        await _seed_usage(
            db,
            user_id=user_id,
            provider_id=provider_id,
            model_name="model-1",
            input_tokens=100,
            output_tokens=50,
            total_cost="0.001",
            created_at=day1,
        )
        await _seed_usage(
            db,
            user_id=user_id,
            provider_id=provider_id,
            model_name="model-1",
            input_tokens=40,
            output_tokens=10,
            total_cost="0.001",
            created_at=day1_late,
        )
        await _seed_usage(
            db,
            user_id=user_id,
            provider_id=provider_id,
            model_name="model-2",
            input_tokens=70,
            output_tokens=30,
            total_cost="0.001",
            created_at=day1,
        )
        # Day 2: one row on model-1.
        await _seed_usage(
            db,
            user_id=user_id,
            provider_id=provider_id,
            model_name="model-1",
            input_tokens=200,
            output_tokens=100,
            total_cost="0.002",
            created_at=day2,
        )

        with _patch_period():
            resp = await client.get("/api/v1/billing/usage/me", headers=headers)

        assert resp.status_code == 200, resp.text
        data = extract_data(resp)
        by_day = data["by_day"]

        d1_m1 = [d for d in by_day if d["date"] == "2026-06-05" and d["model_name"] == "model-1"]
        d1_m2 = [d for d in by_day if d["date"] == "2026-06-05" and d["model_name"] == "model-2"]
        d2_m1 = [d for d in by_day if d["date"] == "2026-06-06" and d["model_name"] == "model-1"]
        assert len(d1_m1) == 1, by_day
        assert d1_m1[0]["input_tokens"] == 140, d1_m1  # 100 + 40 same UTC day
        assert d1_m1[0]["request_count"] == 2, d1_m1
        assert len(d1_m2) == 1 and d1_m2[0]["input_tokens"] == 70, by_day
        assert len(d2_m1) == 1 and d2_m1[0]["input_tokens"] == 200, by_day

        # by_model model-1 total = day1 (140) + day2 (200) = 340 input.
        m1 = next(m for m in data["by_model"] if m["model_name"] == "model-1")
        assert m1["input_tokens"] == 340, data["by_model"]
    finally:
        await cleanup_test_user(client, auth_headers, user_id)
        await _cleanup_seeded(db)


async def test_usage_me_requires_auth(client, db, auth_headers):
    """Unauthenticated requests are rejected (get_current_user gate)."""
    logger.info("=== EXPECTED TEST OUTPUT: 401 for unauthenticated /billing/usage/me ===")
    resp = await client.get("/api/v1/billing/usage/me")
    assert resp.status_code == 401, resp.text


class BillingUsageMeTestSuite(BaseIntegrationTestSuite):
    """Integration suite for GET /api/v1/billing/usage/me (SHU-844)."""

    def get_test_functions(self) -> list[Callable]:
        return [
            test_usage_me_scopes_to_caller,
            test_usage_me_excludes_byok,
            test_usage_me_defaults_to_calendar_month,
            test_usage_me_by_day_buckets,
            test_usage_me_requires_auth,
        ]

    def get_suite_name(self) -> str:
        return "Billing Usage (My Usage) Tests"

    def get_suite_description(self) -> str:
        return "Per-user /billing/usage/me: scoping, BYOK exclusion, period handling, by_day bucketing (SHU-844)"


if __name__ == "__main__":
    suite = BillingUsageMeTestSuite()
    sys.exit(suite.run())
