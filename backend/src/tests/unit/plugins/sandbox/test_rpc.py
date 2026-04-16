"""Unit tests for the length-prefixed JSON frame codec and message builders."""

from __future__ import annotations

import asyncio
import json
import struct
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from _module_loader import load_module as _load_module

_SANDBOX_DIR = Path(__file__).resolve().parents[4] / "shu" / "plugins" / "sandbox"


if "shu" not in sys.modules:
    sys.modules["shu"] = MagicMock()
if "shu.plugins" not in sys.modules:
    sys.modules["shu.plugins"] = MagicMock()
if "shu.plugins.sandbox" not in sys.modules:
    sys.modules["shu.plugins.sandbox"] = MagicMock()

_rpc_mod = _load_module("shu.plugins.sandbox.rpc", _SANDBOX_DIR / "rpc.py")

read_frame = _rpc_mod.read_frame
write_frame = _rpc_mod.write_frame
MAX_FRAME_BYTES = _rpc_mod.MAX_FRAME_BYTES
_HEADER_SIZE = _rpc_mod._HEADER_SIZE

# Message type constants
MSG_HANDSHAKE = _rpc_mod.MSG_HANDSHAKE
MSG_READY = _rpc_mod.MSG_READY
MSG_EXECUTE = _rpc_mod.MSG_EXECUTE
MSG_CALL = _rpc_mod.MSG_CALL
MSG_RESULT = _rpc_mod.MSG_RESULT
MSG_ERROR = _rpc_mod.MSG_ERROR
MSG_FINAL_RESULT = _rpc_mod.MSG_FINAL_RESULT
MSG_FINAL_ERROR = _rpc_mod.MSG_FINAL_ERROR
MSG_LOG = _rpc_mod.MSG_LOG

ParentMessage = _rpc_mod.ParentMessage
ChildMessage = _rpc_mod.ChildMessage


def _encode_frame(payload: dict) -> bytes:
    """Manually build a wire frame for feeding into a StreamReader."""
    data = json.dumps(payload, separators=(",", ":")).encode()
    return struct.pack("!I", len(data)) + data


def _make_reader(raw: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(raw)
    reader.feed_eof()
    return reader


def _make_writer() -> tuple[asyncio.StreamWriter, MagicMock]:
    """Build a StreamWriter that captures written bytes without real I/O."""
    transport = MagicMock()
    transport.is_closing = MagicMock(return_value=False)
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    loop = asyncio.get_event_loop()
    writer = asyncio.StreamWriter(transport, protocol, reader, loop)
    return writer, transport


class TestReadFrame:
    @pytest.mark.asyncio
    async def test_round_trip_simple(self):
        msg = {"type": "handshake", "payload": {"user_id": "u1"}}
        reader = _make_reader(_encode_frame(msg))
        result = await read_frame(reader)
        assert result == msg

    @pytest.mark.asyncio
    async def test_round_trip_nested(self):
        msg = {"type": "result", "id": 1, "value": {"items": [1, 2, 3], "ok": True}}
        reader = _make_reader(_encode_frame(msg))
        assert await read_frame(reader) == msg

    @pytest.mark.asyncio
    async def test_consecutive_frames(self):
        m1 = {"type": "call", "id": 1}
        m2 = {"type": "result", "id": 1, "value": "ok"}
        reader = _make_reader(_encode_frame(m1) + _encode_frame(m2))
        assert await read_frame(reader) == m1
        assert await read_frame(reader) == m2

    @pytest.mark.asyncio
    async def test_oversized_frame_rejected(self):
        header = struct.pack("!I", MAX_FRAME_BYTES + 1)
        reader = _make_reader(header + b"\x00" * 16)
        with pytest.raises(ValueError, match="exceeds maximum"):
            await read_frame(reader)

    @pytest.mark.asyncio
    async def test_eof_during_header_raises(self):
        reader = _make_reader(b"\x00\x00")  # only 2 of 4 header bytes
        with pytest.raises(asyncio.IncompleteReadError):
            await read_frame(reader)

    @pytest.mark.asyncio
    async def test_eof_during_payload_raises(self):
        header = struct.pack("!I", 100)
        reader = _make_reader(header + b"short")
        with pytest.raises(asyncio.IncompleteReadError):
            await read_frame(reader)

    @pytest.mark.asyncio
    async def test_malformed_json_raises(self):
        garbage = b"not valid json {{"
        header = struct.pack("!I", len(garbage))
        reader = _make_reader(header + garbage)
        with pytest.raises(json.JSONDecodeError):
            await read_frame(reader)

    @pytest.mark.asyncio
    async def test_empty_dict(self):
        reader = _make_reader(_encode_frame({}))
        assert await read_frame(reader) == {}


class TestWriteFrame:
    @pytest.mark.asyncio
    async def test_writes_correct_wire_format(self):
        msg = {"type": "ready"}
        expected_body = json.dumps(msg, separators=(",", ":")).encode()
        expected_header = struct.pack("!I", len(expected_body))

        writer, transport = _make_writer()
        await write_frame(writer, msg)

        written = b"".join(call.args[0] for call in transport.write.call_args_list)
        assert written == expected_header + expected_body

    @pytest.mark.asyncio
    async def test_oversized_payload_rejected(self):
        msg = {"big": "x" * (MAX_FRAME_BYTES + 1)}
        writer, _transport = _make_writer()
        with pytest.raises(ValueError, match="exceeds maximum"):
            await write_frame(writer, msg)

    @pytest.mark.asyncio
    async def test_compact_json_no_whitespace(self):
        """write_frame uses compact separators to save bytes on the wire."""
        msg = {"key": "value", "nested": {"a": 1}}
        writer, transport = _make_writer()
        await write_frame(writer, msg)

        written = b"".join(call.args[0] for call in transport.write.call_args_list)
        body = written[_HEADER_SIZE:]
        assert b" " not in body
        assert json.loads(body) == msg


class TestRoundTripViaLoopback:
    """End-to-end: write_frame → raw bytes → read_frame."""

    @pytest.mark.asyncio
    async def test_write_then_read(self):
        msg = {"type": "execute", "vparams": {"query": "hello"}}

        writer, transport = _make_writer()
        await write_frame(writer, msg)

        raw = b"".join(call.args[0] for call in transport.write.call_args_list)
        read_reader = _make_reader(raw)
        assert await read_frame(read_reader) == msg

    @pytest.mark.asyncio
    async def test_every_message_type_round_trips(self):
        """Every builder output survives write_frame → read_frame."""
        messages = [
            ParentMessage.handshake({"plugin_module": "m", "user_id": "u"}),
            ParentMessage.execute({"q": "x"}),
            ParentMessage.result(1, {"data": True}),
            ParentMessage.error(1, {"exc_type": "X", "payload": {}, "traceback": ""}),
            ChildMessage.ready(),
            ChildMessage.call(1, "http", "fetch", ["url"], {}),
            ChildMessage.final_result({"ok": True}),
            ChildMessage.final_error("E", {}, "tb"),
            ChildMessage.log("b64data"),
        ]
        writer, transport = _make_writer()
        for msg in messages:
            await write_frame(writer, msg)

        raw = b"".join(call.args[0] for call in transport.write.call_args_list)
        reader = _make_reader(raw)
        for expected in messages:
            assert await read_frame(reader) == expected


class TestParentMessage:
    def test_handshake(self):
        msg = ParentMessage.handshake({"plugin_module": "my.plugin", "user_id": "u1"})
        assert msg == {
            "type": MSG_HANDSHAKE,
            "payload": {"plugin_module": "my.plugin", "user_id": "u1"},
        }

    def test_execute(self):
        msg = ParentMessage.execute({"query": "hello"})
        assert msg == {"type": MSG_EXECUTE, "vparams": {"query": "hello"}}

    def test_result(self):
        msg = ParentMessage.result(id=1, value={"status_code": 200})
        assert msg == {"type": MSG_RESULT, "id": 1, "value": {"status_code": 200}}

    def test_error(self):
        exc_payload = {
            "exc_type": "HttpRequestFailed",
            "payload": {"status_code": 404, "url": "https://test.com", "body": None, "headers": {}},
            "traceback": "Traceback...",
        }
        msg = ParentMessage.error(id=1, exc_payload=exc_payload)
        assert msg["type"] == MSG_ERROR
        assert msg["id"] == 1
        assert msg["exc_type"] == "HttpRequestFailed"
        assert msg["payload"]["status_code"] == 404
        assert msg["traceback"] == "Traceback..."


class TestChildMessage:
    def test_ready(self):
        assert ChildMessage.ready() == {"type": MSG_READY}

    def test_call(self):
        msg = ChildMessage.call(id=1, cap="http", method="fetch", args=["https://test.com"], kwargs={"timeout": 5})
        assert msg == {
            "type": MSG_CALL,
            "id": 1,
            "cap": "http",
            "method": "fetch",
            "args": ["https://test.com"],
            "kwargs": {"timeout": 5},
        }

    def test_final_result(self):
        msg = ChildMessage.final_result(value={"ok": True, "items": [1, 2]})
        assert msg == {"type": MSG_FINAL_RESULT, "value": {"ok": True, "items": [1, 2]}}

    def test_final_error(self):
        msg = ChildMessage.final_error(
            exc_type="ValueError",
            payload={"message": "bad"},
            traceback_text="Traceback...\nValueError: bad",
        )
        assert msg == {
            "type": MSG_FINAL_ERROR,
            "exc_type": "ValueError",
            "payload": {"message": "bad"},
            "traceback": "Traceback...\nValueError: bad",
        }

    def test_log(self):
        payload = {
            "name": "plugin.x",
            "levelno": 20,
            "msg": "hello",
            "pathname": "p.py",
            "lineno": 1,
            "funcName": "f",
            "created": 123.0,
            "exc_text": None,
            "extras": {"k": "v"},
        }
        msg = ChildMessage.log(payload)
        assert msg == {"type": MSG_LOG, "record": payload}


class TestAllBuildersJsonSerializable:
    def test_all_builders_produce_json_serializable_dicts(self):
        messages = [
            ParentMessage.handshake({"k": "v"}),
            ParentMessage.execute({"q": "x"}),
            ParentMessage.result(1, {"data": [1]}),
            ParentMessage.error(1, {"exc_type": "X", "payload": {}, "traceback": ""}),
            ChildMessage.ready(),
            ChildMessage.call(1, "http", "fetch", ["url"], {}),
            ChildMessage.final_result({"ok": True}),
            ChildMessage.final_error("E", {}, "tb"),
            ChildMessage.log("b64data"),
        ]
        for msg in messages:
            assert isinstance(msg, dict)
            json.dumps(msg)


class TestMessageConstants:
    def test_constants_are_unique_strings(self):
        consts = [
            MSG_HANDSHAKE, MSG_READY, MSG_EXECUTE, MSG_CALL,
            MSG_RESULT, MSG_ERROR, MSG_FINAL_RESULT, MSG_FINAL_ERROR, MSG_LOG,
        ]
        assert all(isinstance(c, str) for c in consts)
        assert len(set(consts)) == len(consts), "Message type constants must be unique"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
