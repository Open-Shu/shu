"""Billing module configuration.

Isolated configuration for Stripe integration. Can work standalone or
integrate with the main Shu Settings class.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BillingSettings(BaseSettings):
    """Stripe billing configuration.

    All settings use SHU_STRIPE_* prefix for consistency with the main app,
    but this class can be instantiated independently.
    """

    # Stripe API keys
    secret_key: str | None = Field(None, alias="SHU_STRIPE_SECRET_KEY")
    publishable_key: str | None = Field(None, alias="SHU_STRIPE_PUBLISHABLE_KEY")
    webhook_secret: str | None = Field(None, alias="SHU_STRIPE_WEBHOOK_SECRET")

    # Stripe product/price configuration
    # These should be created in Stripe Dashboard first
    product_id: str | None = Field(None, alias="SHU_STRIPE_PRODUCT_ID")
    price_id_monthly: str | None = Field(None, alias="SHU_STRIPE_PRICE_ID_MONTHLY")

    # Meter IDs for usage-based billing (created via Stripe Billing > Meters)
    meter_id_tokens: str | None = Field(None, alias="SHU_STRIPE_METER_ID_TOKENS")

    # Operational settings
    # test or live - used for validation and logging
    mode: Literal["test", "live"] = Field("test", alias="SHU_STRIPE_MODE")

    # Base URL for redirects after checkout/portal (e.g., https://app.shu.ai)
    app_base_url: str = Field("http://localhost:3000", alias="SHU_APP_BASE_URL")

    # Usage reporting interval in seconds (default: 1 hour)
    # Stripe recommends hourly reporting for usage-based billing
    usage_report_interval_seconds: int = Field(3600, alias="SHU_STRIPE_USAGE_REPORT_INTERVAL")

    # Grace period in days before suspending service after payment failure
    payment_grace_period_days: int = Field(7, alias="SHU_STRIPE_PAYMENT_GRACE_DAYS")

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

    @property
    def is_configured(self) -> bool:
        """Check if minimum Stripe configuration is present."""
        return bool(self.secret_key)

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

        if not self.webhook_secret:
            issues.append("SHU_STRIPE_WEBHOOK_SECRET is required for webhook verification")

        if not self.price_id_monthly:
            issues.append("SHU_STRIPE_PRICE_ID_MONTHLY is required for subscriptions")

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
