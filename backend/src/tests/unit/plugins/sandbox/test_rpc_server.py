"""Unit tests for RpcServer (parent-side RPC message loop)."""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
_server_mod = _load_module("shu.plugins.sandbox.rpc_server", _SANDBOX_DIR / "rpc_server.py")

# rpc_server.py uses structlog-style keyword args (e.g. logger.warning("msg", key=val))
# which stdlib Logger doesn't accept. Replace the module-level logger with a MagicMock
# so these calls succeed in tests without requiring full structlog configuration.
_server_mod.logger = MagicMock()

RpcServer = _server_mod.RpcServer
CapabilityDenied = sys.modules["shu.plugins.host.exceptions"].CapabilityDenied
HttpRequestFailed = sys.modules["shu.plugins.host.exceptions"].HttpRequestFailed
ChildMessage = _rpc_mod.ChildMessage
ParentMessage = _rpc_mod.ParentMessage
MSG_CALL = _rpc_mod.MSG_CALL
MSG_LOG = _rpc_mod.MSG_LOG
MSG_RESULT = _rpc_mod.MSG_RESULT
MSG_ERROR = _rpc_mod.MSG_ERROR
MSG_FINAL_ERROR = _rpc_mod.MSG_FINAL_ERROR
MSG_FINAL_RESULT = _rpc_mod.MSG_FINAL_RESULT
MSG_HANDSHAKE = _rpc_mod.MSG_HANDSHAKE
serialize_exc = _exc_mod.serialize_exc
deserialize_exc = _exc_mod.deserialize_exc
write_frame = _rpc_mod.write_frame
read_frame = _rpc_mod.read_frame
CAP_NAMES = _host_base_mod.CAP_NAMES

_HEADER_FMT = "!I"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)


class _FakeHost:
    """Mimics the real Host's declared-caps gate without importing host_builder.

    Raises CapabilityDenied for any capability name in CAP_NAMES that is not
    in the declared set, exactly as the real Host.__getattribute__ does.
    """

    def __init__(self, declared_caps: set[str], caps: dict[str, object]) -> None:
        object.__setattr__(self, "_declared_caps", declared_caps)
        for name, cap in caps.items():
            object.__setattr__(self, name, cap)

    def __getattribute__(self, name: str) -> object:
        if name in CAP_NAMES:
            declared = object.__getattribute__(self, "_declared_caps")
            if name not in declared:
                raise CapabilityDenied(name)
        return object.__getattribute__(self, name)


def _make_writer() -> tuple[asyncio.StreamWriter, MagicMock]:
    """Build a StreamWriter backed by a MagicMock transport for frame capture."""
    transport = MagicMock()
    transport.is_closing = MagicMock(return_value=False)
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    loop = asyncio.get_event_loop()
    writer = asyncio.StreamWriter(transport, protocol, reader, loop)
    return writer, transport


def _decode_frames(transport: MagicMock) -> list[dict[str, Any]]:
    """Extract all JSON frames written to a mock transport."""
    raw = b"".join(call.args[0] for call in transport.write.call_args_list)
    frames: list[dict[str, Any]] = []
    offset = 0
    while offset < len(raw):
        (length,) = struct.unpack(_HEADER_FMT, raw[offset : offset + _HEADER_SIZE])
        offset += _HEADER_SIZE
        frame = json.loads(raw[offset : offset + length])
        offset += length
        frames.append(frame)
    return frames


def _feed_frames(reader: asyncio.StreamReader, frames: list[dict[str, Any]]) -> None:
    """Encode and feed length-prefixed JSON frames into a StreamReader."""
    for frame in frames:
        data = json.dumps(frame, separators=(",", ":")).encode()
        reader.feed_data(struct.pack(_HEADER_FMT, len(data)) + data)
    reader.feed_eof()


def _make_server(
    declared_caps: set[str] | None = None,
    caps: dict[str, object] | None = None,
    handshake_payload: dict[str, Any] | None = None,
) -> RpcServer:
    """Build an RpcServer with a _FakeHost for testing."""
    host = _FakeHost(
        declared_caps=declared_caps or set(),
        caps=caps or {},
    )
    payload = handshake_payload or {"plugin_module": "test_plugin", "execution_id": "exec-123"}
    return RpcServer(host=host, handshake_payload=payload)


class TestDispatchCall:
    """Tests for RpcServer._dispatch_call."""

    @pytest.mark.asyncio
    async def test_declared_capability_returns_msg_result(self) -> None:
        """MSG_CALL on a declared capability awaits the method and writes MSG_RESULT."""
        fetch_mock = AsyncMock(return_value={"status": 200, "body": "ok"})
        http_cap = MagicMock()
        http_cap.fetch = fetch_mock

        server = _make_server(
            declared_caps={"http"},
            caps={"http": http_cap},
        )
        writer, transport = _make_writer()

        frame = ChildMessage.call(id=1, cap="http", method="fetch", args=["https://example.com"], kwargs={"timeout": 30})
        await server._dispatch_call(frame, writer)

        fetch_mock.assert_awaited_once_with("https://example.com", timeout=30)

        frames = _decode_frames(transport)
        assert len(frames) == 1
        assert frames[0]["type"] == MSG_RESULT
        assert frames[0]["id"] == 1
        assert frames[0]["value"] == {"status": 200, "body": "ok"}

    @pytest.mark.asyncio
    async def test_undeclared_capability_returns_msg_error_with_capability_denied(self) -> None:
        """MSG_CALL on undeclared capability writes MSG_ERROR with CapabilityDenied payload."""
        server = _make_server(declared_caps=set(), caps={})
        writer, transport = _make_writer()

        frame = ChildMessage.call(id=2, cap="secrets", method="get", args=["my_key"], kwargs={})
        await server._dispatch_call(frame, writer)

        frames = _decode_frames(transport)
        assert len(frames) == 1
        error_frame = frames[0]
        assert error_frame["type"] == MSG_ERROR
        assert error_frame["id"] == 2
        assert error_frame["exc_type"] == "CapabilityDenied"
        assert error_frame["payload"]["capability"] == "secrets"

        # Round-trip: deserialize_exc should reconstruct a CapabilityDenied
        exc = deserialize_exc(error_frame)
        assert isinstance(exc, CapabilityDenied)
        assert exc.capability == "secrets"

    @pytest.mark.asyncio
    async def test_method_raising_http_request_failed_returns_msg_error_with_full_attrs(self) -> None:
        """MSG_CALL whose method raises HttpRequestFailed writes MSG_ERROR with all attributes preserved."""
        original_exc = HttpRequestFailed(
            status_code=429,
            url="https://api.example.com/data",
            body={"error": "rate limited"},
            headers={"Retry-After": "60", "X-Request-Id": "abc-123"},
        )
        fetch_mock = AsyncMock(side_effect=original_exc)
        http_cap = MagicMock()
        http_cap.fetch = fetch_mock

        server = _make_server(
            declared_caps={"http"},
            caps={"http": http_cap},
        )
        writer, transport = _make_writer()

        frame = ChildMessage.call(id=3, cap="http", method="fetch", args=["https://api.example.com/data"], kwargs={})
        await server._dispatch_call(frame, writer)

        frames = _decode_frames(transport)
        assert len(frames) == 1
        error_frame = frames[0]
        assert error_frame["type"] == MSG_ERROR
        assert error_frame["id"] == 3
        assert error_frame["exc_type"] == "HttpRequestFailed"
        assert error_frame["payload"]["status_code"] == 429
        assert error_frame["payload"]["url"] == "https://api.example.com/data"
        assert error_frame["payload"]["body"] == {"error": "rate limited"}
        assert error_frame["payload"]["headers"]["Retry-After"] == "60"
        assert error_frame["payload"]["headers"]["X-Request-Id"] == "abc-123"

        # Round-trip: reconstruct and verify all attributes
        exc = deserialize_exc(error_frame)
        assert isinstance(exc, HttpRequestFailed)
        assert exc.status_code == 429
        assert exc.url == "https://api.example.com/data"
        assert exc.body == {"error": "rate limited"}
        assert exc.headers["Retry-After"] == "60"
        assert exc.headers["X-Request-Id"] == "abc-123"


class TestHandleLog:
    """Tests for RpcServer._handle_log."""

    def test_log_frame_routes_to_plugin_logger_with_tags(self) -> None:
        """MSG_LOG reconstructs a LogRecord from JSON fields, tags it, and re-emits."""
        plugin_name = "my_plugin"
        execution_id = "exec-456"
        server = _make_server(
            handshake_payload={"plugin_module": plugin_name, "execution_id": execution_id},
        )

        # Mirrors the dict the child's logging ferry now ships (no pickle).
        log_frame = ChildMessage.log({
            "name": "child.logger",
            "levelno": logging.WARNING,
            "msg": "something went wrong: details",
            "pathname": "plugin.py",
            "lineno": 42,
            "funcName": "do_thing",
            "created": 1_700_000_000.0,
            "exc_text": None,
            "extras": {"request_id": "r-1"},
        })

        mock_logger = MagicMock()
        with patch.object(_server_mod, "get_logger", return_value=mock_logger) as mock_get_logger:
            server._handle_log(log_frame)

        mock_get_logger.assert_called_once_with(f"plugin.{plugin_name}")
        mock_logger.handle.assert_called_once()
        handled_record = mock_logger.handle.call_args[0][0]
        assert handled_record.plugin_name == plugin_name
        assert handled_record.execution_id == execution_id
        assert handled_record.levelno == logging.WARNING
        assert handled_record.getMessage() == "something went wrong: details"
        assert handled_record.request_id == "r-1"

    def test_log_frame_with_bad_payload_is_skipped(self) -> None:
        """A malformed record dict does not break the parent message loop."""
        server = _make_server(handshake_payload={"plugin_module": "p"})
        # Missing required keys like ``name`` and ``levelno`` — rebuild should
        # raise, be caught, and the frame skipped without raising outward.
        log_frame = ChildMessage.log({"garbage": True})

        mock_logger = MagicMock()
        with patch.object(_server_mod, "get_logger", return_value=mock_logger) as mock_get_logger:
            server._handle_log(log_frame)

        mock_get_logger.assert_not_called()
        mock_logger.handle.assert_not_called()


class TestServeOn:
    """Tests for the serve_on message loop, focused on MSG_FINAL_ERROR round-trip."""

    @pytest.mark.asyncio
    async def test_msg_final_error_raises_deserialized_http_request_failed(self) -> None:
        """MSG_FINAL_ERROR with HttpRequestFailed payload raises with full attribute set.

        Regression test for Fix B wire-format change: the frame uses
        ``payload`` (not ``exc_args``), matching ChildMessage.final_error's
        actual output. If someone reverts that rename, this test fails.
        """
        original_exc = HttpRequestFailed(
            status_code=502,
            url="https://api.example.com/webhook",
            body="Bad Gateway",
            headers={"Content-Type": "text/plain"},
        )
        serialized = serialize_exc(original_exc)
        final_error_frame = ChildMessage.final_error(
            exc_type=serialized["exc_type"],
            payload=serialized["payload"],
            traceback_text=serialized["traceback"],
        )

        server = _make_server()
        reader = asyncio.StreamReader()
        writer, _ = _make_writer()

        _feed_frames(reader, [final_error_frame])

        with pytest.raises(HttpRequestFailed) as exc_info:
            await server.serve_on("fake.sock", reader, writer)

        exc = exc_info.value
        assert exc.status_code == 502
        assert exc.url == "https://api.example.com/webhook"
        assert exc.body == "Bad Gateway"
        assert exc.headers["Content-Type"] == "text/plain"

    @pytest.mark.asyncio
    async def test_msg_final_error_raises_deserialized_capability_denied(self) -> None:
        """MSG_FINAL_ERROR with CapabilityDenied payload round-trips correctly."""
        original_exc = CapabilityDenied("storage")
        serialized = serialize_exc(original_exc)
        final_error_frame = ChildMessage.final_error(
            exc_type=serialized["exc_type"],
            payload=serialized["payload"],
            traceback_text=serialized["traceback"],
        )

        server = _make_server()
        reader = asyncio.StreamReader()
        writer, _ = _make_writer()

        _feed_frames(reader, [final_error_frame])

        with pytest.raises(CapabilityDenied) as exc_info:
            await server.serve_on("fake.sock", reader, writer)

        assert exc_info.value.capability == "storage"

    @pytest.mark.asyncio
    async def test_msg_final_result_returns_value(self) -> None:
        """MSG_FINAL_RESULT causes serve_on to return the value dict."""
        server = _make_server()
        reader = asyncio.StreamReader()
        writer, _ = _make_writer()

        result_frame = ChildMessage.final_result({"experiences": [1, 2, 3]})
        _feed_frames(reader, [result_frame])

        result = await server.serve_on("fake.sock", reader, writer)

        assert result == {"experiences": [1, 2, 3]}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
