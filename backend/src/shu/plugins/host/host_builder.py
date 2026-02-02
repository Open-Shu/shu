from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .auth_capability import AuthCapability
from .cache_capability import CacheCapability
from .cursor_capability import CursorCapability
from .exceptions import CapabilityDenied
from .http_capability import HttpCapability
from .identity_capability import IdentityCapability
from .kb_capability import KbCapability
from .ocr_capability import OcrCapability
from .secrets_capability import SecretsCapability
from .storage_capability import StorageCapability


@dataclass
class HostContext:
    auth: dict[str, Any]
    schedule_id: str | None
    ocr_mode: str | None


def parse_host_context(host_context: dict[str, Any] | None) -> HostContext:
    ctx = host_context or {}
    auth_ctx = {}
    try:
        auth_ctx = (ctx.get("auth") or {}) if isinstance(ctx, dict) else {}
    except Exception:
        auth_ctx = {}
    # exec.schedule_id
    schedule_id: str | None = None
    try:
        exec_ctx = ctx.get("exec") if isinstance(ctx, dict) else None
        if isinstance(exec_ctx, dict):
            sid = exec_ctx.get("schedule_id")
            schedule_id = str(sid) if isinstance(sid, str) and sid else None
    except Exception:
        schedule_id = None
    # ocr.mode
    ocr_mode: str | None = None
    try:
        ocr_ctx = ctx.get("ocr") if isinstance(ctx, dict) else None
        if isinstance(ocr_ctx, dict):
            m = ocr_ctx.get("mode") or ocr_ctx.get("ocr_mode")
            if isinstance(m, str):
                mm = m.strip().lower()
                ocr_mode = mm if mm in {"auto", "always", "never", "fallback"} else None
    except Exception:
        ocr_mode = None
    return HostContext(auth=auth_ctx, schedule_id=schedule_id, ocr_mode=ocr_mode)


class Host:
    """Minimal host object exposing only requested capabilities.

    Security: This class is immutable after construction to prevent plugins
    from replacing capabilities with malicious versions or adding undeclared ones.
    """

    __slots__ = (
        "_declared_caps",
        "_frozen",
        "auth",
        "cache",
        "cursor",
        "http",
        "identity",
        "kb",
        "ocr",
        "secrets",
        "storage",
    )

    def __init__(self, declared_caps: list[str] | None = None) -> None:
        object.__setattr__(self, "_declared_caps", set(declared_caps or []))
        object.__setattr__(self, "_frozen", False)
        # Initialize capability slots to None
        for cap in (
            "http",
            "identity",
            "auth",
            "kb",
            "secrets",
            "storage",
            "cursor",
            "cache",
            "ocr",
        ):
            object.__setattr__(self, cap, None)

    def _freeze(self) -> None:
        """Mark the host as frozen after capability setup."""
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError("Host attributes are immutable after construction")
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        raise AttributeError("Host attributes cannot be deleted")

    # Capability names that require declaration before access
    _CAP_NAMES = frozenset(("http", "identity", "auth", "kb", "secrets", "storage", "cursor", "cache", "ocr"))

    def __getattribute__(self, name: str) -> Any:
        # For capability attributes, check if declared before returning
        if name in Host._CAP_NAMES:
            declared = object.__getattribute__(self, "_declared_caps")
            if name not in declared:
                raise CapabilityDenied(name)
        return object.__getattribute__(self, name)


def make_host(
    *,
    plugin_name: str,
    user_id: str,
    user_email: str | None,
    capabilities: list[str] | None = None,
    provider_identities: dict[str, list[dict[str, Any]]] | None = None,
    host_context: dict[str, Any] | None = None,
) -> Host:
    caps = set(capabilities or [])
    # Policy: when kb is declared, auto-include cursor for plugin authors
    if "kb" in caps:
        caps.add("cursor")

    h = Host(declared_caps=list(caps))
    parsed = parse_host_context(host_context)

    http_cap: HttpCapability | None = None
    if "http" in caps:
        http_cap = HttpCapability(plugin_name=plugin_name, user_id=user_id)
        h.http = http_cap

    if "identity" in caps:
        h.identity = IdentityCapability(user_id=user_id, user_email=user_email, providers=provider_identities or {})

    if "auth" in caps:
        # Build primary email map per provider (best-effort)
        primaries: dict[str, str | None] = {}
        try:
            provs = provider_identities or {}
            for k, lst in provs.items():
                try:
                    first = (lst or [None])[0] or {}
                    email = first.get("primary_email") if isinstance(first, dict) else None
                    primaries[str(k).lower()] = email if (email is None or isinstance(email, str)) else None
                except Exception:
                    primaries[str(k).lower()] = None
        except Exception:
            primaries = {}
        h.auth = AuthCapability(
            plugin_name=plugin_name,
            user_id=user_id,
            http=http_cap,
            context=parsed.auth,
            provider_primary_emails=primaries,
        )

    if "kb" in caps:
        h.kb = KbCapability(
            plugin_name=plugin_name,
            user_id=user_id,
            ocr_mode=parsed.ocr_mode,
            schedule_id=parsed.schedule_id,
        )

    if "secrets" in caps:
        h.secrets = SecretsCapability(plugin_name=plugin_name, user_id=user_id)

    if "storage" in caps:
        h.storage = StorageCapability(plugin_name=plugin_name, user_id=user_id)

    if "cursor" in caps:
        h.cursor = CursorCapability(plugin_name=plugin_name, user_id=user_id, schedule_id=parsed.schedule_id)

    if "ocr" in caps:
        # Treat OCR as a utility without implicit policy; do not re-parse host overlay
        h.ocr = OcrCapability(plugin_name=plugin_name, user_id=user_id, ocr_mode=None)

    if "cache" in caps:
        h.cache = CacheCapability(plugin_name=plugin_name, user_id=user_id)

    # Freeze the host to prevent plugins from modifying capabilities
    h._freeze()
    return h
