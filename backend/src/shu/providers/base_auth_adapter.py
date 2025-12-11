from __future__ import annotations

from typing import Any, Dict, List, Optional


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

