"""Unit tests for the child-side RPC client."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

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

_host_exc_mod = _load_module("shu.plugins.host.exceptions", _HOST_DIR / "exceptions.py")
_rpc_mod = _load_module("shu.plugins.sandbox.rpc", _SANDBOX_DIR / "rpc.py")
_exc_mod = _load_module("shu.plugins.sandbox.exceptions", _SANDBOX_DIR / "exceptions.py")
_client_mod = _load_module("shu.plugins.sandbox.rpc_client", _SANDBOX_DIR / "rpc_client.py")

RpcClient = _client_mod.RpcClient
rpc_connect = _client_mod.connect
ParentMessage = _rpc_mod.ParentMessage
ChildMessage = _rpc_mod.ChildMessage
read_frame = _rpc_mod.read_frame
write_frame = _rpc_mod.write_frame
MSG_CALL = _rpc_mod.MSG_CALL
serialize_exc = _exc_mod.serialize_exc
PluginError = _exc_mod.PluginError
HttpRequestFailed = _host_exc_mod.HttpRequestFailed
CapabilityDenied = _host_exc_mod.CapabilityDenied


async def _fake_parent(
    uds_path: str,
    handshake_payload: dict,
    responses: list[dict],
):
    """Start a fake parent server that sends a handshake then responds to calls.

    *responses* is a list of pre-built frames (ParentMessage.result/error)
    sent in order for each MSG_CALL received.
    """
    response_iter = iter(responses)
    ready_event = asyncio.Event()
    calls_received: list[dict] = []

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        # Send handshake
        await write_frame(writer, ParentMessage.handshake(handshake_payload))
        ready_event.set()

        # Handle calls
        try:
            while True:
                frame = await read_frame(reader)
                if frame.get("type") == MSG_CALL:
                    calls_received.append(frame)
                    resp = next(response_iter, None)
                    if resp is not None:
                        await write_frame(writer, resp)
        except (asyncio.IncompleteReadError, ConnectionError, OSError, GeneratorExit):
            pass
        finally:
            try:
                writer.close()
            except RuntimeError:
                pass

    server = await asyncio.start_unix_server(handler, path=uds_path)
    return server, ready_event, calls_received


@pytest.fixture
def uds_path() -> str:
    # AF_UNIX path limit is ~104 bytes on macOS; pytest's tmp_path is too long.
    path = tempfile.mktemp(prefix="shu-test-", suffix=".sock", dir="/tmp")
    yield path
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


class TestRpcClientConnect:
    @pytest.mark.asyncio
    async def test_connect_and_read_handshake(self, uds_path: str):
        payload = {"plugin_module": "my.plugin", "user_id": "u1"}
        server, ready, _ = await _fake_parent(uds_path, payload, [])
        try:
            client = await rpc_connect(uds_path)
            hs = await client.read_handshake()
            assert hs == payload
            await client.close()
        finally:
            server.close()
            await server.wait_closed()


class TestRpcClientCall:
    @pytest.mark.asyncio
    async def test_call_returns_result(self, uds_path: str):
        responses = [ParentMessage.result(id=1, value={"status_code": 200, "body": "ok"})]
        server, ready, calls = await _fake_parent(uds_path, {}, responses)
        try:
            client = await rpc_connect(uds_path)
            await client.read_handshake()
            await client.start_reader()
            result = await client.call("http", "fetch", ["https://test.com"], {})
            assert result == {"status_code": 200, "body": "ok"}
            assert len(calls) == 1
            assert calls[0]["cap"] == "http"
            assert calls[0]["method"] == "fetch"
            assert calls[0]["args"] == ["https://test.com"]
            await client.close()
        finally:
            server.close()
            await server.wait_closed()

    @pytest.mark.asyncio
    async def test_call_raises_on_error(self, uds_path: str):
        exc = HttpRequestFailed(404, "https://test.com/missing", body=None, headers={})
        exc_payload = serialize_exc(exc)
        responses = [ParentMessage.error(id=1, exc_payload=exc_payload)]
        server, ready, _ = await _fake_parent(uds_path, {}, responses)
        try:
            client = await rpc_connect(uds_path)
            await client.read_handshake()
            await client.start_reader()
            with pytest.raises(HttpRequestFailed) as exc_info:
                await client.call("http", "fetch", ["https://test.com/missing"], {})
            assert exc_info.value.status_code == 404
            await client.close()
        finally:
            server.close()
            await server.wait_closed()

    @pytest.mark.asyncio
    async def test_call_raises_capability_denied(self, uds_path: str):
        exc = CapabilityDenied("secrets")
        exc_payload = serialize_exc(exc)
        responses = [ParentMessage.error(id=1, exc_payload=exc_payload)]
        server, ready, _ = await _fake_parent(uds_path, {}, responses)
        try:
            client = await rpc_connect(uds_path)
            await client.read_handshake()
            await client.start_reader()
            with pytest.raises(CapabilityDenied) as exc_info:
                await client.call("secrets", "get", ["key"], {})
            assert exc_info.value.capability == "secrets"
            await client.close()
        finally:
            server.close()
            await server.wait_closed()

    @pytest.mark.asyncio
    async def test_sequential_calls_get_correct_ids(self, uds_path: str):
        responses = [
            ParentMessage.result(id=1, value="first"),
            ParentMessage.result(id=2, value="second"),
        ]
        server, ready, calls = await _fake_parent(uds_path, {}, responses)
        try:
            client = await rpc_connect(uds_path)
            await client.read_handshake()
            await client.start_reader()
            r1 = await client.call("http", "fetch", ["a"], {})
            r2 = await client.call("http", "fetch", ["b"], {})
            assert r1 == "first"
            assert r2 == "second"
            assert calls[0]["id"] == 1
            assert calls[1]["id"] == 2
            await client.close()
        finally:
            server.close()
            await server.wait_closed()


class TestRpcClientDisconnect:
    @pytest.mark.asyncio
    async def test_parent_disconnect_rejects_pending_calls(self, uds_path: str):
        """When the parent closes the connection, pending calls should fail."""
        parent_writer_ref: list[asyncio.StreamWriter] = []
        connected = asyncio.Event()

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            await write_frame(writer, ParentMessage.handshake({}))
            parent_writer_ref.append(writer)
            connected.set()
            # Keep handler alive until writer is closed externally
            try:
                await reader.read(1)
            except (ConnectionError, OSError):
                pass

        server = await asyncio.start_unix_server(handler, path=uds_path)
        try:
            client = await rpc_connect(uds_path)
            await client.read_handshake()
            await client.start_reader()
            await connected.wait()

            call_task = asyncio.create_task(
                client.call("http", "fetch", ["url"], {})
            )
            await asyncio.sleep(0.05)

            # Simulate parent crash by closing the writer
            parent_writer_ref[0].close()
            await parent_writer_ref[0].wait_closed()

            with pytest.raises((ConnectionError, OSError)):
                await asyncio.wait_for(call_task, timeout=2.0)
        finally:
            await client.close()
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
