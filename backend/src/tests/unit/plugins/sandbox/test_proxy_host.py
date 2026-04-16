"""Unit tests for ProxyHost and ProxyCapability."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from _module_loader import load_module as _load_module

_SANDBOX_DIR = Path(__file__).resolve().parents[4] / "shu" / "plugins" / "sandbox"
_HOST_DIR = Path(__file__).resolve().parents[4] / "shu" / "plugins" / "host"


if "shu" not in sys.modules:
    sys.modules["shu"] = MagicMock()
if "shu.plugins" not in sys.modules:
    sys.modules["shu.plugins"] = MagicMock()
if "shu.plugins.host" not in sys.modules:
    sys.modules["shu.plugins.host"] = MagicMock()
if "shu.plugins.sandbox" not in sys.modules:
    sys.modules["shu.plugins.sandbox"] = MagicMock()

_host_base_mod = _load_module("shu.plugins.host.base", _HOST_DIR / "base.py")
_host_exc_mod = _load_module("shu.plugins.host.exceptions", _HOST_DIR / "exceptions.py")
_identity_mod = _load_module("shu.plugins.host.identity_capability", _HOST_DIR / "identity_capability.py")
_log_mod = _load_module("shu.plugins.host.log_capability", _HOST_DIR / "log_capability.py")
_utils_mod = _load_module("shu.plugins.host.utils_capability", _HOST_DIR / "utils_capability.py")
_rpc_mod = _load_module("shu.plugins.sandbox.rpc", _SANDBOX_DIR / "rpc.py")
_exc_mod = _load_module("shu.plugins.sandbox.exceptions", _SANDBOX_DIR / "exceptions.py")
_client_mod = _load_module("shu.plugins.sandbox.rpc_client", _SANDBOX_DIR / "rpc_client.py")
_proxy_mod = _load_module("shu.plugins.sandbox.proxy_host", _SANDBOX_DIR / "proxy_host.py")

ProxyHost = _proxy_mod.ProxyHost
ProxyCapability = _proxy_mod.ProxyCapability
# Get CapabilityDenied from the same module object proxy_host.py imported,
# so pytest.raises matches even when test ordering loads the class twice.
CapabilityDenied = sys.modules["shu.plugins.host.exceptions"].CapabilityDenied
IdentityCapability = _identity_mod.IdentityCapability
LogCapability = _log_mod.LogCapability
UtilsCapability = _utils_mod.UtilsCapability


def _make_mock_client() -> MagicMock:
    client = MagicMock()
    client.call = AsyncMock()
    return client


def _make_proxy_host(
    declared_caps: set[str] | None = None,
    client: MagicMock | None = None,
    identity: object | None = None,
    log: object | None = None,
    utils: object | None = None,
) -> ProxyHost:
    return ProxyHost(
        client=client or _make_mock_client(),
        declared_caps=declared_caps or {"http", "kb"},
        identity=identity or MagicMock(user_id="u1", user_email="u@test.com"),
        log=log or MagicMock(),
        utils=utils or MagicMock(),
    )


class TestProxyCapability:
    @pytest.mark.asyncio
    async def test_getattr_returns_callable_that_calls_client(self):
        client = _make_mock_client()
        client.call.return_value = {"status_code": 200}
        proxy = ProxyCapability(client, "http")

        result = await proxy.fetch("https://test.com", timeout=5)

        client.call.assert_awaited_once_with("http", "fetch", ["https://test.com"], {"timeout": 5})
        assert result == {"status_code": 200}

    @pytest.mark.asyncio
    async def test_different_methods_dispatch_correctly(self):
        client = _make_mock_client()
        client.call.side_effect = [b"bytes_data", {"doc_id": "d1"}]
        proxy = ProxyCapability(client, "http")

        await proxy.fetch_bytes("https://test.com/file")
        await proxy.post("https://test.com/api", body={"k": "v"})

        assert client.call.await_count == 2
        assert client.call.await_args_list[0].args == ("http", "fetch_bytes", ["https://test.com/file"], {})
        assert client.call.await_args_list[1].args == ("http", "post", ["https://test.com/api"], {"body": {"k": "v"}})


class TestProxyHostDeclaredCaps:
    def test_declared_cap_returns_proxy_capability(self):
        host = _make_proxy_host(declared_caps={"http", "kb"})
        assert isinstance(host.http, ProxyCapability)
        assert isinstance(host.kb, ProxyCapability)

    def test_proxy_capability_is_cached(self):
        host = _make_proxy_host(declared_caps={"http"})
        cap1 = host.http
        cap2 = host.http
        assert cap1 is cap2

    def test_undeclared_cap_raises_capability_denied(self):
        host = _make_proxy_host(declared_caps={"http"})
        with pytest.raises(Exception, match="not declared in plugin manifest"):
            _ = host.secrets

    def test_undeclared_cap_error_message_includes_name(self):
        host = _make_proxy_host(declared_caps=set())
        with pytest.raises(Exception, match="not declared in plugin manifest") as exc_info:
            _ = host.auth
        assert exc_info.value.capability == "auth"


class TestProxyHostLocalCaps:
    def test_identity_returns_local_object(self):
        identity = MagicMock(user_id="u1", user_email="u@test.com")
        host = _make_proxy_host(identity=identity)
        assert host.identity is identity

    def test_log_returns_local_object(self):
        log = MagicMock()
        host = _make_proxy_host(log=log)
        assert host.log is log

    def test_utils_returns_local_object(self):
        utils = MagicMock()
        host = _make_proxy_host(utils=utils)
        assert host.utils is utils

    def test_identity_available_even_if_not_in_declared_caps(self):
        """identity is passed by value at handshake — always local."""
        host = _make_proxy_host(declared_caps=set(), identity=MagicMock(user_id="u1"))
        assert host.identity.user_id == "u1"

    def test_log_available_even_if_not_in_declared_caps(self):
        host = _make_proxy_host(declared_caps=set())
        assert host.log is not None

    def test_utils_available_even_if_not_in_declared_caps(self):
        host = _make_proxy_host(declared_caps=set())
        assert host.utils is not None


class TestProxyHostImmutability:
    def test_setattr_raises(self):
        host = _make_proxy_host()
        with pytest.raises(AttributeError, match="immutable"):
            host.http = "something"

    def test_delattr_raises(self):
        host = _make_proxy_host()
        with pytest.raises(AttributeError, match="cannot be deleted"):
            del host.http


class TestProxyHostEndToEnd:
    @pytest.mark.asyncio
    async def test_proxy_http_fetch(self):
        """Simulates ``host.http.fetch(url)`` from plugin code."""
        client = _make_mock_client()
        client.call.return_value = {"status_code": 200, "body": "ok"}
        host = _make_proxy_host(declared_caps={"http"}, client=client)

        result = await host.http.fetch("https://test.com")

        client.call.assert_awaited_once_with("http", "fetch", ["https://test.com"], {})
        assert result == {"status_code": 200, "body": "ok"}


class TestRealIdentityCapability:
    def test_identity_is_real_frozen_dataclass(self):
        identity = IdentityCapability(user_id="u1", user_email="u@test.com", providers={"google": []})
        host = _make_proxy_host(identity=identity)
        assert host.identity is identity
        assert host.identity.user_id == "u1"
        assert host.identity.user_email == "u@test.com"
        assert host.identity.get_current_user_identity() == {"user_id": "u1", "email": "u@test.com"}


class TestRealLogCapability:
    def test_log_info_reaches_stdlib_logging(self, caplog):
        log = LogCapability(plugin_name="test_plug", user_id="u1")
        host = _make_proxy_host(log=log)
        with caplog.at_level(logging.INFO, logger="shu.plugins.runtime"):
            host.log.info("hello from plugin")
        assert any("hello from plugin" in r.message for r in caplog.records)

    def test_log_error_reaches_stdlib_logging(self, caplog):
        log = LogCapability(plugin_name="test_plug", user_id="u1")
        host = _make_proxy_host(log=log)
        with caplog.at_level(logging.ERROR, logger="shu.plugins.runtime"):
            host.log.error("something broke")
        assert any("something broke" in r.message for r in caplog.records)


class TestRealUtilsCapability:
    @pytest.mark.asyncio
    async def test_map_safe_runs_real_method(self):
        utils = UtilsCapability(plugin_name="test_plug", user_id="u1")
        host = _make_proxy_host(utils=utils)

        async def double(x: int) -> int:
            return x * 2

        results, errors = await host.utils.map_safe([1, 2, 3], double)
        assert results == [2, 4, 6]
        assert errors == []

    @pytest.mark.asyncio
    async def test_map_safe_collects_errors(self):
        utils = UtilsCapability(plugin_name="test_plug", user_id="u1")
        host = _make_proxy_host(utils=utils)

        async def fail_on_two(x: int) -> int:
            if x == 2:
                raise ValueError("no twos")
            return x * 2

        results, errors = await host.utils.map_safe([1, 2, 3], fail_on_two)
        assert results == [2, 6]
        assert len(errors) == 1
        assert errors[0][0] == 2
        assert isinstance(errors[0][1], ValueError)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
