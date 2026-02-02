from __future__ import annotations

import logging
import urllib.parse
from typing import Any

from ...core.config import get_settings_instance
from ...core.http_client import get_http_client
from .base import ImmutableCapabilityMixin
from .exceptions import EgressDenied, HttpRequestFailed

logger = logging.getLogger(__name__)


def _is_allowed_url(url: str, allowlist: list[str] | None) -> bool:
    if not allowlist:
        return True
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return False
    host = host.lower()
    for pat in allowlist:
        pat = (pat or "").lower().strip()
        if not pat:
            continue
        # simple suffix match for domains, exact for hosts
        if host == pat or (pat.startswith(".") and host.endswith(pat)):
            return True
    return False


class HttpCapability(ImmutableCapabilityMixin):
    """HTTP client for plugins with egress policy enforcement.

    Security: This class is immutable (via ImmutableCapabilityMixin) to prevent
    plugins from mutating _plugin_name or _user_id to bypass audit logging or allowlist checks.
    """

    __slots__ = ("_allowlist", "_default_timeout", "_plugin_name", "_user_id")

    _plugin_name: str
    _user_id: str
    _allowlist: list[str] | None
    _default_timeout: float

    def __init__(self, *, plugin_name: str, user_id: str) -> None:
        s = get_settings_instance()
        object.__setattr__(self, "_plugin_name", plugin_name)
        object.__setattr__(self, "_user_id", user_id)
        # settings may define a simple domain allowlist; empty/None = allow all
        object.__setattr__(self, "_allowlist", getattr(s, "http_egress_allowlist", None))
        object.__setattr__(self, "_default_timeout", float(getattr(s, "http_default_timeout", 30.0)))

    async def fetch(self, method: str, url: str, **kwargs) -> dict[str, Any]:
        if not _is_allowed_url(url, self._allowlist):
            logger.warning(
                "Egress denied by allowlist",
                extra={"plugin": self._plugin_name, "user_id": self._user_id, "url": url},
            )
            raise EgressDenied(f"URL not allowed by policy: {url}")
        timeout = kwargs.pop("timeout", self._default_timeout)
        headers = kwargs.pop("headers", {}) or {}
        # Ensure a basic UA and trace headers
        headers.setdefault("User-Agent", f"Shu-Tool/{self._plugin_name}")
        headers.setdefault("X-Shu-User", self._user_id)
        kwargs["headers"] = headers
        # Build a log URL including query params (if any), and compute a safe auth hash when available
        try:
            params = kwargs.get("params")
            log_url = url
            if params:
                log_url = f"{url}?" + urllib.parse.urlencode(params, doseq=True)
        except Exception:
            log_url = url
        auth_hash = None
        try:
            auth = headers.get("Authorization")
            if isinstance(auth, str) and auth.startswith("Bearer "):
                import hashlib

                auth_hash = hashlib.sha256(auth.split(" ", 1)[1].encode("utf-8")).hexdigest()[:10]
        except Exception:
            auth_hash = None
        # Audit before call
        logger.info(
            "host.http.fetch",
            extra={
                "plugin": self._plugin_name,
                "user_id": self._user_id,
                "method": method,
                "url": log_url,
                "auth_bearer_hash": auth_hash,
            },
        )
        client = await get_http_client()
        import httpx

        resp: httpx.Response = await client.request(method.upper(), url, timeout=timeout, **kwargs)
        content_type = resp.headers.get("content-type", "")
        body: Any
        try:
            body = resp.json() if "json" in content_type else resp.text
        except Exception:
            body = resp.text
        status = int(resp.status_code)
        result = {"status_code": status, "headers": dict(resp.headers), "body": body}
        if status >= 400:
            # Centralize provider HTTP error handling so plugins don't have to
            try:
                logger.warning(
                    "host.http error",
                    extra={
                        "plugin": self._plugin_name,
                        "user_id": self._user_id,
                        "method": method,
                        "url": url,
                        "status": status,
                    },
                )
            except Exception:
                pass
            raise HttpRequestFailed(status, url, body=body, headers=result["headers"])
        return result

    async def fetch_bytes(self, method: str, url: str, **kwargs) -> dict[str, Any]:
        if not _is_allowed_url(url, self._allowlist):
            logger.warning(
                "Egress denied by allowlist",
                extra={"plugin": self._plugin_name, "user_id": self._user_id, "url": url},
            )
            raise EgressDenied(f"URL not allowed by policy: {url}")
        timeout = kwargs.pop("timeout", self._default_timeout)
        headers = kwargs.pop("headers", {}) or {}
        headers.setdefault("User-Agent", f"Shu-Tool/{self._plugin_name}")
        headers.setdefault("X-Shu-User", self._user_id)
        kwargs["headers"] = headers
        logger.info(
            "host.http.fetch_bytes",
            extra={
                "plugin": self._plugin_name,
                "user_id": self._user_id,
                "method": method,
                "url": url,
            },
        )
        client = await get_http_client()
        import httpx

        resp: httpx.Response = await client.request(method.upper(), url, timeout=timeout, **kwargs)
        status = int(resp.status_code)
        result = {"status_code": status, "headers": dict(resp.headers), "content": resp.content}
        if status >= 400:
            try:
                logger.warning(
                    "host.http error (bytes)",
                    extra={
                        "plugin": self._plugin_name,
                        "user_id": self._user_id,
                        "method": method,
                        "url": url,
                        "status": status,
                    },
                )
            except Exception:
                pass
            raise HttpRequestFailed(status, url, body=None, headers=result["headers"])
        return result
