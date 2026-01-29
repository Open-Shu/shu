"""ProviderCredential model: provider-agnostic storage of OAuth credentials.

Stores encrypted access/refresh tokens and related metadata for any provider.
Links to ProviderIdentity via credential_id.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Index, String, Text

from ..core.oauth_encryption import get_oauth_encryption_service
from .base import BaseModel


class ProviderCredential(BaseModel):
    __tablename__ = "provider_credentials"

    # Owning user
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)

    # Provider key (e.g., "google", "microsoft")
    provider_key = Column(String, nullable=False, index=True)

    # Provider account identifier (optional at creation; can be populated later)
    account_id = Column(String, nullable=True, index=True)

    # Encrypted token blobs
    access_token_encrypted = Column(Text, nullable=False)
    refresh_token_encrypted = Column(Text, nullable=True)

    # OAuth client metadata (optional, provider-specific)
    token_uri = Column(String, nullable=True)
    client_id = Column(String, nullable=True)
    client_secret = Column(String, nullable=True)

    # Granted scopes/permissions (provider-native identifiers)
    scopes = Column(JSON, nullable=True)

    # Expiration and status
    expires_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    # Extra provider-specific fields
    credential_meta = Column(JSON, nullable=True)

    __table_args__ = (
        Index(
            "ix_provider_credentials_user_provider_account",
            "user_id",
            "provider_key",
            "account_id",
        ),
    )

    # Convenience helpers mirroring UserGoogleCredentials API
    def set_access_token(self, token: str) -> None:
        svc = get_oauth_encryption_service()
        self.access_token_encrypted = svc.encrypt_token(token)

    def get_access_token(self) -> str:
        svc = get_oauth_encryption_service()
        return svc.decrypt_token(self.access_token_encrypted)

    def set_refresh_token(self, token: str) -> None:
        if token is None:
            self.refresh_token_encrypted = None
            return
        svc = get_oauth_encryption_service()
        self.refresh_token_encrypted = svc.encrypt_token(token)

    def get_refresh_token(self) -> str | None:
        if not self.refresh_token_encrypted:
            return None
        svc = get_oauth_encryption_service()
        return svc.decrypt_token(self.refresh_token_encrypted)

    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        return datetime.now(UTC) >= self.expires_at
