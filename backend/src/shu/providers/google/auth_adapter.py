from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING, Any

import certifi
import httpx

from ...core.logging import get_logger
from ..base_auth_adapter import BaseAuthAdapter

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


logger = get_logger(__name__)


class GoogleAuthAdapter(BaseAuthAdapter):
    """Thin adapter delegating to existing Google-specific helpers on AuthCapability.

    This keeps step 1 (AUTH-REF-002) low-risk by avoiding large moves. In a later
    step we can migrate logic into the adapter itself if needed.
    """

    # TODO: Refactor this function. It's too complex (number of branches and statements).
    async def user_token(self, *, required_scopes: list[str] | None = None) -> str | None:  # noqa: PLR0912, PLR0915
        """Fetch a Google user access token from ProviderCredential, refreshing via OAuth if needed.
        Returns None if no credential or required scopes are not granted.
        """
        from datetime import datetime, timedelta

        from sqlalchemy import and_, select  # type: ignore

        from ...core.database import get_db_session  # type: ignore
        from ...models.provider_credential import ProviderCredential  # type: ignore

        settings = self._auth._settings
        db = await get_db_session()
        try:
            res = await db.execute(
                select(ProviderCredential)
                .where(
                    and_(
                        ProviderCredential.user_id == self._auth._user_id,
                        ProviderCredential.provider_key == "google",
                        ProviderCredential.is_active == True,  # noqa: E712
                    )
                )
                .order_by(ProviderCredential.updated_at.desc())
            )
            row = res.scalars().first()
            if not row:
                try:
                    logger.info(
                        "google.user_token: no credential row for user; provider=google user_id=%s",
                        self._auth._user_id,
                    )
                except Exception:
                    pass
                return None

            # Validate required scopes if provided
            try:
                req = {str(s) for s in (required_scopes or []) if s}
                granted_list = [str(s) for s in (getattr(row, "scopes", None) or []) if s]
                granted = set(granted_list)
                try:
                    logger.debug(
                        "google.user_token: required_scopes=%s granted_scopes(row)=%s",
                        list(req),
                        granted_list,
                    )
                except Exception:
                    pass
                if req and not req.issubset(granted):
                    try:
                        logger.warning(
                            "google.user_token: insufficient scopes; required=%s granted=%s",
                            list(req),
                            list(granted),
                        )
                    except Exception:
                        pass
                    return None
            except Exception:
                pass

            refresh_token = None
            try:
                refresh_token = row.get_refresh_token()
            except Exception:
                refresh_token = None

            # If no refresh token, return current access token best-effort
            if not refresh_token:
                try:
                    return row.get_access_token()
                except Exception:
                    return None

            token_url = getattr(row, "token_uri", None) or "https://oauth2.googleapis.com/token"
            client_id = getattr(row, "client_id", None) or settings.google_client_id
            client_secret = getattr(row, "client_secret", None) or settings.google_client_secret
            if not (client_id and client_secret):
                return None

            data = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            }
            req_scopes_list = [str(s) for s in (required_scopes or []) if s]
            if req_scopes_list:
                data["scope"] = " ".join(req_scopes_list)

            body = await self._auth._post_form(token_url, data)  # type: ignore
            access_token = body.get("access_token") if isinstance(body, dict) else None
            if not access_token:
                return None

            # Optional: verify scopes with tokeninfo
            try:
                if req_scopes_list:
                    ti = await self._auth._http.fetch(  # type: ignore
                        "GET",
                        "https://www.googleapis.com/oauth2/v1/tokeninfo",
                        params={"access_token": access_token},
                        headers={"Accept": "application/json"},
                        timeout=10,
                    )
                    body_ti = ti.get("body") if isinstance(ti, dict) else None
                    scope_str = body_ti.get("scope") if isinstance(body_ti, dict) else None
                    token_scopes = {s for s in str(scope_str or "").split() if s}
                    if token_scopes and not set(req_scopes_list).issubset(token_scopes):
                        return None
            except Exception:
                pass

            # Update access token and expiry
            try:
                row.set_access_token(access_token)
                exp_in = int((body.get("expires_in") if isinstance(body, dict) else 3600) or 3600)
                row.expires_at = datetime.now(UTC) + timedelta(seconds=max(1, exp_in))
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

    async def service_account_token(self, *, scopes: list[str], subject: str | None = None) -> str:
        return await self._auth.google_service_account_token(scopes=scopes, subject=subject)

    async def delegation_check(self, *, scopes: list[str], subject: str) -> dict[str, Any]:
        return await self._auth.google_domain_delegation_check(scopes=scopes, subject=subject)

    # Step 2 usage by API endpoints
    async def build_authorization_url(self, *, scopes: list[str]) -> dict[str, Any]:
        from urllib.parse import urlparse

        from google_auth_oauthlib.flow import Flow  # type: ignore

        settings = self._auth._settings
        redirect_uri = settings.get_oauth_redirect_uri("google")
        if not (settings.google_client_id and settings.google_client_secret and redirect_uri):
            raise RuntimeError("Google OAuth is not configured")

        # Validate redirect URI is absolute (Google requires full origin + path)
        parsed = urlparse(redirect_uri)
        if not parsed.scheme or not parsed.netloc:
            raise RuntimeError("OAUTH_REDIRECT_URI must be an absolute URL, e.g., http://localhost:8000/auth/callback")

        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [redirect_uri],
                }
            },
            scopes=scopes,
        )
        flow.redirect_uri = redirect_uri
        authorization_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes=False,  # Do not implicitly request previously granted scopes; honor requested scopes only
            prompt="consent",
            state="provider=google",
        )
        return {"url": authorization_url, "state": state}

    async def exchange_code(self, *, code: str, scopes: list[str] | None = None) -> dict[str, Any]:
        import requests

        settings = self._auth._settings
        redirect_uri = settings.get_oauth_redirect_uri("google")
        if not (settings.google_client_id and settings.google_client_secret and redirect_uri):
            raise RuntimeError("Google OAuth is not configured")
        resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
        )
        if not resp.ok:
            raise RuntimeError(f"Provider token exchange failed: {resp.text[:300]}")
        return resp.json() or {}

    async def status(self, *, user_id: str, db) -> dict[str, Any]:
        from sqlalchemy import and_, select  # type: ignore

        from ...models.provider_credential import ProviderCredential  # type: ignore

        settings = self._auth._settings
        result = await db.execute(
            select(ProviderCredential).where(
                and_(
                    ProviderCredential.user_id == user_id,
                    ProviderCredential.provider_key == "google",
                    ProviderCredential.is_active == True,  # noqa: E712
                )
            )
        )
        creds = result.scalars().all()
        scopes_union: list[str] = []
        for c in creds:
            try:
                for s in c.scopes or []:
                    if s not in scopes_union:
                        scopes_union.append(s)
            except Exception:
                pass
        meta: dict[str, Any] = {}
        try:
            redirect_uri = settings.get_oauth_redirect_uri("google")
            meta = {
                "user_oauth_configured": bool(
                    getattr(settings, "google_client_id", None)
                    and getattr(settings, "google_client_secret", None)
                    and redirect_uri
                ),
                "service_account_configured": bool(
                    getattr(settings, "google_service_account_json", None)
                    or getattr(settings, "google_service_account_file", None)
                ),
                "google_domain": getattr(settings, "google_domain", None) or None,
            }
        except Exception:
            meta = {}
        return {
            "user_connected": len(creds) > 0,
            "granted_scopes": scopes_union,
            **meta,
        }

    async def disconnect(self, *, user_id: str, db) -> None:
        from sqlalchemy import delete  # type: ignore

        from ...models.provider_credential import ProviderCredential  # type: ignore

        await db.execute(
            delete(ProviderCredential).where(
                ProviderCredential.user_id == user_id,
                ProviderCredential.provider_key == "google",
            )
        )

    async def get_user_info(
        self,
        *,
        access_token: str | None = None,  # Unused - Google uses id_token
        id_token: str | None = None,
        db: AsyncSession | None = None,  # Unused, kept for interface compatibility
    ) -> dict[str, Any]:
        """Verify Google ID token and return normalized user info.

        Args:
            _access_token: Not used for Google (Google uses id_token)
            id_token: The Google ID token to verify
            db: Unused, kept for interface compatibility

        Returns:
            Normalized user info dict with keys:
            - provider_id: Google's unique user identifier (sub claim)
            - provider_key: "google"
            - email: User's email address
            - name: User's display name
            - picture: Avatar URL (optional)

        Raises:
            ValueError: If id_token is missing, invalid, or verification fails

        """
        if not id_token:
            raise ValueError("Missing Google ID token")

        url = "https://oauth2.googleapis.com/tokeninfo"
        settings = self._auth._settings

        try:
            async with httpx.AsyncClient(verify=certifi.where(), timeout=httpx.Timeout(15.0)) as client:
                resp = await client.get(url, params={"id_token": id_token}, headers={"Accept": "application/json"})

            if resp.status_code != 200:
                text = resp.text[:300]
                raise ValueError(f"Google token verification failed: HTTP {resp.status_code}: {text}")

            data = resp.json()
        except httpx.HTTPError as e:
            logger.error("Google token verification network error", extra={"error": str(e)}, exc_info=True)
            raise ValueError(f"Network error during Google token verification: {e}") from e

        # Verify audience claim matches our client ID
        aud = data.get("aud")
        expected_client_id = settings.google_client_id
        if not aud or aud != expected_client_id:
            logger.error("Google ID token audience mismatch", extra={"aud": aud, "expected": expected_client_id})
            raise ValueError("Invalid Google ID token: audience mismatch")

        sub = data.get("sub")
        email = data.get("email")

        if not sub or not email:
            raise ValueError("Invalid Google ID token payload: missing sub or email")

        return {
            "provider_id": sub,
            "provider_key": "google",
            "email": email,
            "name": data.get("name") or email.split("@")[0],
            "picture": data.get("picture"),
        }
