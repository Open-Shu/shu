"""Child-side stand-in for :class:`~shu.plugins.host.host_builder.Host`.

``ProxyHost`` mirrors the real Host's attribute-access API so plugin code
can call ``host.http.fetch(...)`` without knowing it's in a sandbox.

* **Locally constructed** (no RPC): ``identity`` (frozen dataclass,
  reconstructed from handshake values), ``log`` (always available),
  ``utils`` (always available, stateless helpers).
* **RPC-proxied**: every other declared capability.  ``ProxyCapability``
  turns attribute access on the capability object into
  ``client.call(cap, method, args, kwargs)`` RPC calls to the parent.
* **Denied**: undeclared capabilities raise ``CapabilityDenied`` on
  access — fast-fail mirror of the parent-side check so the common
  denial doesn't cost an IPC round-trip.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from shu.plugins.host.base import CAP_NAMES
from shu.plugins.host.exceptions import CapabilityDenied
from shu.plugins.sandbox.rpc_client import RpcClient


class ProxyCapability:
    """Turns attribute access into RPC calls to the parent."""

    def __init__(self, client: RpcClient, cap_name: str) -> None:
        self._client = client
        self._cap_name = cap_name

    def __getattr__(self, method_name: str) -> Callable[..., Awaitable[Any]]:
        async def _call(*args: Any, **kwargs: Any) -> Any:
            return await self._client.call(
                self._cap_name, method_name, list(args), kwargs,
            )
        _call.__name__ = f"{self._cap_name}.{method_name}"
        _call.__qualname__ = f"ProxyCapability({self._cap_name!r}).{method_name}"
        return _call


class ProxyHost:
    """Child-side stand-in for ``Host``.

    Args:
        client: The :class:`RpcClient` connected to the parent.
        declared_caps: Effective capability set from the handshake
            (post auto-add, e.g. ``kb`` implies ``cursor``).
        identity: Locally reconstructed ``IdentityCapability``.
        log: Locally constructed ``LogCapability``.
        utils: Locally constructed ``UtilsCapability``.
    """

    def __init__(
        self,
        *,
        client: RpcClient,
        declared_caps: set[str],
        identity: Any,
        log: Any,
        utils: Any,
    ) -> None:
        object.__setattr__(self, "_client", client)
        object.__setattr__(self, "_declared_caps", declared_caps)
        object.__setattr__(self, "_identity", identity)
        object.__setattr__(self, "_log", log)
        object.__setattr__(self, "_utils", utils)
        object.__setattr__(self, "_proxies", {})

    def __getattribute__(self, name: str) -> Any:
        # Local capabilities — always available, no RPC.
        if name == "identity":
            return object.__getattribute__(self, "_identity")
        if name == "log":
            return object.__getattribute__(self, "_log")
        if name == "utils":
            return object.__getattribute__(self, "_utils")

        # Known capability name — check declaration.
        if name in CAP_NAMES:

            # Fast fail undeclared capabilities
            declared = object.__getattribute__(self, "_declared_caps")
            if name not in declared:
                raise CapabilityDenied(name)

            # Return a cached ProxyCapability / build if not cached
            proxies = object.__getattribute__(self, "_proxies")
            if name not in proxies:
                client = object.__getattribute__(self, "_client")
                proxies[name] = ProxyCapability(client, name)
            return proxies[name]

        # Anything else (private attrs, dunder methods, etc.)
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("ProxyHost attributes are immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("ProxyHost attributes cannot be deleted")
