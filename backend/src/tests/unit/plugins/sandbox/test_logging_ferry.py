"""Unit tests for the child-side logging ferry."""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import struct
import sys
from pathlib import Path
from typing import Any
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
_ferry_mod = _load_module("shu.plugins.sandbox.logging_ferry", _SANDBOX_DIR / "logging_ferry.py")

install_queue_handler = _ferry_mod.install_queue_handler
drain_loop = _ferry_mod.drain_loop
_DropOldestQueueHandler = _ferry_mod._DropOldestQueueHandler
_QUEUE_MAX_SIZE = _ferry_mod._QUEUE_MAX_SIZE
_DROP_WARN_INTERVAL = _ferry_mod._DROP_WARN_INTERVAL
MSG_LOG = _rpc_mod.MSG_LOG


def _make_writer() -> tuple[asyncio.StreamWriter, MagicMock]:
    transport = MagicMock()
    transport.is_closing = MagicMock(return_value=False)
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    loop = asyncio.get_event_loop()
    writer = asyncio.StreamWriter(transport, protocol, reader, loop)
    return writer, transport


def _decode_log_frames(transport: MagicMock) -> list[dict[str, Any]]:
    """Extract serialized log-record dicts from captured transport.write calls.

    The ferry now ships structured JSON (not pickle) so the payload is
    a dict. Each dict has the shape produced by
    ``logging_ferry._record_to_dict``: ``name``, ``levelno``, ``msg``,
    ``pathname``, ``lineno``, ``funcName``, ``created``, ``exc_text``,
    and an ``extras`` dict for any non-base LogRecord attributes.
    """
    raw = b"".join(call.args[0] for call in transport.write.call_args_list)
    records: list[dict[str, Any]] = []
    offset = 0
    while offset < len(raw):
        (length,) = struct.unpack("!I", raw[offset : offset + 4])
        offset += 4
        frame = json.loads(raw[offset : offset + length])
        offset += length
        assert frame["type"] == MSG_LOG
        records.append(frame["record"])
    return records


class TestInstallQueueHandler:
    # install_queue_handler() mutates the root logger's level to DEBUG;
    # tests that don't restore it leak that state into every subsequent
    # test in the session, which previously surfaced unrelated bugs
    # whose log() calls would otherwise have been filtered out.
    @pytest.fixture(autouse=True)
    def _restore_root_logger(self):
        root = logging.getLogger()
        orig_handlers = list(root.handlers)
        orig_level = root.level
        try:
            yield
        finally:
            root.handlers.clear()
            root.handlers.extend(orig_handlers)
            root.setLevel(orig_level)

    def test_clears_existing_handlers(self):
        root = logging.getLogger()
        root.addHandler(logging.StreamHandler())
        assert len(root.handlers) >= 1
        q, handler = install_queue_handler()
        assert len(root.handlers) == 1
        assert root.handlers[0] is handler

    def test_returns_queue_and_handler(self):
        q, handler = install_queue_handler()
        assert isinstance(q, queue.Queue)
        assert isinstance(handler, _DropOldestQueueHandler)
        assert q.maxsize == _QUEUE_MAX_SIZE

    def test_logger_info_produces_record_on_queue(self):
        q, handler = install_queue_handler()
        logger = logging.getLogger("test.plugin")
        logger.info("hello %s", "world", extra={"k": "v"})
        record = q.get_nowait()
        assert record.getMessage() == "hello world"
        assert record.k == "v"


class TestDropOldestOverflow:
    def test_overflow_drops_oldest_and_increments_counter(self):
        q: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=3)
        handler = _DropOldestQueueHandler(q)

        records = []
        for i in range(5):
            r = logging.LogRecord(
                "test", logging.INFO, "", 0, f"msg-{i}", (), None,
            )
            records.append(r)
            handler.enqueue(r)

        assert handler.drop_count == 2
        # Queue should contain the last 3 records
        remaining = []
        while not q.empty():
            remaining.append(q.get_nowait())
        messages = [r.getMessage() for r in remaining]
        assert messages == ["msg-2", "msg-3", "msg-4"]

    def test_no_drop_when_space_available(self):
        q: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=10)
        handler = _DropOldestQueueHandler(q)
        r = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        handler.enqueue(r)
        assert handler.drop_count == 0
        assert q.qsize() == 1


class TestDrainLoop:
    @pytest.mark.asyncio
    async def test_ferries_record_to_writer(self):
        q: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=100)
        handler = _DropOldestQueueHandler(q)

        record = logging.LogRecord(
            "test.plugin", logging.INFO, "", 0, "hello %s", ("world",), None,
        )
        q.put_nowait(record)

        writer, transport = _make_writer()
        task = asyncio.create_task(drain_loop(q, handler, writer))
        # Give the drain loop time to process
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        records = _decode_log_frames(transport)
        assert len(records) == 1
        # prepare() should have formatted msg % args before serialization
        assert records[0]["msg"] == "hello world"

    @pytest.mark.asyncio
    async def test_exc_info_rendered_to_exc_text(self):
        q: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=100)
        handler = _DropOldestQueueHandler(q)

        try:
            raise ValueError("boom")
        except ValueError:
            record = logging.LogRecord(
                "test", logging.ERROR, "", 0, "fail", (), sys.exc_info(),
            )
        q.put_nowait(record)

        writer, transport = _make_writer()
        task = asyncio.create_task(drain_loop(q, handler, writer))
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        records = _decode_log_frames(transport)
        assert len(records) == 1
        # prepare() renders exc_info into exc_text; exc_info is not shipped
        assert records[0]["exc_text"] is not None
        assert "ValueError: boom" in records[0]["exc_text"]
        assert "exc_info" not in records[0]

    @pytest.mark.asyncio
    async def test_overflow_warning_emitted_when_queue_saturated(self):
        """Regression: the drop-warning must be written directly to the
        writer (not re-queued), so it cannot itself be evicted when the
        queue is under sustained pressure."""
        # Small queue so we can fill it past the threshold easily.
        q: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=_QUEUE_MAX_SIZE)
        handler = _DropOldestQueueHandler(q)

        # Pretend the queue has already dropped enough to cross the
        # warning threshold, and keep the queue full so any re-queue
        # attempt would fail.
        handler.drop_count = _DROP_WARN_INTERVAL
        for i in range(_QUEUE_MAX_SIZE):
            q.put_nowait(logging.LogRecord(
                "test", logging.INFO, "", 0, f"msg-{i}", (), None,
            ))

        writer, transport = _make_writer()
        task = asyncio.create_task(drain_loop(q, handler, writer))
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        records = _decode_log_frames(transport)
        warnings = [r for r in records if r["levelno"] == logging.WARNING
                    and "overflow" in r["msg"]]
        assert len(warnings) >= 1, "drop warning not emitted under saturation"
        assert f"dropped {_DROP_WARN_INTERVAL} records" in warnings[0]["msg"]

    @pytest.mark.asyncio
    async def test_extra_fields_preserved(self):
        q: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=100)
        handler = _DropOldestQueueHandler(q)

        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "x", (), None,
        )
        record.plugin_name = "my_plugin"
        record.custom_key = "custom_val"
        q.put_nowait(record)

        writer, transport = _make_writer()
        task = asyncio.create_task(drain_loop(q, handler, writer))
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        records = _decode_log_frames(transport)
        assert records[0]["extras"]["plugin_name"] == "my_plugin"
        assert records[0]["extras"]["custom_key"] == "custom_val"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
