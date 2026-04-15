"""Unit tests for the child-side logging ferry."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import logging
import pickle
import queue
import struct
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

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


def _decode_log_frames(transport: MagicMock) -> list[logging.LogRecord]:
    """Extract LogRecord objects from captured transport.write calls."""
    raw = b"".join(call.args[0] for call in transport.write.call_args_list)
    records: list[logging.LogRecord] = []
    offset = 0
    while offset < len(raw):
        (length,) = struct.unpack("!I", raw[offset : offset + 4])
        offset += 4
        frame = json.loads(raw[offset : offset + length])
        offset += length
        assert frame["type"] == MSG_LOG
        record = pickle.loads(base64.b64decode(frame["record"]))
        records.append(record)
    return records


class TestInstallQueueHandler:
    def test_clears_existing_handlers(self):
        root = logging.getLogger()
        orig_handlers = list(root.handlers)
        try:
            root.addHandler(logging.StreamHandler())
            assert len(root.handlers) >= 1
            q, handler = install_queue_handler()
            assert len(root.handlers) == 1
            assert root.handlers[0] is handler
        finally:
            root.handlers.clear()
            root.handlers.extend(orig_handlers)

    def test_returns_queue_and_handler(self):
        root = logging.getLogger()
        orig_handlers = list(root.handlers)
        try:
            q, handler = install_queue_handler()
            assert isinstance(q, queue.Queue)
            assert isinstance(handler, _DropOldestQueueHandler)
            assert q.maxsize == _QUEUE_MAX_SIZE
        finally:
            root.handlers.clear()
            root.handlers.extend(orig_handlers)

    def test_logger_info_produces_record_on_queue(self):
        root = logging.getLogger()
        orig_handlers = list(root.handlers)
        try:
            q, handler = install_queue_handler()
            logger = logging.getLogger("test.plugin")
            logger.info("hello %s", "world", extra={"k": "v"})
            record = q.get_nowait()
            assert record.getMessage() == "hello world"
            assert record.k == "v"
        finally:
            root.handlers.clear()
            root.handlers.extend(orig_handlers)


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
        # prepare() should have formatted msg % args
        assert records[0].message == "hello world"

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
        # prepare() renders exc_info into exc_text and clears exc_info
        assert records[0].exc_text is not None
        assert "ValueError: boom" in records[0].exc_text
        assert records[0].exc_info is None

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
        warnings = [r for r in records if r.levelno == logging.WARNING
                    and "overflow" in r.getMessage()]
        assert len(warnings) >= 1, "drop warning not emitted under saturation"
        assert f"dropped {_DROP_WARN_INTERVAL} records" in warnings[0].getMessage()

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
        assert records[0].plugin_name == "my_plugin"
        assert records[0].custom_key == "custom_val"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
