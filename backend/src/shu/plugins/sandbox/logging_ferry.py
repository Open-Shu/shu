"""Child-side logging ferry for the plugin sandbox.

Python's stdlib logging API is synchronous — ``logger.info(...)`` is a
regular ``def`` and cannot ``await`` — but the UDS channel to the parent
is async. The queue is the only bridge that preserves the logging
contract: a sync :class:`logging.handlers.QueueHandler` drops records
onto a bounded in-process queue, and a separate async drain task reads
them off and writes them as ``MSG_LOG`` frames over the UDS. Side
benefits: plugin latency is decoupled from parent backpressure, the
bounded queue caps child-process memory under a log-spamming plugin,
and the drain task survives plugin cancellation so trailing records
still flush before the child exits.

The drain task prepares each record (formats ``msg % args``, renders
``exc_info`` to ``exc_text``, clears unpicklable fields like live frame
references), pickles it, and base64-encodes before writing.

Overflow policy: when the queue is full the **oldest** record is dropped
and a counter is incremented. Every 1 000 drops a synthetic warning is
emitted through the queue so loss is visible in the parent logs.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import logging
import logging.handlers
import pickle
import queue
from typing import Any

from shu.plugins.sandbox.rpc import ChildMessage, write_frame

_QUEUE_MAX_SIZE: int = 1000
_DROP_WARN_INTERVAL: int = 1000

# Formatter used to render exc_info → exc_text before pickling.
# The parent re-formats with its own pipeline, but exc_info contains
# live frame references that can't be pickled — rendering here ensures
# the traceback text survives the boundary.
_FORMATTER = logging.Formatter()


class _DropOldestQueueHandler(logging.handlers.QueueHandler):
    """QueueHandler that drops the oldest record on overflow."""

    def __init__(self, q: queue.Queue[Any]) -> None:
        super().__init__(q)
        self.drop_count: int = 0

    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            # Drop the oldest record to make room.
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            self.drop_count += 1
            try:
                self.queue.put_nowait(record)
            except queue.Full:
                pass


def install_queue_handler() -> tuple[queue.Queue[logging.LogRecord], _DropOldestQueueHandler]:
    """Replace all root-logger handlers with a :class:`_DropOldestQueueHandler`.

    Returns the ``(queue, handler)`` tuple so the caller can pass the
    queue to :func:`drain_loop` and the handler to :func:`drain_loop`
    for ``prepare()`` calls.
    """
    q: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=_QUEUE_MAX_SIZE)
    handler = _DropOldestQueueHandler(q)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    return q, handler


def _prepare_record(record: logging.LogRecord) -> logging.LogRecord:
    """Make *record* safe for pickling while preserving ``exc_text``.

    stdlib ``QueueHandler.prepare()`` zeroes ``exc_text`` — we need it
    to survive so the parent can re-emit the traceback. This function
    does the same work (format ``msg % args``, render ``exc_info``
    → ``exc_text``, clear unpicklable fields) but keeps ``exc_text``.
    """
    # Shallow-copy before mutating: the caller (third-party libs,
    # pytest caplog, plugin code that inspects records post-emit)
    # still holds a reference to this record, and nulling args /
    # exc_info / stack_info on it would destroy state they may need.
    # Matches stdlib's documented "modified copy" pattern for
    # overriding QueueHandler.prepare.
    record = copy.copy(record)
    record.message = record.getMessage()
    record.msg = record.message
    record.args = None
    if record.exc_info:
        record.exc_text = _FORMATTER.formatException(record.exc_info)
        record.exc_info = None
    record.stack_info = None
    return record


async def drain_loop(
    q: queue.Queue[logging.LogRecord],
    handler: _DropOldestQueueHandler,
    writer: asyncio.StreamWriter,
) -> None:
    """Pull log records from *q* and ferry them to the parent over *writer*.

    Runs until cancelled.  Each record is ``prepare()``-d (formats
    ``msg % args``, renders ``exc_info`` → ``exc_text``), pickled, and
    base64-encoded before writing as a ``MSG_LOG`` frame.

    A synthetic "dropped N records" warning is written directly to
    *writer* every :data:`_DROP_WARN_INTERVAL` drops so loss is visible
    in the parent logs.
    """
    last_warned_at: int = 0

    while True:
        # Poll the sync queue with a short sleep to stay cancellation-
        # friendly. run_in_executor would block the thread pool and
        # resist cancellation.
        try:
            record: logging.LogRecord = q.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.05)
            continue

        # Write the overflow warning directly to the writer rather than
        # re-queueing it. Plugins can log from threads (threading.Thread,
        # asyncio.to_thread, third-party libs) — a thread that refills
        # the queue between the get() above and a would-be put() below
        # would silently drop the warning, defeating the signal exactly
        # when it matters most. A direct write cannot be dropped.
        current_bucket = (handler.drop_count // _DROP_WARN_INTERVAL) * _DROP_WARN_INTERVAL
        if current_bucket > last_warned_at and handler.drop_count >= _DROP_WARN_INTERVAL:
            await _write_drop_warning(writer, handler.drop_count)
            last_warned_at = current_bucket

        prepared = _prepare_record(record)
        data = base64.b64encode(pickle.dumps(prepared)).decode("ascii")
        try:
            await write_frame(writer, ChildMessage.log(data))
        except (ConnectionError, OSError):
            # Parent gone — stop silently; the child will exit soon anyway.
            break


async def _write_drop_warning(
    writer: asyncio.StreamWriter,
    drop_count: int,
) -> None:
    """Write a synthetic overflow warning frame directly to *writer*.

    Bypasses the queue so plugin threads cannot refill it and drop the
    warning. If the parent has disconnected the write fails silently —
    the next main-record write in ``drain_loop`` will detect it and
    exit.
    """
    warning_record = logging.LogRecord(
        name="shu.plugins.sandbox.logging_ferry",
        level=logging.WARNING,
        pathname=__file__,
        lineno=0,
        msg="Plugin log queue overflow: dropped %d records",
        args=(drop_count,),
        exc_info=None,
    )
    prepared = _prepare_record(warning_record)
    data = base64.b64encode(pickle.dumps(prepared)).decode("ascii")
    try:
        await write_frame(writer, ChildMessage.log(data))
    except (ConnectionError, OSError):
        pass
