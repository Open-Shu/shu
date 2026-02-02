from __future__ import annotations

import logging
import time
from datetime import UTC
from typing import Any

from sqlalchemy import and_, select

from ...core.config import get_settings_instance
from ...core.database import get_db_session
from ...models.provider_credential import ProviderCredential
from ...providers.registry import get_auth_adapter
from .base import ImmutableCapabilityMixin
from .exceptions import HttpRequestFailed
from .http_capability import HttpCapability

logger = logging.getLogger(__name__)

# Simple in-memory token cache: key -> (expires_at_epoch, access_token)
_TOKEN_CACHE: dict[tuple[str, ...], tuple[float, str]] = {}


class AuthCapability(ImmutableCapabilityMixin):
    """OAuth and authentication capability for plugins.

    Security: This class is immutable (via ImmutableCapabilityMixin) to prevent
    plugins from mutating _plugin_name or _user_id to access other users' tokens.
    """

    __slots__ = ("_ctx", "_http", "_plugin_name", "_primary_emails", "_settings", "_user_id")

    _plugin_name: str
    _user_id: str
    _http: HttpCapability
    _settings: Any
    _ctx: dict[str, Any] | None
    _primary_emails: dict[str, str | None]

    def __init__(
        self,
        *,
        plugin_name: str,
        user_id: str,
        http: HttpCapability | None = None,
        context: dict[str, Any] | None = None,
        provider_primary_emails: dict[str, str | None] | None = None,
    ) -> None:
        object.__setattr__(self, "_plugin_name", plugin_name)
        object.__setattr__(self, "_user_id", user_id)
        object.__setattr__(self, "_http", http or HttpCapability(plugin_name=plugin_name, user_id=user_id))
        object.__setattr__(self, "_settings", get_settings_instance())
        # Optional per-call overlay from __host.auth to influence provider selection
        object.__setattr__(self, "_ctx", context if isinstance(context, dict) else None)
        # Best-effort mapping of provider key -> primary email for the connected account
        object.__setattr__(
            self,
            "_primary_emails",
            {
                str(k).lower(): (v if (v is None or isinstance(v, str)) else None)
                for k, v in (provider_primary_emails or {}).items()
            },
        )

    def get_context_for(self, provider: str) -> dict[str, Any] | None:
        try:
            ctx = self._ctx or {}
            return (ctx.get(provider) or None) if isinstance(ctx, dict) else None
        except Exception:
            return None

    async def _post_form(self, url: str, data: dict[str, str]) -> dict[str, Any]:
        resp = await self._http.fetch(
            "POST", url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        if int(resp.get("status_code", 0)) >= 400:
            raise RuntimeError(f"Auth token exchange failed: HTTP {resp['status_code']}: {str(resp.get('body'))[:200]}")
        body = resp.get("body")
        return body if isinstance(body, dict) else {}

    # --- OAuth 2.0 helpers (subset needed today) ---
    def build_authorization_url(
        self,
        *,
        auth_url: str,
        client_id: str,
        redirect_uri: str,
        scopes: str | list[str],
        state: str | None = None,
        include_pkce: bool = True,
        code_challenge_method: str = "S256",
        extra_params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        import base64
        import hashlib
        import secrets

        scope_str = scopes if isinstance(scopes, str) else " ".join(scopes)
        params: dict[str, str] = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope_str,
        }
        if state:
            params["state"] = state
        result: dict[str, Any] = {}
        if include_pkce:
            # RFC 7636 PKCE (default S256)
            code_verifier = secrets.token_urlsafe(96)[:128]
            if code_challenge_method.upper() == "S256":
                digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
                challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
            else:
                challenge = code_verifier
                code_challenge_method = "plain"
            params["code_challenge"] = challenge
            params["code_challenge_method"] = code_challenge_method
            result["code_verifier"] = code_verifier
            result["code_challenge_method"] = code_challenge_method
        if extra_params:
            params.update({k: str(v) for k, v in extra_params.items() if v is not None})
        import urllib.parse

        qs = urllib.parse.urlencode(params)
        url = f"{auth_url}?{qs}"
        result["url"] = url
        return result

    async def exchange_authorization_code(
        self,
        *,
        token_url: str,
        client_id: str,
        code: str,
        redirect_uri: str,
        client_secret: str | None = None,
        code_verifier: str | None = None,
        extra_params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        data: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
        }
        if client_secret:
            data["client_secret"] = client_secret
        if code_verifier:
            data["code_verifier"] = code_verifier
        if extra_params:
            data.update({k: str(v) for k, v in extra_params.items() if v is not None})
        return await self._post_form(token_url, data)

    async def user_google_token(self, *, required_scopes: list[str] | None = None) -> str | None:
        """Return an access token for the connected Google account of the current user (via ProviderCredential).
        - If required_scopes is provided, ensure the stored grant includes all of them; otherwise return None
        - Uses the stored refresh_token in provider_credentials and refreshes via Google's token endpoint.
        """
        db = await get_db_session()
        try:
            res = await db.execute(
                select(ProviderCredential)
                .where(
                    and_(
                        ProviderCredential.user_id == self._user_id,
                        ProviderCredential.provider_key == "google",
                        ProviderCredential.is_active,
                    )
                )
                .order_by(ProviderCredential.updated_at.desc())
            )
            row = res.scalars().first()
            if not row:
                return None

            # Validate scopes first if caller provided requirements
            try:
                req = {str(s) for s in (required_scopes or []) if s}
                granted = {str(s) for s in (getattr(row, "scopes", None) or []) if s}
                if req and not req.issubset(granted):
                    logger.warning(
                        "user_google_token: insufficient scopes; required=%s granted=%s",
                        list(req),
                        list(granted),
                    )
                    return None
            except Exception:
                pass

            refresh_token = None
            try:
                refresh_token = row.get_refresh_token()
            except Exception:
                refresh_token = None
            if not refresh_token:
                # Best effort: return current access token if present
                try:
                    return row.get_access_token()
                except Exception:
                    return None

            token_url = getattr(row, "token_uri", None) or "https://oauth2.googleapis.com/token"
            client_id = getattr(row, "client_id", None) or self._settings.google_client_id
            client_secret = getattr(row, "client_secret", None) or self._settings.google_client_secret
            if not (client_id and client_secret):
                return None
            refresh_data = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            }
            # Optional downscoping: limit to required scopes
            req_scopes_list = [str(s) for s in (required_scopes or []) if s]
            if req_scopes_list:
                refresh_data["scope"] = " ".join(req_scopes_list)
                try:
                    logger.info(
                        "user_google_token: refresh with scope param",
                        extra={"scopes": req_scopes_list},
                    )
                except Exception:
                    pass
            body = await self._post_form(token_url, refresh_data)
            access_token = body.get("access_token") if isinstance(body, dict) else None
            if not access_token:
                return None

            # Optional: verify token scopes
            try:
                req_scopes = set(req_scopes_list)
                if req_scopes:
                    ti = await self._http.fetch(
                        "GET",
                        "https://www.googleapis.com/oauth2/v1/tokeninfo",
                        params={"access_token": access_token},
                        headers={"Accept": "application/json"},
                        timeout=10,
                    )
                    body_ti = ti.get("body") if isinstance(ti, dict) else None
                    scope_str = body_ti.get("scope") if isinstance(body_ti, dict) else None
                    token_scopes = {s for s in str(scope_str or "").split() if s}
                    if req_scopes and token_scopes and not req_scopes.issubset(token_scopes):
                        logger.warning(
                            "user_google_token: refreshed access token lacks required scopes; required=%s token_scopes=%s",
                            list(req_scopes),
                            list(token_scopes),
                        )
                        return None
            except Exception as e:
                try:
                    logger.warning("user_google_token: tokeninfo check failed", extra={"error": str(e)[:200]})
                except Exception:
                    pass

            # Best-effort: update access token/expiry on the row
            try:
                from datetime import datetime, timedelta

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

    async def jwt_bearer_assertion(
        self,
        *,
        token_url: str,
        issuer: str,
        scopes: str | list[str],
        subject: str | None = None,
        private_key_pem: str,
        key_id: str | None = None,
        audience: str | None = None,
        lifetime_seconds: int = 3600,
        extra_headers: dict[str, Any] | None = None,
        extra_claims: dict[str, Any] | None = None,
    ) -> str:
        # Prefer PyJWT if present; fallback to python-jose
        try:
            import jwt  # type: ignore
        except Exception:
            try:
                from jose import jwt  # type: ignore
            except Exception as e:
                raise ImportError("PyJWT or python-jose[cryptography] is required for jwt_bearer_assertion.") from e

        now = int(time.time())
        exp = now + min(max(60, lifetime_seconds), 3600)
        aud = audience or token_url
        scope_str = scopes if isinstance(scopes, str) else " ".join(scopes)
        claims: dict[str, Any] = {
            "iss": issuer,
            "scope": scope_str,
            "aud": aud,
            "iat": now,
            "exp": exp,
        }
        if subject:
            claims["sub"] = subject
        if extra_claims:
            claims.update(extra_claims)
        headers = {"alg": "RS256", "typ": "JWT"}
        if extra_headers:
            headers.update(extra_headers)
        logger.info(
            "jwt_bearer_assertion: preparing assertion",
            extra={
                "token_url": token_url,
                "issuer": issuer,
                "subject": subject or "",
                "audience": aud,
                "scopes": scope_str.split(" "),
            },
        )
        assertion = jwt.encode(claims, private_key_pem, algorithm="RS256", headers=headers)

        cache_key = (
            "jwt_bearer",
            token_url,
            issuer,
            subject or "",
            scope_str,
            str(hash(private_key_pem)),
        )
        cached = _TOKEN_CACHE.get(cache_key)
        if cached and cached[0] - 60 > now:
            return cached[1]

        body = await self._post_form(
            token_url,
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        )
        token = body.get("access_token")
        if not token:
            logger.error(
                "jwt_bearer_assertion: token missing in response",
                extra={
                    "token_url": token_url,
                    "issuer": issuer,
                    "subject": subject or "",
                    "status": body.get("error") or body.get("error_description") or "unknown",
                },
            )
            raise RuntimeError(f"Token response missing access_token: {body}")
        expires_in = int(body.get("expires_in", 3600))
        _TOKEN_CACHE[cache_key] = (now + min(expires_in, 3600), token)
        return token

    async def google_service_account_token(self, *, scopes: list[str], subject: str | None = None) -> str:
        import json
        import os

        sa_info: dict[str, Any] | None = None
        if self._settings.google_service_account_json:
            raw = self._settings.google_service_account_json
            try:
                raw_str = raw.strip()
                if raw_str.startswith("{"):
                    sa_info = json.loads(raw_str)
                elif os.path.exists(raw_str):
                    with open(raw_str, encoding="utf-8") as f:
                        sa_info = json.load(f)
                else:
                    sa_info = json.loads(raw_str)
            except Exception as e:
                raise RuntimeError(
                    "Invalid GOOGLE_SERVICE_ACCOUNT_JSON content or path; set GOOGLE_SERVICE_ACCOUNT_JSON to a file path or inline JSON"
                ) from e
        elif self._settings.google_service_account_file:
            fp = self._settings.google_service_account_file
            if not os.path.exists(fp):
                raise RuntimeError(f"Service account file not found: {fp}")
            with open(fp, encoding="utf-8") as f:
                sa_info = json.load(f)
        else:
            raise RuntimeError(
                "Service account credentials not configured (set GOOGLE_SERVICE_ACCOUNT_JSON to a file path or inline JSON)"
            )

        assert sa_info is not None, "Service account JSON not loaded"
        client_email = sa_info.get("client_email")
        private_key = sa_info.get("private_key")
        token_uri = sa_info.get("token_uri", "https://oauth2.googleapis.com/token")
        key_id = sa_info.get("private_key_id")
        if not (client_email and private_key):
            raise RuntimeError("Service account JSON missing client_email or private_key")
        if "\\n" in private_key:
            private_key = private_key.replace("\\n", "\n")

        # Try google-auth Credentials flow first
        try:
            from google.auth.transport.requests import Request as _GARequest  # type: ignore
            from google.oauth2 import service_account as _sa  # type: ignore

            creds = _sa.Credentials.from_service_account_info(
                sa_info,
                scopes=scopes,
                subject=subject,
            )
            creds.refresh(_GARequest())
            if getattr(creds, "token", None):
                return str(creds.token)
        except Exception:
            pass

        # Fallback: manual JWT bearer assertion
        return await self.jwt_bearer_assertion(
            token_url=token_uri,
            issuer=client_email,
            scopes=scopes,
            subject=subject,
            private_key_pem=private_key,
            key_id=key_id,
            audience=token_uri,
        )

    async def google_domain_delegation_check(self, *, scopes: list[str], subject: str) -> dict[str, Any]:
        import json
        import os

        sa_info: dict[str, Any] | None = None
        raw = self._settings.google_service_account_json or self._settings.google_service_account_file
        if not raw:
            return {
                "ready": False,
                "status": 0,
                "error": {"message": "Service account not configured (GOOGLE_SERVICE_ACCOUNT_JSON)"},
            }
        try:
            raw_str = str(raw).strip()
            if raw_str.startswith("{"):
                sa_info = json.loads(raw_str)
            elif os.path.exists(raw_str):
                with open(raw_str, encoding="utf-8") as f:
                    sa_info = json.load(f)
            else:
                sa_info = json.loads(raw_str)
        except Exception as e:
            return {
                "ready": False,
                "status": 0,
                "error": {"message": f"Failed to load service account JSON: {e}"},
            }
        client_email = (sa_info or {}).get("client_email")
        private_key = (sa_info or {}).get("private_key")
        token_uri = (sa_info or {}).get("token_uri", "https://oauth2.googleapis.com/token")
        client_id = (sa_info or {}).get("client_id")
        if not (client_email and private_key):
            return {
                "ready": False,
                "status": 0,
                "client_id": client_id,
                "issuer": client_email,
                "error": {"message": "Service account JSON missing client_email or private_key"},
            }
        if "\\n" in private_key:
            private_key = private_key.replace("\\n", "\n")
        try:
            from jose import jwt  # type: ignore
        except Exception as e:
            return {
                "ready": False,
                "status": 0,
                "client_id": client_id,
                "issuer": client_email,
                "error": {"message": f"python-jose not available: {e}"},
            }
        now = int(time.time())
        exp = now + 3600
        aud = token_uri
        scope_str = " ".join(scopes)
        claims: dict[str, Any] = {
            "iss": client_email,
            "scope": scope_str,
            "aud": aud,
            "iat": now,
            "exp": exp,
            "sub": subject,
        }
        headers = {"alg": "RS256", "typ": "JWT"}
        assertion = jwt.encode(claims, private_key, algorithm="RS256", headers=headers)
        try:
            resp = await self._http.fetch(
                "POST",
                token_uri,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            status = int(resp.get("status_code", 0))
            body = resp.get("body")
        except HttpRequestFailed as e:
            logger.warning(
                "Google domain delegation check HTTP error",
                extra={"status_code": e.status_code, "url": e.url},
            )
            status = int(getattr(e, "status_code", 0))
            body = getattr(e, "body", None)
        ok = (status == 200) and isinstance(body, dict) and bool(body.get("access_token"))
        if ok:
            return {
                "ready": True,
                "status": status,
                "client_id": client_id,
                "issuer": client_email,
                "scopes": scopes,
            }
        err_msg = None
        if isinstance(body, dict):
            err_msg = body.get("error_description") or body.get("error") or str(body)
        else:
            err_msg = str(body)[:400]
        return {
            "ready": False,
            "status": status,
            "client_id": client_id,
            "issuer": client_email,
            "scopes": scopes,
            "error": {"message": err_msg},
        }

    # --- Provider registry facade (AUTH-REF-001) ---
    async def provider_user_token(self, provider: str, *, required_scopes: list[str] | None = None) -> str | None:
        prov = (provider or "").lower().strip()
        adapter = get_auth_adapter(prov, self)
        return await adapter.user_token(required_scopes=required_scopes)

    async def provider_service_account_token(
        self, provider: str, *, scopes: list[str], subject: str | None = None
    ) -> str:
        prov = (provider or "").lower().strip()
        adapter = get_auth_adapter(prov, self)
        return await adapter.service_account_token(scopes=scopes, subject=subject)

    async def provider_delegation_check(self, provider: str, *, scopes: list[str], subject: str) -> dict[str, Any]:
        prov = (provider or "").lower().strip()
        adapter = get_auth_adapter(prov, self)
        return await adapter.delegation_check(scopes=scopes, subject=subject)

    async def resolve_token_and_target(
        self, provider: str, *, scopes: list[str] | None = None
    ) -> tuple[str | None, str | None]:
        try:
            sel = self.get_context_for(provider) if hasattr(self, "get_context_for") else None
        except Exception:
            sel = None
        if not isinstance(sel, dict):
            return None, None
        mode = str(sel.get("mode") or "").lower()
        subject = (sel.get("subject") or "").strip() or None
        sc = scopes or []
        if not sc:
            ctx_sc = sel.get("scopes")
            if isinstance(ctx_sc, (list, tuple)):
                sc = [str(x) for x in ctx_sc if x]
            elif isinstance(ctx_sc, str):
                sc = [s for s in ctx_sc.split() if s]
            else:
                sc = []
        if mode == "user":
            try:
                logger.info(f"auth.resolve_token_and_target provider={provider} mode={mode} required_scopes={sc}")
            except Exception:
                pass
            tok = await self.provider_user_token(provider, required_scopes=sc or None)
            if not tok:
                return None, None
            # For end-user OAuth tokens, Google recommends using 'users/me'
            # This also avoids odd edge cases when passing explicit emails.
            return tok, "me"
        if mode == "domain_delegate":
            if not subject:
                raise RuntimeError("Impersonation email is required for Domain-wide Delegation (impersonation).")
            if not sc:
                raise RuntimeError("Missing OAuth scopes for domain delegation. Ensure op_auth.scopes are provided.")
            tok = await self.provider_service_account_token(provider, scopes=sc, subject=subject)
            return (tok, subject) if tok else (None, None)
        if mode == "service_account":
            if not sc:
                raise RuntimeError("Missing OAuth scopes for service account. Ensure op_auth.scopes are provided.")
            tok = await self.provider_service_account_token(provider, scopes=sc, subject=None)
            return (tok, None) if tok else (None, None)
        return None, None
