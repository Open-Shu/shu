"""Length-prefixed JSON frame codec and message builders for the sandbox
control channel.

Every message on the Unix domain socket between parent and child is a
length-prefixed JSON frame:

    [4 bytes: big-endian uint32 payload length][N bytes: UTF-8 JSON]

The codec rejects frames larger than :data:`MAX_FRAME_BYTES` (64 MiB).

Message type constants and typed builder functions live here so both
parent and child import the same definitions — single source of truth
for the wire format.
"""

import asyncio
import json
import struct
from typing import Any


MSG_HANDSHAKE: str = "handshake"
MSG_READY: str = "ready"
MSG_EXECUTE: str = "execute"
MSG_CALL: str = "call"
MSG_RESULT: str = "result"
MSG_ERROR: str = "error"
MSG_FINAL_RESULT: str = "final_result"
MSG_FINAL_ERROR: str = "final_error"
MSG_LOG: str = "log"


class ParentMessage:
    """Messages sent by the parent (launcher / RPC dispatcher) to the child."""

    @staticmethod
    def handshake(payload: dict[str, Any]) -> dict[str, Any]:
        """Bootstrap handshake with plugin/env details."""
        return {"type": MSG_HANDSHAKE, "payload": payload}

    @staticmethod
    def execute(vparams: dict[str, Any]) -> dict[str, Any]:
        """Run the plugin with these validated params."""
        return {"type": MSG_EXECUTE, "vparams": vparams}

    @staticmethod
    def result(id: int, value: Any) -> dict[str, Any]:
        """Successful capability return value."""
        return {"type": MSG_RESULT, "id": id, "value": value}

    @staticmethod
    def error(id: int, exc_payload: dict[str, Any]) -> dict[str, Any]:
        """Capability raised an exception.

        *exc_payload* is the output of
        :func:`~shu.plugins.sandbox.exceptions.serialize_exc`.
        """
        return {"type": MSG_ERROR, "id": id, **exc_payload}


class ChildMessage:
    """Messages sent by the child (sandbox bootstrap) to the parent."""

    @staticmethod
    def ready() -> dict[str, Any]:
        """Sandbox lockdown complete, ready for execute."""
        return {"type": MSG_READY}

    @staticmethod
    def call(
        id: int,
        cap: str,
        method: str,
        args: list[Any],
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Invoke a host capability method."""
        return {
            "type": MSG_CALL,
            "id": id,
            "cap": cap,
            "method": method,
            "args": args,
            "kwargs": kwargs,
        }

    @staticmethod
    def final_result(value: Any) -> dict[str, Any]:
        """plugin.execute() returned successfully."""
        return {"type": MSG_FINAL_RESULT, "value": value}

    @staticmethod
    def final_error(
        exc_type: str,
        exc_args: dict[str, Any],
        traceback_text: str,
    ) -> dict[str, Any]:
        """plugin.execute() raised an exception."""
        return {
            "type": MSG_FINAL_ERROR,
            "exc_type": exc_type,
            "exc_args": exc_args,
            "traceback": traceback_text,
        }

    @staticmethod
    def log(record_b64: str) -> dict[str, Any]:
        """Pickled log record (fire-and-forget)."""
        return {"type": MSG_LOG, "record": record_b64}

MAX_FRAME_BYTES: int = 64 * 1024 * 1024  # 64 MiB

_HEADER_FMT = "!I"  # network byte-order (big-endian) unsigned 32-bit int
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)


async def read_frame(reader: asyncio.StreamReader) -> dict[str, Any]:
    """Read one length-prefixed JSON frame from *reader*.

    Raises:
        ConnectionError: The stream closed before a complete frame could
            be read (EOF mid-header or mid-payload).
        ValueError: The frame exceeds :data:`MAX_FRAME_BYTES`.
    """
    header = await reader.readexactly(_HEADER_SIZE)
    (length,) = struct.unpack(_HEADER_FMT, header)
    if length > MAX_FRAME_BYTES:
        raise ValueError(
            f"Frame size {length} exceeds maximum ({MAX_FRAME_BYTES})"
        )
    data = await reader.readexactly(length)
    return json.loads(data)


async def write_frame(writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
    """Write one length-prefixed JSON frame to *writer*.

    Raises:
        ValueError: The serialized payload exceeds :data:`MAX_FRAME_BYTES`.
    """
    data = json.dumps(payload, separators=(",", ":")).encode()
    if len(data) > MAX_FRAME_BYTES:
        raise ValueError(
            f"Frame size {len(data)} exceeds maximum ({MAX_FRAME_BYTES})"
        )
    writer.write(struct.pack(_HEADER_FMT, len(data)) + data)
    await writer.drain()
