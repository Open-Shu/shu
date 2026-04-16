"""Parent-side RPC server for the plugin sandbox.

Counterpart to :class:`shu.plugins.sandbox.rpc_client.RpcClient`.
Owns the parent end of the UDS connection and drives the message loop:

* Sends ``MSG_HANDSHAKE`` on entry.
* Reads child frames in a loop, dispatching ``MSG_CALL`` and ``MSG_LOG``.
* Returns the plugin result dict on ``MSG_FINAL_RESULT`` or raises the
  deserialized exception on ``MSG_FINAL_ERROR``.

The :meth:`send_execute` method is deliberately separate from
:meth:`serve_on` so the launcher (task 23) can control exactly when
``MSG_EXECUTE`` is sent — typically after observing ``MSG_READY`` or
performing other pre-execution setup.  :meth:`serve_on` does **not**
send ``MSG_EXECUTE``; the launcher calls :meth:`send_execute` at the
appropriate point while the serve loop is running in a concurrent task.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from shu.core.logging import get_logger
from shu.plugins.sandbox.exceptions import deserialize_exc, serialize_exc

if TYPE_CHECKING:
    from shu.plugins.host.host_builder import Host

from shu.plugins.sandbox.rpc import (
    MSG_CALL,
    MSG_FINAL_ERROR,
    MSG_FINAL_RESULT,
    MSG_LOG,
    MSG_READY,
    ParentMessage,
    read_frame,
    write_frame,
)

logger = get_logger(__name__)


class RpcServer:
    """Parent-side RPC transport over a Unix domain socket.

    Lifecycle
    ---------
    1. Caller constructs the server with a pre-built :class:`Host` and a
       handshake payload dict.
    2. Caller opens the UDS connection and passes reader/writer to
       :meth:`serve_on`, which sends the handshake and enters the
       message loop.
    3. At the appropriate moment (after observing ``MSG_READY`` or any
       other launcher-specific readiness gate), the caller invokes
       :meth:`send_execute` on the *same writer* to trigger plugin
       execution in the child.
    4. :meth:`serve_on` returns the plugin result dict when
       ``MSG_FINAL_RESULT`` arrives, or raises on ``MSG_FINAL_ERROR``.

    The server does **not** own connection setup or teardown — that is
    the launcher's responsibility.
    """

    def __init__(self, host: Host, handshake_payload: dict[str, Any]) -> None:
        self._host = host
        self._handshake_payload = handshake_payload

    async def send_handshake(self, writer: asyncio.StreamWriter) -> None:
        """Write the handshake frame.

        Extracted from serve_on so the launcher can sequence outbound
        writes deterministically (handshake → execute) before entering
        the read loop. If we left handshake-writing in serve_on, the
        launcher couldn't interleave send_execute without racing the
        handshake: create_task(serve_on) schedules but doesn't run
        until the current coroutine yields, so a subsequent
        send_execute would fire first.
        """
        await write_frame(writer, ParentMessage.handshake(self._handshake_payload))

    async def serve_on(
        self,
        uds_path: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> dict[str, Any]:
        """Run the parent-side message loop until the child sends a final message.

        Assumes the handshake has already been sent by the caller
        (typically via :meth:`send_handshake` before :meth:`send_execute`
        and this call). Reads frames in a loop. Returns the plugin
        result dict on ``MSG_FINAL_RESULT`` or raises the deserialized
        exception on ``MSG_FINAL_ERROR``.

        Parameters
        ----------
        uds_path:
            Path of the Unix domain socket.  Used only for logging
            context — the actual transport uses the already-open
            *reader*/*writer* pair.  Kept as a parameter so the
            launcher can pass a single call site rather than adding
            separate log statements.
        reader:
            Async stream to read child frames from.
        writer:
            Async stream to write parent frames to.
        """
        logger.info("rpc server starting", extra={"uds_path": uds_path})

        try:
            while True:
                frame = await read_frame(reader)
                msg_type = frame.get("type")

                if msg_type == MSG_READY:
                    logger.debug("child signaled ready", extra={"uds_path": uds_path})
                    continue

                if msg_type == MSG_CALL:
                    await self._dispatch_call(frame, writer)
                    continue

                if msg_type == MSG_LOG:
                    self._handle_log(frame)
                    continue

                if msg_type == MSG_FINAL_RESULT:
                    logger.info("plugin finished successfully", extra={"uds_path": uds_path})
                    return frame["value"]

                if msg_type == MSG_FINAL_ERROR:
                    logger.warning("plugin finished with error", extra={"uds_path": uds_path})
                    raise deserialize_exc(frame)

                logger.warning(
                    "ignoring unknown message type from child",
                    extra={"msg_type": msg_type, "uds_path": uds_path},
                )

        except (ConnectionError, OSError, asyncio.IncompleteReadError) as exc:
            raise ConnectionError(
                f"Child connection lost on {uds_path}: {exc}"
            ) from exc

    async def send_execute(
        self,
        writer: asyncio.StreamWriter,
        vparams: dict[str, Any],
    ) -> None:
        """Send ``MSG_EXECUTE`` to the child.

        Called by the launcher after the child has signaled readiness.
        This is intentionally separate from :meth:`serve_on` so the
        launcher controls the timing.
        """
        await write_frame(writer, ParentMessage.execute(vparams))

    async def _dispatch_call(
        self,
        frame: dict[str, Any],
        writer: asyncio.StreamWriter,
    ) -> None:
        """Dispatch a ``MSG_CALL`` to the appropriate host capability.

        Resolves the capability via ``getattr(self._host, cap_name)`` which
        relies on :class:`Host.__getattribute__` to enforce the declared-
        capabilities gate (raises :class:`CapabilityDenied` for undeclared
        caps).  Then resolves and awaits the method, sending the return
        value back as ``MSG_RESULT`` or any exception as ``MSG_ERROR``.
        """
        call_id: int = frame["id"]
        try:
            cap = getattr(self._host, frame["cap"])
            method = getattr(cap, frame["method"])
            value = await method(*frame["args"], **frame["kwargs"])
        except Exception as exc:
            # Catch-all is intentional: every exception must round-trip to the
            # child so plugin except-blocks work. serialize_exc handles known
            # types (CapabilityDenied, HttpRequestFailed, etc.) and falls back
            # to PluginError for anything else.
            logger.warning(
                "capability call failed",
                extra={
                    "call_id": call_id,
                    "cap": frame.get("cap"),
                    "method": frame.get("method"),
                    "exc": str(exc),
                },
            )
            await write_frame(writer, ParentMessage.error(call_id, serialize_exc(exc)))
            return
        await write_frame(writer, ParentMessage.result(call_id, value))

    def _handle_log(self, frame: dict[str, Any]) -> None:
        """Route a ``MSG_LOG`` record from the child into the parent logger.

        Rebuilds a :class:`logging.LogRecord` from the JSON-safe dict
        sent by the child, attaches plugin identity tags, and re-emits
        it through the parent's logging pipeline so all standard
        handlers (file, structured, etc.) process the child's log
        output.

        Uses a structured dict rather than pickle: a malicious child
        could otherwise craft a pickle payload that executes code in
        the parent on unpickle.
        """
        plugin_name = self._handshake_payload.get("plugin_module", "unknown")
        execution_id = self._handshake_payload.get("execution_id")

        try:
            record = self._rebuild_record(frame["record"])
        except Exception as exc:
            # Malformed log frame from the child — log and skip. A single
            # bad frame must not break the parent's message loop; plugins
            # must not be able to halt the RPC channel by sending garbage.
            logger.warning("failed to decode child log record", extra={"exc": str(exc)})
            return

        record.plugin_name = plugin_name  # type: ignore[attr-defined]
        record.execution_id = execution_id  # type: ignore[attr-defined]

        get_logger(f"plugin.{plugin_name}").handle(record)

    @staticmethod
    def _rebuild_record(payload: dict[str, Any]) -> logging.LogRecord:
        """Reconstruct a :class:`LogRecord` from the child's JSON payload.

        The child has already applied ``msg % args`` in the ferry, so
        we pass ``args=None`` and set ``message`` directly — otherwise
        stdlib formatters would try to re-apply format args and raise.
        """
        record = logging.LogRecord(
            name=payload["name"],
            level=payload["levelno"],
            pathname=payload.get("pathname", ""),
            lineno=payload.get("lineno", 0),
            msg=payload.get("msg", ""),
            args=None,
            exc_info=None,
            func=payload.get("funcName"),
        )
        # Child already called getMessage() before serialization; keep it
        # so parent formatters don't re-run % formatting on a pre-formatted
        # string (which breaks when the message happens to contain "%").
        record.message = payload.get("msg", "")
        if payload.get("exc_text"):
            record.exc_text = payload["exc_text"]
        if payload.get("created") is not None:
            record.created = payload["created"]
        for k, v in (payload.get("extras") or {}).items():
            setattr(record, k, v)
        return record
