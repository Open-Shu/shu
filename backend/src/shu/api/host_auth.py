"""Generic Host Auth endpoints for provider connection status and OAuth flows.

Currently supports minimal status for Google (Gmail) user credentials.
"""

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.models import User
from ..auth.rbac import get_current_user
from ..core.logging import get_logger
from ..core.response import ShuResponse
from ..models.provider_credential import ProviderCredential
from ..models.provider_identity import ProviderIdentity
from .dependencies import get_db

logger = get_logger(__name__)

from datetime import UTC, datetime, timedelta

import requests
from fastapi import HTTPException, status
from pydantic import BaseModel

from ..core.oauth_encryption import OAuthEncryptionError
from ..plugins.host.auth_capability import AuthCapability
from ..providers.registry import get_auth_adapter

router = APIRouter(prefix="/host/auth", tags=["host-auth"])


@router.get("/status")
async def get_host_auth_status(
    providers: str | None = Query(
        None,
        description="Comma-separated providers, e.g., 'google'",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return connection status for requested identity providers.

    Response format (subset):
    {
      "google": { "user_connected": bool, "granted_scopes": ["..."] }
    }
    """
    requested: list[str] = [p.strip().lower() for p in providers.split(",")] if providers else ["google"]
    out: dict[str, Any] = {}

    # Provider-agnostic status via adapter when available; fallback to ProviderIdentity
    for p in requested:
        try:
            auth = AuthCapability(plugin_name="admin", user_id=str(current_user.id))
            adapter = get_auth_adapter(p, auth)
            status_payload = await adapter.status(user_id=str(current_user.id), db=db)
            if isinstance(status_payload, dict):
                out[p] = status_payload
                continue
        except NotImplementedError:
            pass
        except Exception as e:
            logger.warning(f"Adapter status failed for provider={p}: {e}")
        # Fallback: ProviderIdentity only
        try:
            res = await db.execute(
                select(ProviderIdentity).where(
                    and_(
                        ProviderIdentity.user_id == str(current_user.id),
                        ProviderIdentity.provider_key == p,
                    )
                )
            )
            pis = res.scalars().all()
            user_connected = len(pis) > 0
            scopes_union: list[str] = []
            for pi in pis:
                try:
                    for s in pi.scopes or []:
                        s_str = str(s)
                        # Normalize Microsoft scopes in fallback path
                        if p == "microsoft" and s_str and not s_str.startswith("https://"):
                            s_str = f"https://graph.microsoft.com/{s_str}"
                        if s_str not in scopes_union:
                            scopes_union.append(s_str)
                except Exception:
                    pass
            out[p] = {"user_connected": bool(user_connected), "granted_scopes": scopes_union}
        except Exception:
            out[p] = {"user_connected": False, "granted_scopes": []}

    return ShuResponse.success(out)


class AuthorizeResponse(BaseModel):
    provider: str
    authorization_url: str
    state: str | None = None


@router.get("/authorize")
async def host_auth_authorize(
    provider: str = Query(..., description="Identity provider, e.g., 'google'"),
    scopes: str | None = Query(None, description="Comma-separated scopes for OAuth consent"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return an authorization URL for the requested provider via adapter registry.

    Frontend should open this URL to complete consent. Until TASK-163 lands,
    scopes must be provided by the caller.
    """
    provider = (provider or "").lower().strip()

    # Parse scopes; if omitted, compute from user's subscriptions (fallback to all enabled plugins)
    scope_list: list[str] = []
    if scopes:
        scope_list = [s.strip() for s in scopes.split(",") if s.strip()]
    if not scope_list:
        try:
            from ..services.host_auth_service import HostAuthService

            scope_list = await HostAuthService.compute_consent_scopes(db, str(current_user.id), provider)
        except Exception as e:
            logger.error(f"compute_consent_scopes failed: {e}")
            scope_list = []
    if not scope_list:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No scopes available. Subscribe plugins or pass scopes explicitly.",
        )

    # Build authorization URL via provider adapter
    try:
        auth = AuthCapability(plugin_name="admin", user_id=str(current_user.id))
        adapter = get_auth_adapter(provider, auth)
    except NotImplementedError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported provider")

    try:
        res = await adapter.build_authorization_url(scopes=scope_list)
        authorization_url = res.get("url") or res.get("authorization_url")
        state = res.get("state")
        if not authorization_url:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Authorization URL generation failed",
            )
        return ShuResponse.success(
            {
                "provider": provider,
                "authorization_url": authorization_url,
                "state": state,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Host auth authorize failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authorization URL generation failed",
        )


class ConsentScopesResponse(BaseModel):
    provider: str
    scopes: list[str]


@router.get("/consent-scopes")
async def host_auth_consent_scopes(
    provider: str = Query(..., description="Identity provider, e.g., 'google' or 'microsoft'"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the server-computed union of delegated scopes required by enabled plugins for the provider.

    This is an initial server-side computation to support TASK-163. It derives scope strings
    from plugin manifests (op_auth) for the given provider with mode='user'. Once plugin
    subscriptions are persisted, this endpoint will return the union for the user's selected
    subscriptions instead of all enabled plugins.
    """
    prov = (provider or "").strip().lower()
    try:
        from ..services.host_auth_service import HostAuthService

        union_scopes = await HostAuthService.compute_consent_scopes(db, str(current_user.id), prov)
        return ShuResponse.success({"provider": prov, "scopes": union_scopes})
    except Exception as e:
        logger.error(f"consent-scopes compute failed for provider={prov}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Consent scopes computation failed",
        )


class SubscriptionIn(BaseModel):
    provider: str
    plugin_name: str
    account_id: str | None = None


class SubscriptionListResponse(BaseModel):
    items: list[dict]


@router.get("/subscriptions")
async def list_subscriptions(
    provider: str = Query(...),
    account_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List the caller's plugin subscriptions for a provider (optional: one account_id)."""
    from ..services.host_auth_service import HostAuthService

    prov = (provider or "").strip().lower()
    rows = await HostAuthService.list_subscriptions(db, str(current_user.id), prov, account_id)
    return ShuResponse.success(
        {
            "items": [
                {
                    "id": r.id,
                    "plugin_name": r.plugin_name,
                    "provider": r.provider_key,
                    "account_id": r.account_id,
                }
                for r in rows
            ]
        }
    )


@router.post("/subscriptions")
async def create_subscription(
    body: SubscriptionIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create or idempotently upsert a plugin subscription for the current user."""
    from ..services.host_auth_service import HostAuthService

    try:
        rec = await HostAuthService.validate_and_create_subscription(
            db,
            str(current_user.id),
            body.provider,
            body.plugin_name,
            body.account_id,
        )
    except ValueError as ve:
        raise HTTPException(status_code=422, detail=str(ve))
    except LookupError as le:
        raise HTTPException(status_code=400, detail=str(le))

    return ShuResponse.success(
        {
            "id": rec.id,
            "plugin_name": rec.plugin_name,
            "provider": rec.provider_key,
            "account_id": rec.account_id,
        }
    )


@router.delete("/subscriptions")
async def delete_subscription(
    body: SubscriptionIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from ..services.host_auth_service import HostAuthService

    try:
        deleted = await HostAuthService.delete_subscription(
            db,
            str(current_user.id),
            body.provider,
            body.plugin_name,
            body.account_id,
        )
    except ValueError as ve:
        raise HTTPException(status_code=422, detail=str(ve))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to delete subscription")
    return ShuResponse.success({"deleted": bool(deleted)})


class ExchangeRequest(BaseModel):
    provider: str
    code: str
    scopes: list[str] | None = None


@router.post("/exchange")
async def host_auth_exchange(
    body: ExchangeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Exchange authorization code for tokens and store them (provider-agnostic).

    For provider=google, tokens are stored in ProviderCredential and identity is upserted in ProviderIdentity.
    """
    provider = (body.provider or "").lower().strip()
    try:
        auth = AuthCapability(plugin_name="admin", user_id=str(current_user.id))
        adapter = get_auth_adapter(provider, auth)
    except NotImplementedError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported provider")

    # Provider configuration validation is handled by the adapter; do not enforce Google settings here.

    requested_scopes = body.scopes or ["openid", "email", "profile"]

    try:
        # Exchange via provider adapter
        try:
            logger.debug("host_auth.exchange: requested_scopes(pre-exchange)=%s", requested_scopes)
        except Exception:
            pass
        try:
            tok = await adapter.exchange_code(code=body.code, scopes=requested_scopes)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Provider token exchange failed: {str(e)[:300]}",
            )
        access_token = tok.get("access_token")
        refresh_token = tok.get("refresh_token")
        expires_in = tok.get("expires_in")
        expiry = None
        if expires_in:
            try:
                expiry = datetime.now(UTC) + timedelta(seconds=int(expires_in))
            except Exception:
                expiry = None
        scope_str = tok.get("scope")
        token_scopes = [s for s in scope_str.split() if s] if isinstance(scope_str, str) else requested_scopes

        if not access_token or not refresh_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to obtain tokens from provider",
            )

        # Normalize Microsoft scopes: add URL prefix if missing
        if provider == "microsoft":
            normalized_scopes = []
            for scope in (token_scopes or requested_scopes):
                if scope and not scope.startswith("https://"):
                    # Microsoft returns short-form scopes like "Mail.Read"
                    # but manifests declare them as "https://graph.microsoft.com/Mail.Read"
                    normalized_scopes.append(f"https://graph.microsoft.com/{scope}")
                else:
                    normalized_scopes.append(scope)
            final_scopes = normalized_scopes
        else:
            final_scopes = token_scopes or requested_scopes
        # Log what we will persist for diagnosis of scope issues
        try:
            logger.info(
                "host_auth.exchange: provider=%s user=%s scopes=%s",
                provider,
                getattr(current_user, "email", None),
                list(set(final_scopes or [])),
            )
            logger.debug(
                "host_auth.exchange: token_scopes(from_provider)=%s requested_scopes(body)=%s final_scopes(persisted)=%s",
                token_scopes,
                requested_scopes,
                final_scopes,
            )
        except Exception:
            pass

        # Create provider-agnostic credential
        from ..models.provider_credential import ProviderCredential

        pc = ProviderCredential(
            user_id=str(current_user.id),
            provider_key=provider,
            scopes=list(set(final_scopes or [])),
            expires_at=expiry if isinstance(expiry, datetime) else None,
            is_active=True,
        )
        pc.set_access_token(access_token)
        if refresh_token:
            pc.set_refresh_token(refresh_token)
        db.add(pc)

        await db.commit()
        await db.refresh(pc)
        try:
            logger.debug(
                "host_auth.exchange: persisted credential id=%s scopes=%s",
                getattr(pc, "id", None),
                getattr(pc, "scopes", None),
            )
        except Exception:
            pass

        # Persist provider-agnostic identity at connect time (OIDC userinfo for Google only)
        try:
            if provider == "google":
                info_resp = requests.get(
                    "https://openidconnect.googleapis.com/v1/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10,
                )
                if info_resp.ok:
                    info = info_resp.json() or {}
                    account_id = str(info.get("sub") or "")
                    primary_email = info.get("email")
                    display_name = info.get("name")
                    avatar_url = info.get("picture")
                    try:
                        pc.account_id = account_id or (primary_email or None)
                        await db.commit()
                        await db.refresh(pc)
                    except Exception:
                        pass
                    # Upsert ProviderIdentity
                    existing_pi = None
                    try:
                        pi_q = select(ProviderIdentity).where(
                            and_(
                                ProviderIdentity.user_id == str(current_user.id),
                                ProviderIdentity.provider_key == provider,
                                ProviderIdentity.account_id == (account_id or (primary_email or "")),
                            )
                        )
                        pi_res = await db.execute(pi_q)
                        existing_pi = pi_res.scalar_one_or_none()
                    except Exception:
                        existing_pi = None
                    if existing_pi:
                        if primary_email:
                            existing_pi.primary_email = primary_email
                        if display_name:
                            existing_pi.display_name = display_name
                        if avatar_url:
                            existing_pi.avatar_url = avatar_url
                        existing_pi.scopes = pc.scopes
                        existing_pi.credential_id = pc.id
                        existing_pi.identity_meta = info
                    else:
                        pi = ProviderIdentity(
                            user_id=str(current_user.id),
                            provider_key=provider,
                            account_id=(account_id or (primary_email or "")),
                            primary_email=primary_email,
                            display_name=display_name,
                            avatar_url=avatar_url,
                            scopes=pc.scopes,
                            credential_id=pc.id,
                            identity_meta=info,
                        )
                        db.add(pi)
                    await db.commit()

            elif provider == "microsoft":
                info_resp = requests.get(
                    "https://graph.microsoft.com/v1.0/me",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10,
                )
                if info_resp.ok:
                    info = info_resp.json() or {}
                    account_id = str(info.get("id") or (info.get("userPrincipalName") or ""))
                    primary_email = info.get("mail") or info.get("userPrincipalName")
                    display_name = info.get("displayName")
                    avatar_url = None
                    try:
                        pc.account_id = account_id or (primary_email or None)
                        await db.commit()
                        await db.refresh(pc)
                    except Exception:
                        pass
                    # Upsert ProviderIdentity
                    existing_pi = None
                    try:
                        pi_q = select(ProviderIdentity).where(
                            and_(
                                ProviderIdentity.user_id == str(current_user.id),
                                ProviderIdentity.provider_key == provider,
                                ProviderIdentity.account_id == (account_id or (primary_email or "")),
                            )
                        )
                        pi_res = await db.execute(pi_q)
                        existing_pi = pi_res.scalar_one_or_none()
                    except Exception:
                        existing_pi = None
                    if existing_pi:
                        if primary_email:
                            existing_pi.primary_email = primary_email
                        if display_name:
                            existing_pi.display_name = display_name
                        if avatar_url:
                            existing_pi.avatar_url = avatar_url
                        existing_pi.scopes = pc.scopes
                        existing_pi.credential_id = pc.id
                        existing_pi.identity_meta = info
                    else:
                        pi = ProviderIdentity(
                            user_id=str(current_user.id),
                            provider_key=provider,
                            account_id=(account_id or (primary_email or "")),
                            primary_email=primary_email,
                            display_name=display_name,
                            avatar_url=avatar_url,
                            scopes=pc.scopes,
                            credential_id=pc.id,
                            identity_meta=info,
                        )
                        db.add(pi)
                    await db.commit()

        except Exception as e:
            logger.warning(f"Failed to persist provider identity: {e}")

        return ShuResponse.success(
            {
                "provider": provider,
                "user_connected": True,
                "granted_scopes": pc.scopes,
                "expires_at": pc.expires_at.isoformat() if isinstance(pc.expires_at, datetime) else None,
            }
        )

    except HTTPException:
        raise
    except OAuthEncryptionError as e:
        await db.rollback()
        logger.error(f"Host auth exchange failed (encryption): {e}")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except Exception as e:
        await db.rollback()
        logger.error(f"Host auth exchange failed: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Token exchange failed")


class DisconnectRequest(BaseModel):
    provider: str


@router.post("/disconnect")
async def host_auth_disconnect(
    body: DisconnectRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Disconnect current user's account for a provider by deleting stored credentials and identities.

    Provider-specific credential cleanup is handled by the adapter; we always remove ProviderIdentity rows here.
    """
    provider = (body.provider or "").lower().strip()

    try:
        # Provider-specific cleanup via adapter (no-op if provider unsupported)
        try:
            auth = AuthCapability(plugin_name="admin", user_id=str(current_user.id))
            adapter = get_auth_adapter(provider, auth)
            await adapter.disconnect(user_id=str(current_user.id), db=db)
        except NotImplementedError:
            pass
        except Exception as e:
            logger.warning(f"Adapter disconnect failed for provider={provider}: {e}")

        # Delete ProviderIdentity rows for this user/provider
        await db.execute(
            delete(ProviderIdentity).where(
                ProviderIdentity.user_id == current_user.id,
                ProviderIdentity.provider_key == provider,
            )
        )
        # Delete ProviderCredential rows for this user/provider (fully remove tokens)
        await db.execute(
            delete(ProviderCredential).where(
                ProviderCredential.user_id == str(current_user.id),
                ProviderCredential.provider_key == provider,
            )
        )
        await db.commit()
        return ShuResponse.success({"provider": provider, "disconnected": True})
    except Exception as e:
        await db.rollback()
        logger.error(f"Host auth disconnect failed: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Disconnect failed")


class DelegationCheckRequest(BaseModel):
    subject: str
    scopes: list[str] | None = None


class GenericDelegationCheckRequest(BaseModel):
    provider: str
    subject: str
    scopes: list[str] | None = None


class ServiceAccountCheckRequest(BaseModel):
    provider: str
    scopes: list[str] | None = None


@router.post("/delegation-check")
async def host_auth_delegation_check(
    body: GenericDelegationCheckRequest,
    current_user: User = Depends(get_current_user),
):
    """Provider-agnostic domain delegation readiness probe.

    Body: { provider: "google" | ..., subject: str, scopes?: string[] }
    Returns: {ready: bool, status: int, client_id: str, issuer: str, scopes: [...], error: {...}}
    """
    try:
        provider = (body.provider or "").lower().strip()
        auth = AuthCapability(plugin_name="admin", user_id=str(current_user.id))
        scopes = body.scopes or []
        if not scopes:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Missing required scopes for delegation check",
            )
        try:
            res = await auth.provider_delegation_check(provider, scopes=scopes, subject=body.subject)
        except NotImplementedError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported provider")
        return ShuResponse.success(res)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delegation check failed: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Delegation check failed")


@router.post("/service-account-check")
async def host_auth_service_account_check(
    body: ServiceAccountCheckRequest,
    current_user: User = Depends(get_current_user),
):
    """Provider-agnostic service account readiness probe (no impersonation).

    Body: { provider: "google" | ..., scopes?: string[] }
    Returns: {ready: bool, status: int, client_id?: str, issuer?: str, scopes: [...], error?: {...}}
    """
    try:
        provider = (body.provider or "").lower().strip()
        auth = AuthCapability(plugin_name="admin", user_id=str(current_user.id))
        scopes = body.scopes or []
        if not scopes:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Missing required scopes for service account check",
            )
        # Attempt to obtain a token without impersonation
        try:
            token = await auth.provider_service_account_token(provider, scopes=scopes, subject=None)
        except NotImplementedError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported provider")
        except Exception as e:
            return ShuResponse.success({"ready": False, "status": 0, "scopes": scopes, "error": {"message": str(e)}})
        return ShuResponse.success(
            {
                "ready": bool(token),
                "status": 200 if token else 0,
                "scopes": scopes,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Service account check failed: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Service account check failed")


@router.post("/google/delegation-check")
async def host_auth_google_delegation_check(
    body: DelegationCheckRequest,
    current_user: User = Depends(get_current_user),
):
    """Probe Google Domain-Wide Delegation readiness for the given subject.

    Returns: {ready: bool, status: int, client_id: str, issuer: str, scopes: [...], error: {...}}
    """
    try:
        auth = AuthCapability(plugin_name="admin", user_id=str(current_user.id))
        scopes = body.scopes or [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.modify",
        ]
        res = await auth.google_domain_delegation_check(scopes=scopes, subject=body.subject)
        return ShuResponse.success(res)
    except Exception as e:
        logger.error(f"Delegation check failed: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Delegation check failed")


from fastapi.responses import HTMLResponse


@router.get("/callback")
async def host_auth_callback(provider: str | None = None, code: str = "", state: str | None = None):
    """OAuth callback helper that posts the code back to opener and closes the window.

    Configure GOOGLE_REDIRECT_URI to this route: <base>/api/v1/host/auth/callback
    """
    prov = (provider or "").lower().strip()
    if not prov and state:
        # Expect state like "provider=google"; fallback to empty
        try:
            parts = dict(item.split("=", 1) for item in state.split("&") if "=" in item)
            prov = (parts.get("provider") or "").lower().strip()
        except Exception:
            prov = ""
    html = f"""
<!DOCTYPE html>
<html>
<head><title>Authentication Complete</title></head>
<body>
<script>
  try {{
    if (window.opener) {{
      window.opener.postMessage({{ provider: {prov!r}, code: {code!r} }}, "*");
    }}
  }} catch (e) {{}}
  window.close();
</script>
Authentication complete. You can close this window.
</body>
</html>
"""
    return HTMLResponse(content=html, media_type="text/html")


# Public alias router for non-versioned callback path (/auth/callback)
public_router = APIRouter(prefix="/auth", tags=["host-auth-public"])


@public_router.get("/callback")
async def host_auth_callback_public(provider: str | None = None, code: str = "", state: str | None = None):
    """Public alias for the OAuth callback so environments using /auth/callback work without ingress rewrites."""
    prov = (provider or "").lower().strip()
    if not prov and state:
        try:
            parts = dict(item.split("=", 1) for item in state.split("&") if "=" in item)
            prov = (parts.get("provider") or "").lower().strip()
        except Exception:
            prov = ""
    html = f"""
<!DOCTYPE html>
<html>
<head><title>Authentication Complete</title></head>
<body>
<script>
  try {{
    if (window.opener) {{
      window.opener.postMessage({{ provider: {prov!r}, code: {code!r} }}, "*");
    }}
  }} catch (e) {{}}
  window.close();
</script>
Authentication complete. You can close this window.
</body>
</html>
"""
    return HTMLResponse(content=html, media_type="text/html")
