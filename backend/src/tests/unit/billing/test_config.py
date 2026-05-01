"""Tests for billing config validation logging (SHU-701) and the
``/billing/config`` admin-only ``validation_issues`` field."""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock

import pytest

from shu.billing.config import BillingSettings, log_billing_validation
from shu.billing.router import get_billing_config_endpoint


# Keep BillingSettings construction deterministic regardless of dev/CI env: clear
# any SHU_STRIPE_*/SHU_ROUTER_* OS env vars and skip the .env file. Without this
# a developer with a populated .env (or CI with billing secrets) would get
# different values bleeding into the "not configured" tests.
@pytest.fixture(autouse=True)
def _isolate_billing_env(monkeypatch):
    import os

    for key in [k for k in os.environ if k.startswith(("SHU_STRIPE_", "SHU_ROUTER_"))]:
        monkeypatch.delenv(key, raising=False)
    yield


def _make_settings(**overrides) -> BillingSettings:
    """Build a BillingSettings with .env disabled, then apply any overrides.

    Use this everywhere instead of calling ``BillingSettings(...)`` directly —
    raw construction would still read the repo's .env file.
    """
    return BillingSettings(_env_file=None, **overrides)  # type: ignore[call-arg]


def _settings(**overrides) -> BillingSettings:
    """Build a fully-configured BillingSettings; override fields as needed."""
    base = {
        "SHU_STRIPE_SECRET_KEY": "sk_test_abc",
        "SHU_STRIPE_CUSTOMER_ID": "cus_test_123",
        "SHU_STRIPE_SUBSCRIPTION_ID": "sub_test_123",
        "SHU_ROUTER_SHARED_SECRET": "a" * 64,
        "SHU_STRIPE_PRICE_ID_MONTHLY": "price_test_123",
        "SHU_STRIPE_MODE": "test",
    }
    base.update(overrides)
    return _make_settings(**base)


def _user(*, admin: bool) -> MagicMock:
    user = MagicMock()
    user.can_manage_users.return_value = admin
    return user


# ---------------------------------------------------------------------------
# log_billing_validation — helper invoked from main.py lifespan
# ---------------------------------------------------------------------------


class TestLogBillingValidation:
    def test_fully_configured_no_issues_logs_success(self, caplog):
        """(a) Fully configured + no issues → INFO success, no warnings."""
        caplog.set_level(logging.INFO, logger="shu.billing.config")
        issues = log_billing_validation(_settings())

        assert issues == []
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings == []
        infos = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any("validated successfully" in r.getMessage() for r in infos)

    def test_not_configured_logs_self_hosted_info(self, caplog):
        """(b) Not configured → INFO "self-hosted mode", no warnings, no validation."""
        caplog.set_level(logging.INFO, logger="shu.billing.config")
        # Drop the required tenant identifiers — is_configured becomes False.
        unconfigured = _make_settings(SHU_STRIPE_MODE="test")
        assert unconfigured.is_configured is False

        issues = log_billing_validation(unconfigured)

        assert issues == []
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings == []
        infos = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any("not configured" in r.getMessage() for r in infos)

    def test_partial_config_warns_with_field_name(self, caplog):
        """(c) is_configured + missing price → WARNING naming the field."""
        caplog.set_level(logging.WARNING, logger="shu.billing.config")
        # is_configured needs secret_key + customer_id + subscription_id; drop
        # price_id_monthly to surface a single targeted issue.
        settings = _settings(SHU_STRIPE_PRICE_ID_MONTHLY="")
        issues = log_billing_validation(settings)

        assert any("PRICE_ID_MONTHLY" in i for i in issues)
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert getattr(warnings[0], "field", None) == "SHU_STRIPE_PRICE_ID_MONTHLY"
        assert "PRICE_ID_MONTHLY" in getattr(warnings[0], "issue", "")

    def test_mode_key_mismatch_warns(self, caplog):
        """(d) mode=live but secret_key starts with sk_test_ → WARNING."""
        caplog.set_level(logging.WARNING, logger="shu.billing.config")
        settings = _settings(SHU_STRIPE_MODE="live", SHU_STRIPE_SECRET_KEY="sk_test_abc")
        issues = log_billing_validation(settings)

        # Validate_configuration emits the message with SHU_STRIPE_MODE as the
        # leading field; that's what the structured extra picks up.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        mode_warnings = [w for w in warnings if getattr(w, "field", None) == "SHU_STRIPE_MODE"]
        assert mode_warnings, f"expected SHU_STRIPE_MODE warning, got {issues}"

    def test_validate_raises_logs_error_no_crash(self, caplog):
        """(e) validate_configuration raises → ERROR logged, helper returns []."""
        caplog.set_level(logging.ERROR, logger="shu.billing.config")

        # BillingSettings is a frozen-ish pydantic model — subclass instead of
        # monkey-patching the instance.
        class RaisingSettings(BillingSettings):
            def validate_configuration(self) -> list[str]:
                raise RuntimeError("boom")

        settings = RaisingSettings(
            _env_file=None,
            SHU_STRIPE_SECRET_KEY="sk_test_abc",
            SHU_STRIPE_CUSTOMER_ID="cus_test_123",
            SHU_STRIPE_SUBSCRIPTION_ID="sub_test_123",
        )
        issues = log_billing_validation(settings)

        assert issues == []
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("validation failed to run" in r.getMessage() for r in errors)


# ---------------------------------------------------------------------------
# /billing/config endpoint — admin-only validation_issues field
# ---------------------------------------------------------------------------


def _decode(response) -> dict:
    return json.loads(response.body)["data"]


class TestBillingConfigEndpoint:
    @pytest.mark.asyncio
    async def test_admin_sees_validation_issues_field(self):
        settings = _settings(SHU_STRIPE_PRICE_ID_MONTHLY="")  # one issue
        response = await get_billing_config_endpoint(settings=settings, current_user=_user(admin=True))

        body = _decode(response)
        assert "validation_issues" in body
        assert any("PRICE_ID_MONTHLY" in i for i in body["validation_issues"])

    @pytest.mark.asyncio
    async def test_non_admin_does_not_see_validation_issues_field(self):
        settings = _settings(SHU_STRIPE_PRICE_ID_MONTHLY="")
        response = await get_billing_config_endpoint(settings=settings, current_user=_user(admin=False))

        body = _decode(response)
        assert "validation_issues" not in body

    @pytest.mark.asyncio
    async def test_admin_sees_empty_list_when_not_configured(self):
        """is_configured=False → empty validation_issues, not a missing-field flood."""
        settings = _make_settings(SHU_STRIPE_MODE="test")
        response = await get_billing_config_endpoint(settings=settings, current_user=_user(admin=True))

        body = _decode(response)
        assert body["validation_issues"] == []

    @pytest.mark.asyncio
    async def test_admin_validate_raises_returns_empty_list_not_500(self):
        """If validate_configuration raises, the endpoint returns [] rather than 500."""

        class RaisingSettings(BillingSettings):
            def validate_configuration(self) -> list[str]:
                raise RuntimeError("boom")

        settings = RaisingSettings(
            _env_file=None,
            SHU_STRIPE_SECRET_KEY="sk_test_abc",
            SHU_STRIPE_CUSTOMER_ID="cus_test_123",
            SHU_STRIPE_SUBSCRIPTION_ID="sub_test_123",
        )
        response = await get_billing_config_endpoint(settings=settings, current_user=_user(admin=True))

        body = _decode(response)
        assert body["validation_issues"] == []
