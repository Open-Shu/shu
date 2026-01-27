from __future__ import annotations

from typing import Any, Dict, List, Optional

import certifi
import httpx

from ..base_auth_adapter import BaseAuthAdapter
from ...core.logging import get_logger

logger = get_logger(__name__)


class MicrosoftAuthAdapter(BaseAuthAdapter):
    """Minimal Microsoft 365 OAuth adapter for AUTH-REF-002 grounding.

    Notes:
    - Authorization endpoint (v2): https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize
    - Token endpoint (v2):        https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token
    - Default tenant = "common" unless MICROSOFT_TENANT_ID is set.
    - Scopes must include offline_access to receive refresh_token.
    """

    def _tenant(self) -> str:
        s = self._auth._settings
        tid = getattr(s, "microsoft_tenant_id", None)
        return (tid or "common").strip()

    def _auth_url(self) -> str:
        return f"https://login.microsoftonline.com/{self._tenant()}/oauth2/v2.0/authorize"

    def _token_url(self) -> str:
        return f"https://login.microsoftonline.com/{self._tenant()}/oauth2/v2.0/token"

    async def user_token(self, *, required_scopes: Optional[List[str]] = None) -> Optional[str]:
        """Fetch a Microsoft user access token from ProviderCredential, refreshing via OAuth if needed."""
        from sqlalchemy import select, and_  # type: ignore
        from ...models.provider_credential import ProviderCredential  # type: ignore
        from ...core.database import get_db_session  # type: ignore
        from datetime import datetime, timezone, timedelta

        settings = self._auth._settings
        db = await get_db_session()
        try:
            res = await db.execute(
                select(ProviderCredential).where(
                    and_(
                        ProviderCredential.user_id == self._auth._user_id,
                        ProviderCredential.provider_key == "microsoft",
                        ProviderCredential.is_active == True,  # noqa: E712
                    )
                ).order_by(ProviderCredential.updated_at.desc())
            )
            row = res.scalars().first()
            if not row:
                return None

            # Validate required scopes if provided
            try:
                req = set([str(s) for s in (required_scopes or []) if s])
                granted = set([str(s) for s in (getattr(row, "scopes", None) or []) if s])
                if req and not req.issubset(granted):
                    return None
            except Exception:
                pass

            refresh_token = None
            try:
                refresh_token = row.get_refresh_token()
            except Exception:
                refresh_token = None

            if not refresh_token:
                try:
                    return row.get_access_token()
                except Exception:
                    return None

            client_id = getattr(settings, "microsoft_client_id", None)
            client_secret = getattr(settings, "microsoft_client_secret", None)
            if not (client_id and client_secret):
                return None

            data = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": " ".join([str(s) for s in ((required_scopes or []) + ["offline_access"]) if s]),
            }
            token_url = getattr(row, "token_uri", None) or self._token_url()
            body = await self._auth._post_form(token_url, data)  # type: ignore
            access_token = body.get("access_token") if isinstance(body, dict) else None
            if not access_token:
                return None

            try:
                row.set_access_token(access_token)
                exp_in = int((body.get("expires_in") if isinstance(body, dict) else 3600) or 3600)
                setattr(row, "expires_at", datetime.now(timezone.utc) + timedelta(seconds=max(1, exp_in)))
                await db.commit()
            except Exception:
                try:
                    await db.rollback()
                except Exception:
                    pass
            return access_token
        finally:
            try:
                await db.close()
            except Exception:
                pass

    async def service_account_token(self, *, scopes: List[str], subject: Optional[str] = None) -> str:
        # Not applicable for Microsoft consumer OAuth in this minimal adapter; raise for now.
        raise NotImplementedError("Microsoft service account token not implemented")

    async def delegation_check(self, *, scopes: List[str], subject: str) -> Dict[str, Any]:
        # Not applicable for Microsoft in this minimal adapter; return a neutral response.
        return {"ready": False, "status": 0, "scopes": scopes, "note": "Not implemented"}

    async def build_authorization_url(self, *, scopes: List[str]) -> Dict[str, Any]:
        s = self._auth._settings
        redirect_uri = s.get_oauth_redirect_uri("microsoft") if hasattr(s, "get_oauth_redirect_uri") else getattr(s, "microsoft_redirect_uri", None)
        if not (getattr(s, "microsoft_client_id", None) and redirect_uri):
            raise RuntimeError("Microsoft OAuth is not configured")
        # Ensure offline_access scope for refresh tokens
        scope_list = list({*(scopes or []), "offline_access"})
        res = self._auth.build_authorization_url(  # type: ignore
            auth_url=self._auth_url(),
            client_id=s.microsoft_client_id,
            redirect_uri=redirect_uri,
            scopes=scope_list,
            include_pkce=False,  # keep simple; we don't persist code_verifier yet
            extra_params={
                "response_mode": "query",
                "state": "provider=microsoft",
                "prompt": "select_account",
            },
        )
        return {"url": res.get("url"), "state": "provider=microsoft"}

    async def exchange_code(self, *, code: str, scopes: Optional[List[str]] = None) -> Dict[str, Any]:
        import requests
        s = self._auth._settings
        redirect_uri = s.get_oauth_redirect_uri("microsoft") if hasattr(s, "get_oauth_redirect_uri") else getattr(s, "microsoft_redirect_uri", None)
        if not (getattr(s, "microsoft_client_id", None) and getattr(s, "microsoft_client_secret", None) and redirect_uri):
            raise RuntimeError("Microsoft OAuth is not configured")
        scope_str = " ".join(list({*(scopes or []), "offline_access"})) if scopes else "offline_access"
        resp = requests.post(
            self._token_url(),
            data={
                "client_id": s.microsoft_client_id,
                "client_secret": s.microsoft_client_secret,
                "redirect_uri": redirect_uri,
                "code": code,
                "grant_type": "authorization_code",
                "scope": scope_str,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
        )
        if not resp.ok:
            raise RuntimeError(f"Provider token exchange failed: {resp.text[:300]}")
        return resp.json() or {}

    async def status(self, *, user_id: str, db) -> Dict[str, Any]:
        from sqlalchemy import select, and_  # type: ignore
        from ...models.provider_credential import ProviderCredential  # type: ignore
        result = await db.execute(
            select(ProviderCredential).where(
                and_(
                    ProviderCredential.user_id == user_id,
                    ProviderCredential.provider_key == "microsoft",
                    ProviderCredential.is_active == True,  # noqa: E712
                )
            )
        )
        creds = result.scalars().all()
        scopes_union: List[str] = []
        for c in creds:
            try:
                for s in (c.scopes or []):
                    if s not in scopes_union:
                        scopes_union.append(s)
            except Exception:
                pass
        return {"user_connected": len(creds) > 0, "granted_scopes": scopes_union}

    async def disconnect(self, *, user_id: str, db) -> None:
        from sqlalchemy import delete  # type: ignore
        from ...models.provider_credential import ProviderCredential  # type: ignore
        await db.execute(
            delete(ProviderCredential).where(
                ProviderCredential.user_id == user_id,
                ProviderCredential.provider_key == "microsoft",
            )
        )

    async def get_user_info(
        self,
        *,
        access_token: Optional[str] = None,
        id_token: Optional[str] = None,
        db=None
    ) -> Dict[str, Any]:
        """Get Microsoft user info via Graph API and return normalized user info.

        Args:
            access_token: The Microsoft access token to use for Graph API call
            id_token: Not used for Microsoft (Microsoft uses access_token)
            db: Not used for Microsoft (no legacy lookup needed)

        Returns:
            Normalized user info dict with keys:
            - provider_id: Microsoft's unique user identifier
            - provider_key: "microsoft"
            - email: User's email address
            - name: User's display name
            - picture: None (MS Graph /me doesn't return photo URL directly)

        Raises:
            ValueError: If access_token is missing, invalid, or request fails
        """
        if not access_token:
            raise ValueError("Missing Microsoft access token")

        url = "https://graph.microsoft.com/v1.0/me"

        try:
            async with httpx.AsyncClient(verify=certifi.where(), timeout=httpx.Timeout(15.0)) as client:
                resp = await client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json"
                    }
                )

            if resp.status_code != 200:
                text = resp.text[:300]
                raise ValueError(f"Microsoft user info request failed: HTTP {resp.status_code}: {text}")

            data = resp.json()
        except httpx.HTTPError as e:
            logger.error("Microsoft user info network error", extra={"error": str(e)}, exc_info=True)
            raise ValueError(f"Network error during Microsoft user info request: {e}")

        user_id = data.get("id")
        email = data.get("mail") or data.get("userPrincipalName")

        if not user_id or not email:
            raise ValueError("Invalid Microsoft user info: missing id or email")

        return {
            "provider_id": user_id,
            "provider_key": "microsoft",
            "email": email,
            "name": data.get("displayName") or email.split("@")[0],
            "picture": None,  # MS Graph /me doesn't return photo URL directly
        }

