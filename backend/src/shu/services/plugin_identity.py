"""
Shared helpers for resolving provider identities, secrets, and user email for plugin execution paths.

Auth requirements for plugin operations are declared in manifests via:
- ``required_identities``: global list of provider/mode/scopes requirements
- ``op_auth``: per-operation overrides including provider, mode, scopes, subject_hint, and secrets

Secrets in ``op_auth[op].secrets`` are declared as:
```
{
    "key_name": { "allowed_scope": "user" | "system" | "system_or_user" }
}
```
``allowed_scope`` (default: "system_or_user") controls which storage scope satisfies the requirement:
- "user": Must have a user-scoped secret (no fallback to system).
- "system": Must have a system-scoped secret (ignores user secrets).
- "system_or_user": User secret preferred; system secret accepted as fallback.
"""
from __future__ import annotations
from typing import Dict, List, Any, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

# Models are imported lazily inside functions to avoid circulars in some import contexts


async def get_provider_identities_map(db: AsyncSession, user_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """Return provider identities grouped by provider_key for the given user.
    Shape matches what host.identity expects: {provider_key: [identity_dict, ...]}
    """
    try:
        from ..models.provider_identity import ProviderIdentity  # local import
        providers_map: Dict[str, List[Dict[str, Any]]] = {}
        q_pi = select(ProviderIdentity).where(ProviderIdentity.user_id == str(user_id))
        pi_res = await db.execute(q_pi)
        for pi in pi_res.scalars().all():
            providers_map.setdefault(pi.provider_key, []).append(pi.to_dict())
        return providers_map
    except Exception:
        return {}


async def resolve_user_email_for_execution(
    db: AsyncSession,
    user_id: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    allow_impersonate: bool = True,
) -> Optional[str]:
    """Resolve an effective user email for execution context.

    Resolution order:
    1) If allow_impersonate and params indicate domain_delegate with impersonate_email, use that
    2) Otherwise, fallback to User.email from auth models if present
    Returns None if unresolved (plugins may rely on host.auth to resolve target)
    """
    p = dict(params or {})
    try:
        mode = str(p.get("auth_mode") or "").lower()
    except Exception:
        mode = ""
    if allow_impersonate and mode == "domain_delegate":
        imp = p.get("impersonate_email")
        if isinstance(imp, str) and imp.strip():
            return imp.strip()
    # Fallback to User.email
    try:
        from ..auth.models import User  # local import
        r = await db.execute(select(User).where(User.id == str(user_id)))
        u = r.scalars().first()
        if u and getattr(u, "email", None):
            return str(u.email)
    except Exception:
        pass
    return None


async def has_provider_identity(db: AsyncSession, user_id: str, provider_key: str) -> bool:
    """Return True if the given user has at least one ProviderIdentity (or active ProviderCredential) for provider_key."""
    try:
        from ..models.provider_identity import ProviderIdentity
        res = await db.execute(
            select(ProviderIdentity).where(
                (ProviderIdentity.user_id == str(user_id)) & (ProviderIdentity.provider_key == str(provider_key))
            )
        )
        row = res.scalars().first()
        if row:
            return True
    except Exception:
        pass
    # Fallback: check for an active credential even if identity row is missing
    try:
        from ..models.provider_credential import ProviderCredential
        cred_res = await db.execute(
            select(ProviderCredential).where(
                (ProviderCredential.user_id == str(user_id))
                & (ProviderCredential.provider_key == str(provider_key))
                & (ProviderCredential.is_active == True)  # noqa: E712
            )
        )
        cred = cred_res.scalars().first()
        return bool(cred)
    except Exception:
        return False


def resolve_auth_requirements(plugin: Any, params: Optional[Dict[str, Any]] = None) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[List[str]]]:
    """Resolve (provider, mode, subject, scopes) from plugin manifest and params.
    - Prefer plugin._op_auth[op] if present
    - Overlay with params provider/mode/scopes/subject when explicitly provided
    - Do not infer defaults; if not provided, return None for those fields
    - If provider is None, treat as no identity required (caller may interpret as allowed)
    """
    p = dict(params or {})
    op = str(p.get("op") or "").lower()
    provider: Optional[str] = None
    mode: Optional[str] = None
    scopes: Optional[List[str]] = None
    subject: Optional[str] = None
    try:
        op_auth = getattr(plugin, "_op_auth", None)
        if isinstance(op_auth, dict) and op:
            spec = op_auth.get(op) or {}
            provider = (spec.get("provider") or None)
            mode = (spec.get("mode") or None)
            sc = spec.get("scopes")
            if isinstance(sc, list):
                scopes = [str(s) for s in sc]
    except Exception:
        pass
    # Overlay from params (top-level)
    if isinstance(p.get("provider"), str):
        provider = p.get("provider")
    if isinstance(p.get("provider_key"), str):
        provider = p.get("provider_key")
    if isinstance(p.get("auth_mode"), str):
        mode = p.get("auth_mode")
    # subject can be provided as impersonate_email or subject
    subj = p.get("impersonate_email") or p.get("subject")
    if isinstance(subj, str):
        subject = subj
    # scopes can be provided as list in params
    if isinstance(p.get("scopes"), list):
        scopes = [str(s) for s in p.get("scopes")]

    # Overlay from host auth block: params.__host.auth[provider]
    try:
        host = p.get("__host") if isinstance(p.get("__host"), dict) else None
        auth_block = host.get("auth") if host and isinstance(host.get("auth"), dict) else None
        if auth_block:
            # If provider not declared yet and exactly one provider present, adopt it (still not a guess)
            if provider is None and len(list(auth_block.keys())) == 1:
                k = list(auth_block.keys())[0]
                if isinstance(k, str) and k.strip():
                    provider = k
            prov_key = provider
            if prov_key and isinstance(auth_block.get(prov_key), dict):
                a = auth_block.get(prov_key)
                if isinstance(a.get("mode"), str):
                    mode = a.get("mode")
                sub2 = a.get("subject") or a.get("impersonate_email")
                if isinstance(sub2, str):
                    subject = sub2
    except Exception:
        pass

    # Normalize
    provider = (provider or None)
    if isinstance(provider, str):
        provider = provider.strip().lower() or None
    mode = (mode or None)
    if isinstance(mode, str):
        mode = mode.strip().lower() or None
    subject = (subject or None)
    if isinstance(subject, str):
        subject = subject.strip() or None
    return provider, mode, subject, scopes


def resolve_secret_requirements(
    plugin: Any, params: Optional[Dict[str, Any]] = None
) -> Dict[str, str]:
    """Resolve secret requirements from plugin manifest for the given op.

    Returns a dict mapping secret key names to their allowed_scope constraint.
    Example: {"api_key": "system_or_user", "user_token": "user"}

    Allowed scope values:
    - "user": Only user-scoped secrets satisfy the requirement.
    - "system": Only system-scoped secrets satisfy the requirement.
    - "system_or_user": Either scope satisfies (user preferred, system fallback).
    """
    p = dict(params or {})
    op = str(p.get("op") or "").lower()
    if not op:
        return {}
    try:
        op_auth = getattr(plugin, "_op_auth", None)
        if not isinstance(op_auth, dict):
            return {}
        spec = op_auth.get(op)
        if not isinstance(spec, dict):
            return {}
        secrets = spec.get("secrets")
        if not isinstance(secrets, dict):
            return {}
        result: Dict[str, str] = {}
        for key, val in secrets.items():
            if not isinstance(key, str) or not key.strip():
                continue
            allowed = "system_or_user"  # default
            if isinstance(val, dict):
                a = val.get("allowed_scope")
                if isinstance(a, str) and a.strip().lower() in ("user", "system", "system_or_user"):
                    allowed = a.strip().lower()
            elif isinstance(val, str) and val.strip().lower() in ("user", "system", "system_or_user"):
                allowed = val.strip().lower()
            result[key.strip()] = allowed
        return result
    except Exception:
        return {}


async def ensure_secrets_for_plugin(
    plugin: Any,
    plugin_name: str,
    user_id: str,
    params: Optional[Dict[str, Any]] = None,
) -> None:
    """Ensure all declared secrets are available for the plugin op.

    Raises PluginIdentityError if any required secret is missing or does not
    satisfy the declared allowed_scope constraint.
    """
    requirements = resolve_secret_requirements(plugin, params)
    if not requirements:
        return

    from ..plugins.host.secrets_capability import SecretsCapability
    from ..plugins.host._storage_ops import storage_get, storage_get_system

    secrets_cap = SecretsCapability(plugin_name=plugin_name, user_id=user_id)
    missing: List[str] = []

    for key, allowed_scope in requirements.items():
        value = None
        try:
            if allowed_scope == "user":
                # Only user-scoped secrets satisfy
                raw = await storage_get(user_id, plugin_name, secrets_cap.NAMESPACE, key)
                if raw and raw.get("v"):
                    value = raw.get("v")
            elif allowed_scope == "system":
                # Only system-scoped secrets satisfy
                raw = await storage_get_system(plugin_name, secrets_cap.NAMESPACE, key)
                if raw and raw.get("v"):
                    value = raw.get("v")
            else:
                # system_or_user: use capability's fallback logic
                value = await secrets_cap.get(key)
        except Exception as e:
            logger.warning(
                "Failed to retrieve secret during preflight check",
                extra={"plugin": plugin_name, "key": key, "error": str(e)},
            )
            # Treat retrieval errors as missing secrets

        if not value:
            missing.append(key)

    if missing:
        raise PluginIdentityError(
            "missing_secrets",
            f"Plugin '{plugin_name}' requires secrets that are not configured: {', '.join(missing)}. Configure via Plugin Subscriptions.",
            {"plugin": plugin_name, "missing_secrets": missing},
        )


class PluginIdentityError(Exception):
    """Raised when plugin execution is blocked due to missing subscriptions or identities."""

    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


async def ensure_user_identity_for_plugin(
    db: AsyncSession,
    plugin: Any,
    plugin_name: str,
    user_id: str,
    params: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Ensure the current user can execute the plugin op specified in params.
    Raises PluginIdentityError when execution should be blocked.
    """
    provider, mode_eff, _, scopes = resolve_auth_requirements(plugin, params)
    if not provider or (mode_eff or "").lower() != "user":
        return

    provider_key = str(provider).strip().lower()
    # Subscription enforcement (if subscriptions exist for provider)
    try:
        from ..services.host_auth_service import HostAuthService  # local import

        subs = await HostAuthService.list_subscriptions(db, str(user_id), provider_key, None)
        if subs:
            subscribed_names = {s.plugin_name for s in subs}
            if str(plugin_name) not in subscribed_names:
                raise PluginIdentityError(
                    "subscription_required",
                    f"Plugin '{plugin_name}' is not subscribed for provider '{provider_key}'. Manage in Connected Accounts.",
                    {"provider": provider_key, "plugin": plugin_name},
                )
    except PluginIdentityError:
        raise
    except Exception:
        # Non-blocking if subscription lookup fails; caller logs as needed
        pass

    # Identity/scopes enforcement
    try:
        from ..plugins.host.auth_capability import AuthCapability  # local import

        auth = AuthCapability(plugin_name=str(plugin_name), user_id=str(user_id))
        token = await auth.provider_user_token(provider_key, required_scopes=scopes or None)
    except Exception:
        token = None

    if not token:
        raise PluginIdentityError(
            "insufficient_scopes",
            "Connected account is missing the required scopes for this operation. Reconnect via Connected Accounts.",
            {"provider": provider_key, "plugin": plugin_name, "required_scopes": scopes or []},
        )


async def compute_identity_status(db: AsyncSession, owner_user_id: Optional[str], params: Optional[Dict[str, Any]]) -> str:
    """Compute identity status for a feed row using params and host overlay.
    Returns one of: 'no_owner' | 'delegation' | 'delegation_subject_missing' | 'connected' | 'missing_identity' | 'unknown'
    """
    try:
        p = dict(params or {})
        mode = str(p.get("auth_mode") or "").strip().lower()
        provider_key = str(p.get("provider") or p.get("provider_key") or "").strip().lower()
        subj = str(p.get("impersonate_email") or p.get("subject") or "").strip()
        if not mode or not provider_key:
            host_overlay = p.get("__host") if isinstance(p.get("__host"), dict) else None
            auth_overlay = host_overlay.get("auth") if host_overlay and isinstance(host_overlay.get("auth"), dict) else None
            if auth_overlay:
                for k, v in auth_overlay.items():
                    if not provider_key and isinstance(k, str):
                        provider_key = k.strip().lower()
                    if isinstance(v, dict):
                        if not mode and isinstance(v.get("mode"), str):
                            mode = v.get("mode").strip().lower()
                        if not subj and isinstance(v.get("subject") or v.get("impersonate_email"), str):
                            subj = (v.get("subject") or v.get("impersonate_email"))
                    break
        if not owner_user_id:
            return "no_owner" if provider_key else "unknown"
        if mode == "service_account":
            return "delegation"
        if mode == "domain_delegate":
            return "delegation" if subj else "delegation_subject_missing"
        if provider_key:
            has_id = await has_provider_identity(db, str(owner_user_id), provider_key)
            return "connected" if has_id else "missing_identity"
        return "unknown"
    except Exception:
        return "unknown"
