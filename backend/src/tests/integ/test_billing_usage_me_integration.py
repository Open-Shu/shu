"""Integration tests for GET /api/v1/billing/usage/me (SHU-844).

Verifies the per-user "My Usage" endpoint:
- scopes strictly to the requesting user (one user never sees another's usage),
- excludes BYOK (is_system_managed=False) providers,
- returns the current_period_unknown shape when there's no active period,
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
from datetime import UTC, datetime
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
    """Patch the router's billing-state lookup to supply a known period.

    The endpoint only reads `state.current_period_start/end`, so a SimpleNamespace
    is enough — no full BillingState needed. The self-hosted test env otherwise
    returns HEALTHY_DEFAULT (no period → current_period_unknown)."""

    async def _fake_state():
        return SimpleNamespace(current_period_start=PERIOD_START, current_period_end=PERIOD_END)

    return patch("shu.billing.router.get_current_billing_state", _fake_state)


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


async def test_usage_me_unknown_period(client, db, auth_headers):
    """With no active billing period (default self-hosted test env), returns the unknown shape."""
    headers, user_id = await create_active_user_with_id(client, auth_headers)
    try:
        # No _patch_period(): get_current_billing_state -> HEALTHY_DEFAULT (no period).
        resp = await client.get("/api/v1/billing/usage/me", headers=headers)
        assert resp.status_code == 200, resp.text
        data = extract_data(resp)
        assert data["current_period_unknown"] is True, data
        assert data["period_start"] is None
        assert data["period_end"] is None
        assert data["total_input_tokens"] == 0
        assert data["total_output_tokens"] == 0
        assert data["total_cost_usd"] == 0.0
        assert data["request_count"] == 0
        assert data["by_model"] == []
        assert data["by_day"] == []
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
            test_usage_me_unknown_period,
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
