"""Tests for shu.billing.config — validators and validate_configuration logic.

Coverage focus: the rules we actually wrote (TTL minimum, the CP-base-URL
co-requirement). Pydantic's own type coercion and env-var loading are not
re-tested here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shu.billing.config import BillingSettings


def test_billing_state_cache_ttl_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # Robust to whatever the developer happens to have in their .env / env.
    monkeypatch.delenv("SHU_BILLING_STATE_CACHE_TTL_SECONDS", raising=False)

    settings = BillingSettings(_env_file=None)  # type: ignore[call-arg]
    assert settings.billing_state_cache_ttl_seconds == 500


def test_billing_state_cache_ttl_validator_rejects_below_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # SHU_BILLING_STATE_CACHE_TTL_SECONDS in env would override the kwarg
    # (pydantic-settings env priority), so clear it.
    monkeypatch.setenv("SHU_BILLING_STATE_CACHE_TTL_SECONDS", "5")

    with pytest.raises(ValidationError) as exc_info:
        BillingSettings(_env_file=None)  # type: ignore[call-arg]

    assert "must be >= 10" in str(exc_info.value)


def test_billing_state_cache_ttl_validator_accepts_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHU_BILLING_STATE_CACHE_TTL_SECONDS", "10")

    settings = BillingSettings(_env_file=None)  # type: ignore[call-arg]
    assert settings.billing_state_cache_ttl_seconds == 10


def test_cp_base_url_defaults_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # Robust to whatever the developer happens to have in .env / env.
    monkeypatch.delenv("SHU_CP_BASE_URL", raising=False)

    settings = BillingSettings(_env_file=None)  # type: ignore[call-arg]
    assert settings.cp_base_url is None


def test_validate_configuration_flags_missing_cp_base_url_when_secret_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SHU_CP_BASE_URL", raising=False)

    settings = BillingSettings(
        _env_file=None,  # type: ignore[call-arg]
        secret_key="sk_test_x",
        customer_id="cus_x",
        subscription_id="sub_x",
        price_id_monthly="price_x",
        router_shared_secret="r" * 64,
    )

    assert (
        "SHU_CP_BASE_URL is required when SHU_ROUTER_SHARED_SECRET is set"
        in settings.validate_configuration()
    )


def test_validate_configuration_does_not_flag_missing_cp_base_url_without_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Self-hosted / dev — neither router_shared_secret nor cp_base_url set
    # is valid. _env_file=None bypasses .env loading on top of the env-var
    # cleanup so the test is robust to whatever .env happens to be on disk.
    monkeypatch.delenv("SHU_ROUTER_SHARED_SECRET", raising=False)
    monkeypatch.delenv("SHU_CP_BASE_URL", raising=False)

    settings = BillingSettings(
        _env_file=None,  # type: ignore[call-arg]
        secret_key="sk_test_x",
        customer_id="cus_x",
        subscription_id="sub_x",
        price_id_monthly="price_x",
    )

    issues = settings.validate_configuration()
    assert all("SHU_CP_BASE_URL" not in issue for issue in issues)
