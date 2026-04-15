"""Child-side RPC client for the plugin sandbox.

Owns the UDS connection to the parent.  Provides:

* :meth:`RpcClient.connect` — opens the socket, reads the handshake.
* :meth:`RpcClient.call` — sends a ``MSG_CALL``, awaits the matching
  ``MSG_RESULT`` or ``MSG_ERROR``, returns or raises.

A background reader task dispatches incoming frames to the correct
pending-call future by ``id``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from shu.plugins.sandbox.exceptions import deserialize_exc
from shu.plugins.sandbox.rpc import (
    MSG_ERROR,
    MSG_HANDSHAKE,
    MSG_RESULT,
    ChildMessage,
    read_frame,
    write_frame,
)


class RpcClient:
    """Child-side RPC transport over a Unix domain socket."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._next_id: int = 1
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._reader_task: asyncio.Task[None] | None = None

    async def start_reader(self) -> None:
        """Start the background task that dispatches incoming frames."""
        self._reader_task = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        """Cancel the reader task and close the writer."""
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        self._writer.close()

    async def call(self, cap: str, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        """Send a capability call and wait for the parent's response.

        Returns the deserialized result value on ``MSG_RESULT``.
        Raises the deserialized exception on ``MSG_ERROR``.
        """
        call_id = self._next_id
        self._next_id += 1

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[call_id] = future

        await write_frame(self._writer, ChildMessage.call(call_id, cap, method, args, kwargs))

        try:
            return await future
        finally:
            self._pending.pop(call_id, None)

    async def read_handshake(self) -> dict[str, Any]:
        """Read and return the handshake payload.

        Must be called exactly once, before :meth:`start_reader`.
        """
        frame = await read_frame(self._reader)
        if frame.get("type") != MSG_HANDSHAKE:
            raise RuntimeError(f"Expected handshake, got {frame.get('type')!r}")
        return frame["payload"]

    async def _read_loop(self) -> None:
        """Read frames from the parent and resolve pending futures."""
        try:
            while True:
                frame = await read_frame(self._reader)
                msg_type = frame.get("type")
                if msg_type == MSG_RESULT:
                    self._resolve(frame["id"], frame["value"])
                elif msg_type == MSG_ERROR:
                    self._reject(frame)
                # Other message types (MSG_EXECUTE, etc.) are read
                # directly by the bootstrap, not dispatched here.
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            # Parent disconnected — reject all pending calls.
            for call_id, future in self._pending.items():
                if not future.done():
                    future.set_exception(
                        ConnectionError("Parent disconnected during RPC call")
                    )

    def _resolve(self, call_id: int, value: Any) -> None:
        future = self._pending.get(call_id)
        if future is not None and not future.done():
            future.set_result(value)

    def _reject(self, frame: dict[str, Any]) -> None:
        call_id = frame["id"]
        future = self._pending.get(call_id)
        if future is not None and not future.done():
            exc_payload = {
                "exc_type": frame.get("exc_type", "PluginError"),
                "payload": frame.get("payload", {}),
                "traceback": frame.get("traceback", ""),
            }
            future.set_exception(deserialize_exc(exc_payload))


async def connect(uds_path: str) -> RpcClient:
    """Open a UDS connection and return an :class:`RpcClient`.

    The caller should then call :meth:`RpcClient.read_handshake` to get
    the handshake payload, and :meth:`RpcClient.start_reader` before
    making any :meth:`RpcClient.call` requests.
    """
    reader, writer = await asyncio.open_unix_connection(uds_path)
    return RpcClient(reader, writer)
