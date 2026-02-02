"""ProviderIdentity model: provider-agnostic storage of connected account identity.

Links a user to an identity at a given provider (e.g., Google, Microsoft),
optionally associated to a stored credential record via credential_id.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Column, ForeignKey, Index, String

from .base import BaseModel


class ProviderIdentity(BaseModel):
    __tablename__ = "provider_identities"

    # Owning user
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)

    # Provider key (e.g., "google", "microsoft")
    provider_key = Column(String, nullable=False, index=True)

    # Provider account identifier (e.g., OIDC 'sub')
    account_id = Column(String, nullable=False, index=True)

    # Primary email for the account (if available)
    primary_email = Column(String, nullable=True, index=True)

    # Display information
    display_name = Column(String, nullable=True)
    avatar_url = Column(String, nullable=True)

    # Scopes granted to the associated credential (if known)
    scopes = Column(JSON, nullable=True)

    # Optional link to a credential row (string id); left generic to avoid cross-table FKs
    credential_id = Column(String, nullable=True, index=True)

    # Extra provider-specific fields (e.g., locale, hd, verified flags)
    identity_meta = Column(JSON, nullable=True)

    __table_args__ = (
        Index(
            "ux_provider_identity_user_provider_account",
            "user_id",
            "provider_key",
            "account_id",
            unique=True,
        ),
    )

    def to_public_dict(self) -> dict[str, Any]:
        return self.to_dict()
