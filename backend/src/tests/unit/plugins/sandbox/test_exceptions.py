"""Round-trip tests for the sandbox exception serialization contract.

Import strategy: load the real ``shu.plugins.host.exceptions`` module
directly from its file path, bypassing ``shu.plugins.host.__init__`` which
pulls in the full services layer and circularly re-imports back into the
plugin executor. Mirrors the pattern used in
``test_kb_capability.py``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


_HOST_DIR = Path(__file__).resolve().parents[4] / "shu" / "plugins" / "host"
_SANDBOX_DIR = Path(__file__).resolve().parents[4] / "shu" / "plugins" / "sandbox"


def _load_module(module_name: str, file_path: Path):
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {module_name!r} from {file_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# Stub the package containers so the child modules can be registered without
# triggering their real __init__.
if "shu" not in sys.modules:
    sys.modules["shu"] = MagicMock()
if "shu.plugins" not in sys.modules:
    sys.modules["shu.plugins"] = MagicMock()
if "shu.plugins.host" not in sys.modules:
    sys.modules["shu.plugins.host"] = MagicMock()
if "shu.plugins.sandbox" not in sys.modules:
    sys.modules["shu.plugins.sandbox"] = MagicMock()

_exceptions_mod = _load_module(
    "shu.plugins.host.exceptions", _HOST_DIR / "exceptions.py",
)
_sandbox_exc_mod = _load_module(
    "shu.plugins.sandbox.exceptions", _SANDBOX_DIR / "exceptions.py",
)

CapabilityDenied = _exceptions_mod.CapabilityDenied
EgressDenied = _exceptions_mod.EgressDenied
HttpRequestFailed = _exceptions_mod.HttpRequestFailed

SERIALIZABLE = _sandbox_exc_mod.SERIALIZABLE
PluginError = _sandbox_exc_mod.PluginError
serialize_exc = _sandbox_exc_mod.serialize_exc
deserialize_exc = _sandbox_exc_mod.deserialize_exc


class TestRegistry:
    def test_known_types_registered(self):
        assert set(SERIALIZABLE) == {
            "CapabilityDenied",
            "HttpRequestFailed",
            "EgressDenied",
            "ValueError",
            "TypeError",
        }
        assert SERIALIZABLE["CapabilityDenied"] is CapabilityDenied
        assert SERIALIZABLE["HttpRequestFailed"] is HttpRequestFailed
        assert SERIALIZABLE["EgressDenied"] is EgressDenied
        assert SERIALIZABLE["ValueError"] is ValueError
        assert SERIALIZABLE["TypeError"] is TypeError


class TestHttpRequestFailedRoundTrip:
    def test_all_ctor_args_preserved(self):
        try:
            raise HttpRequestFailed(
                404,
                "https://x.test/y",
                body={"error": {"message": "not found"}},
                headers={"Retry-After": "5", "X-Req-Id": "abc"},
            )
        except HttpRequestFailed as e:
            payload = serialize_exc(e)

        assert payload["exc_type"] == "HttpRequestFailed"
        assert payload["payload"] == {
            "status_code": 404,
            "url": "https://x.test/y",
            "body": {"error": {"message": "not found"}},
            "headers": {"Retry-After": "5", "X-Req-Id": "abc"},
        }
        assert "Traceback" in payload["traceback"]

        r = deserialize_exc(payload)
        assert isinstance(r, HttpRequestFailed)
        assert r.status_code == 404
        assert r.url == "https://x.test/y"
        assert r.body == {"error": {"message": "not found"}}
        assert r.headers == {"Retry-After": "5", "X-Req-Id": "abc"}

    def test_derived_properties_work_after_round_trip(self):
        """@property values on HttpRequestFailed derive from ctor args, so
        they must keep working after reconstruction."""
        try:
            raise HttpRequestFailed(429, "https://x.test/", body=None, headers={"Retry-After": "30"})
        except HttpRequestFailed as e:
            r = deserialize_exc(serialize_exc(e))
        assert r.error_category == "rate_limited"
        assert r.is_retryable is True
        assert r.retry_after_seconds == 30

    def test_none_body_and_headers(self):
        try:
            raise HttpRequestFailed(500, "https://x.test/", body=None, headers=None)
        except HttpRequestFailed as e:
            r = deserialize_exc(serialize_exc(e))
        assert r.body is None
        assert r.headers == {}


class TestCapabilityDeniedRoundTrip:
    def test_capability_name_preserved(self):
        try:
            raise CapabilityDenied("http")
        except CapabilityDenied as e:
            payload = serialize_exc(e)

        assert payload["exc_type"] == "CapabilityDenied"
        assert payload["payload"] == {"capability": "http"}

        r = deserialize_exc(payload)
        assert isinstance(r, CapabilityDenied)
        assert r.capability == "http"
        assert "http" in str(r)


class TestEgressDeniedRoundTrip:
    def test_contextual_message_preserved(self):
        try:
            raise EgressDenied("URL not allowed by policy: https://evil.example")
        except EgressDenied as e:
            payload = serialize_exc(e)

        assert payload["exc_type"] == "EgressDenied"
        assert payload["payload"]["message"] == "URL not allowed by policy: https://evil.example"

        r = deserialize_exc(payload)
        assert isinstance(r, EgressDenied)
        assert "evil.example" in str(r)


class TestBuiltinRoundTrip:
    """Covers ValueError / TypeError — caught by plugins (kb_search, outlook_calendar,
    github) and raised by host capabilities (storage, utils); they must round-trip
    as the original type so plugin ``except ValueError`` blocks still fire."""

    def test_value_error_round_trip(self):
        try:
            raise ValueError("bad value")
        except ValueError as e:
            payload = serialize_exc(e)
        assert payload["exc_type"] == "ValueError"
        assert payload["payload"] == {"message": "bad value"}

        r = deserialize_exc(payload)
        assert type(r) is ValueError
        assert str(r) == "bad value"

    def test_type_error_round_trip(self):
        try:
            raise TypeError("bad type")
        except TypeError as e:
            payload = serialize_exc(e)
        assert payload["exc_type"] == "TypeError"

        r = deserialize_exc(payload)
        assert type(r) is TypeError
        assert str(r) == "bad type"

    def test_value_error_subclass_does_not_masquerade(self):
        """A subclass of ValueError should NOT be reconstructed as ValueError —
        it would lose identity. Falls back to PluginError instead."""

        class MyValueError(ValueError):
            pass

        try:
            raise MyValueError("subclass")
        except MyValueError as e:
            payload = serialize_exc(e)
        assert payload["exc_type"] == "PluginError"
        assert payload["payload"]["original_type"] == "MyValueError"

        r = deserialize_exc(payload)
        assert isinstance(r, PluginError)
        assert r.original_type == "MyValueError"


class TestUnknownTypeFallback:
    def test_unknown_type_becomes_plugin_error(self):
        # RuntimeError is not registered (no plugin catches it today),
        # so it must fall back to PluginError with diagnostics preserved.
        try:
            raise RuntimeError("boom")
        except RuntimeError as e:
            payload = serialize_exc(e)

        assert payload["exc_type"] == "PluginError"
        assert payload["payload"]["original_type"] == "RuntimeError"
        assert payload["payload"]["message"] == "boom"
        assert "Traceback" in payload["payload"]["traceback_text"]

        r = deserialize_exc(payload)
        assert isinstance(r, PluginError)
        assert r.message == "boom"
        assert r.original_type == "RuntimeError"
        assert "Traceback" in r.traceback_text

    def test_unknown_exc_type_on_deserialize_falls_back(self):
        """If a future parent sends an exc_type the child doesn't know
        about (e.g. a new capability exception during a rolling deploy),
        deserialize must not crash."""
        r = deserialize_exc(
            {
                "exc_type": "SomeFutureError",
                "payload": {"message": "huh"},
                "traceback": "tb-text",
            },
        )
        assert isinstance(r, PluginError)
        assert r.message == "huh"
        assert r.original_type == "SomeFutureError"
        assert r.traceback_text == "tb-text"

    def test_empty_payload_deserialize(self):
        r = deserialize_exc({})
        assert isinstance(r, PluginError)
        assert r.original_type == "PluginError"


class TestPluginError:
    def test_attributes(self):
        e = PluginError("msg", "tb", "TypeX")
        assert e.message == "msg"
        assert e.traceback_text == "tb"
        assert e.original_type == "TypeX"
        assert str(e) == "msg"
        assert isinstance(e, Exception)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
