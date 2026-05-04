"""Billing module configuration.

Isolated configuration for Stripe integration. Can work standalone or
integrate with the main Shu Settings class.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Floor on the cache TTL guards against an operator setting a near-zero value
# that would have every enforcement check hit CP. 10s is loose enough not to be
# accidentally tripped, tight enough that emergency cache flushes are still
# possible without code changes.
_MIN_BILLING_STATE_CACHE_TTL_SECONDS = 10


class BillingSettings(BaseSettings):
    """Stripe billing configuration.

    All settings use SHU_STRIPE_* prefix for consistency with the main app,
    but this class can be instantiated independently.
    """

    # Stripe API keys. Webhook ingress runs through the Shu Control Plane,
    # which verifies the Stripe signature once at the edge and forwards events
    # to this tenant under an HMAC envelope. Tenants never verify Stripe
    # signatures directly, so there is no SHU_STRIPE_WEBHOOK_SECRET here.
    secret_key: str | None = Field(None, alias="SHU_STRIPE_SECRET_KEY")
    publishable_key: str | None = Field(None, alias="SHU_STRIPE_PUBLISHABLE_KEY")

    # Shared HMAC secret used to verify the forwarded-envelope signature on
    # /api/v1/billing/webhooks. Must match the tenant row's `shared_secret` in
    # the control-plane registry (64 lowercase hex chars from secrets.token_hex(32)).
    router_shared_secret: str | None = Field(None, alias="SHU_ROUTER_SHARED_SECRET")

    # Base URL for tenant→CP polls (e.g. /api/v1/tenants/{id}/billing-state).
    # Required in hosted mode whenever router_shared_secret is set; absence is
    # surfaced through validate_configuration rather than failing model init so
    # self-hosted deployments without billing can still load BillingSettings.
    cp_base_url: str | None = Field(None, alias="SHU_CP_BASE_URL")

    # In-process TTL for the cached BillingState polled from CP. The cache uses
    # stale-while-error on top of this — TTL governs successful-refresh cadence;
    # outage windows are absorbed by serving the previous value.
    billing_state_cache_ttl_seconds: int = Field(500, alias="SHU_BILLING_STATE_CACHE_TTL_SECONDS")

    # Tenant identifiers — set by the operator at deploy time.
    # These seed billing_state on first boot so webhook handlers and
    # scheduler jobs have a customer/subscription to work with immediately.
    customer_id: str | None = Field(None, alias="SHU_STRIPE_CUSTOMER_ID")
    subscription_id: str | None = Field(None, alias="SHU_STRIPE_SUBSCRIPTION_ID")

    # Stripe product/price configuration
    # These should be created in Stripe Dashboard first
    product_id: str | None = Field(None, alias="SHU_STRIPE_PRODUCT_ID")
    price_id_monthly: str | None = Field(None, alias="SHU_STRIPE_PRICE_ID_MONTHLY")

    # Meter for usage-based billing (created via Stripe Billing > Meters)
    meter_id_cost: str | None = Field(None, alias="SHU_STRIPE_METER_ID_COST")
    meter_event_name: str = Field("usage_cost", alias="SHU_STRIPE_METER_EVENT_NAME")

    # Operational settings
    # test or live - used for validation and logging
    mode: Literal["test", "live"] = Field("test", alias="SHU_STRIPE_MODE")

    # Base URL for Customer Portal return redirect (e.g., https://app.shu.ai)
    app_base_url: str = Field("http://localhost:3000", alias="SHU_APP_BASE_URL")

    # Usage reporting interval in seconds (default: 1 hour)
    # Stripe recommends hourly reporting for usage-based billing
    usage_report_interval_seconds: int = Field(3600, alias="SHU_STRIPE_USAGE_REPORT_INTERVAL")

    # Token credit included per user per month (0 = pure usage-based)
    # This is for informational purposes; actual credits are in Stripe product config
    included_tokens_per_user: int = Field(0, alias="SHU_STRIPE_INCLUDED_TOKENS_PER_USER")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,  # Allow both field names and aliases
    )

    @field_validator("billing_state_cache_ttl_seconds", mode="after")
    @classmethod
    def _enforce_min_cache_ttl(cls, value: int) -> int:
        if value < _MIN_BILLING_STATE_CACHE_TTL_SECONDS:
            raise ValueError(
                f"SHU_BILLING_STATE_CACHE_TTL_SECONDS must be >= "
                f"{_MIN_BILLING_STATE_CACHE_TTL_SECONDS} (got {value})"
            )
        return value

    @property
    def is_configured(self) -> bool:
        """Check if billing is fully configured for this instance.

        Requires Stripe API credentials AND the tenant's pre-provisioned
        identifiers. Without customer_id, webhooks are intentionally dropped
        and the portal is unusable. Without subscription_id, usage reporting
        and seat sync have no subscription to work against. Both are always
        set at deploy time — their absence is a misconfiguration, not a
        valid intermediate state.
        """
        return bool(self.secret_key and self.customer_id and self.subscription_id)

    @property
    def is_production(self) -> bool:
        """Check if running in live mode with real payments."""
        return self.mode == "live" and self.secret_key is not None and self.secret_key.startswith("sk_live_")

    def validate_configuration(self) -> list[str]:
        """Validate configuration and return list of issues.

        Returns empty list if configuration is valid.
        """
        issues = []

        if not self.secret_key:
            issues.append("SHU_STRIPE_SECRET_KEY is required")
        elif self.mode == "live" and not self.secret_key.startswith("sk_live_"):
            issues.append("SHU_STRIPE_MODE is 'live' but secret_key is not a live key")
        elif self.mode == "test" and not self.secret_key.startswith("sk_test_"):
            issues.append("SHU_STRIPE_MODE is 'test' but secret_key is not a test key")

        if not self.customer_id:
            issues.append("SHU_STRIPE_CUSTOMER_ID is required (set at deploy time by the operator)")

        if not self.subscription_id:
            issues.append("SHU_STRIPE_SUBSCRIPTION_ID is required (set at deploy time by the operator)")

        if not self.router_shared_secret:
            issues.append("SHU_ROUTER_SHARED_SECRET is required for router-envelope verification")

        # cp_base_url is only meaningful when router_shared_secret is set —
        # the secret addresses the tenant in CP, the URL is where to reach CP.
        # Either both are present (hosted) or both absent (dev / self-hosted).
        if self.router_shared_secret and not self.cp_base_url:
            issues.append("SHU_CP_BASE_URL is required when SHU_ROUTER_SHARED_SECRET is set")

        if not self.price_id_monthly:
            issues.append("SHU_STRIPE_PRICE_ID_MONTHLY is required for subscriptions")

        if self.usage_report_interval_seconds <= 0:
            issues.append("SHU_STRIPE_USAGE_REPORT_INTERVAL must be > 0")

        return issues


@lru_cache
def get_billing_settings() -> BillingSettings:
    """Get cached billing settings instance."""
    return BillingSettings()  # type: ignore[call-arg]  # pydantic-settings loads from env


def get_billing_settings_dependency() -> BillingSettings:
    """FastAPI dependency for billing settings.

    Unlike the cached version, this can be overridden in tests.
    """
    return get_billing_settings()
