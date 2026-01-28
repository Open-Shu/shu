from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class BaseAuthAdapter:
    """
    Provider auth adapter interface.

    Adapters receive the calling AuthCapability instance so they can reuse
    its HTTP helpers, settings, encryption, and caches without duplicating logic.
    """

    def __init__(self, auth_capability: Any):
        self._auth = auth_capability

    # -- Used by AuthCapability today --
    async def user_token(self, *, required_scopes: Optional[List[str]] = None) -> Optional[str]:
        raise NotImplementedError

    async def service_account_token(self, *, scopes: List[str], subject: Optional[str] = None) -> str:
        raise NotImplementedError

    async def delegation_check(self, *, scopes: List[str], subject: str) -> Dict[str, Any]:
        raise NotImplementedError

    # -- Used by API endpoints --
    async def build_authorization_url(self, *, scopes: List[str]) -> Dict[str, Any]:
        """Return {url, state?, code_verifier?, code_challenge_method?} if applicable."""
        raise NotImplementedError

    async def exchange_code(self, *, code: str, scopes: Optional[List[str]] = None) -> Dict[str, Any]:
        """Exchange authorization code for tokens. Return normalized token payload."""
        raise NotImplementedError

    # -- Status/Disconnect hooks for endpoint abstraction --
    async def status(self, *, user_id: str, db) -> Dict[str, Any]:
        """Return minimal provider connection status for the given user.
        Shape: { user_connected: bool, granted_scopes?: List[str], meta?: Dict[str,Any] }
        """
        raise NotImplementedError

    async def disconnect(self, *, user_id: str, db) -> None:
        """Remove provider-specific credentials/state for the given user."""
        raise NotImplementedError

    async def get_user_info(
        self,
        *,
        access_token: Optional[str] = None,
        id_token: Optional[str] = None,
        db: Optional["AsyncSession"] = None
    ) -> Dict[str, Any]:
        """
        Get normalized user info from the provider.

        This method retrieves user identity information from the OAuth provider
        and returns it in a normalized format that can be used by the unified
        SSO authentication flow.

        Args:
            access_token: OAuth access token (used by Microsoft)
            id_token: OIDC ID token (used by Google)
            db: Optional database session for provider-specific lookups
                (e.g., Google backward compatibility with legacy google_id column)

        Returns:
            Normalized user info dict with keys:
            - provider_id: str - Provider's unique user identifier
            - provider_key: str - Provider name ("google" or "microsoft")
            - email: str - User's email address
            - name: str - User's display name
            - picture: str | None - Avatar URL (optional)
            - existing_user: User | None - Pre-looked-up user (optional, for backward compat)

        Raises:
            ValueError: If token is invalid or user info cannot be retrieved
        """
        raise NotImplementedError

