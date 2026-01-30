"""PluginSubscription model: per-user selection of plugins that may use a given connected account/provider.
Used to compute consent scope unions and enforce execution-time authorization (TASK-163).
"""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Index, String, UniqueConstraint

from .base import BaseModel


class PluginSubscription(BaseModel):
    __tablename__ = "plugin_subscriptions"

    # Owning user
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Provider key (e.g., "google", "microsoft")
    provider_key = Column(String, nullable=False, index=True)

    # Provider account identifier; optional when provider doesn't have multiple accounts
    # or when subscription is provider-wide for the user. Use empty string to represent
    # "unspecified" if needed for uniqueness.
    account_id = Column(String, nullable=True, index=True)

    # Plugin name (matches manifest/registry name)
    plugin_name = Column(String, nullable=False, index=True)

    __table_args__ = (
        # Enforce uniqueness per user/provider/account/plugin
        UniqueConstraint(
            "user_id",
            "provider_key",
            "account_id",
            "plugin_name",
            name="ux_plugin_sub_user_provider_account_plugin",
        ),
        Index("ix_plugin_sub_user_provider", "user_id", "provider_key"),
    )
