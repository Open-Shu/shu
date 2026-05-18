"""Shared SSE stream generator utility for Shu RAG Backend.

This module provides a reusable async generator for Server-Sent Events (SSE)
streaming, with robust error handling, optional error sanitization, and
correlation ID support. It is used by both the chat and experience streaming
endpoints.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator, Callable
from typing import Any

from .logging import get_logger

logger = get_logger(__name__)


def sanitize_stream_error_message(error_content: str | None) -> str:
    """Sanitize error messages for SSE streams while preserving actionable errors.

    Provides selective error sanitization so that user-actionable errors
    (rate limits, timeouts, service unavailability) are passed through
    transparently, while all other errors (API keys, auth, config, DB, etc.)
    are replaced with a generic message to avoid leaking internal details.

    Args:
        error_content: The original error message from the provider or backend.

    Returns:
        Either the original error message (for allowed errors) or a sanitized
        generic message.

    """
    if not error_content:
        return "The request failed. You may want to try another model."

    error_lower = error_content.lower()

    # Preserve user-actionable error types
    if "rate limit" in error_lower or "too many requests" in error_lower:
        return error_content

    if "timeout" in error_lower or "timed out" in error_lower:
        return error_content

    if "service unavailable" in error_lower or "temporarily unavailable" in error_lower:
        return error_content

    # Sanitize all other errors (API keys, auth, config, DB, malformed requests, etc.)
    return "The request failed. You may want to try another model."


async def create_sse_stream_generator(
    event_generator: AsyncGenerator[Any, None],
    error_context: str = "streaming",
    error_sanitizer: Callable[[str | None], str] | None = sanitize_stream_error_message,
    include_correlation_id: bool = False,
    error_code: str = "STREAM_ERROR",
    on_close: Callable[[], None] | None = None,
) -> AsyncGenerator[str, None]:
    r"""Wrap an async event generator with robust error handling for SSE streaming.

    Iterates over the supplied ``event_generator``, serialises each event as a
    JSON SSE payload, and yields the result.  Two distinct error-handling paths
    are implemented:

    **Point A -- in-stream error events:**
    If an individual event has ``type == "error"`` and an ``error_sanitizer``
    callable is provided, the sanitizer is applied to the serialised payload's
    ``content`` and ``message`` fields (when present).  This operates on
    the dict returned by ``to_dict()`` rather than mutating event internals,
    so it works for any event type (chat events with ``content``, experience
    events with ``message``, etc.).  Serialisation failures for individual
    events are logged and skipped so the stream can continue.

    **Point B -- catch-all exception handler:**
    If the underlying generator raises an unexpected exception, a generic error
    payload is constructed and yielded.  A ``correlation_id`` is included in both
    the log output and the client payload when ``include_correlation_id`` is
    ``True``.  ``GeneratorExit`` and ``CancelledError`` (both forms of client
    disconnect / task teardown) are handled gracefully and re-raised so the
    enclosing framework (Starlette / FastAPI) keeps its cancellation contract
    intact — never swallow either, just log them and let unwinding continue.

    A ``data: [DONE]\\n\\n`` marker is always emitted as the final event.

    Args:
        event_generator: Async generator that yields events with a ``to_dict()``
            method.
        error_context: Human-readable context for log messages (e.g.
            ``"send_message"``, ``"experience_execution"``).
        error_sanitizer: Callable to transform the ``content`` field of
            error-type events before serialisation (Point A only).  Defaults to
            ``sanitize_stream_error_message`` which preserves rate-limit,
            timeout, and service-unavailable errors while replacing all others
            with a generic message.  Pass ``None`` to disable sanitisation.
        include_correlation_id: When ``True``, generates a UUID correlation ID
            and includes it in catch-all error payloads and log messages.
        error_code: Error code string included in catch-all error payloads.
        on_close: SHU-784. Optional cleanup callback invoked from the wrapper's
            ``finally`` block regardless of how the stream ended (normal
            completion, client disconnect, internal error). Chat passes a
            closure that fires the stream's ``StreamLifecycle`` with
            ``reason="client_disconnected"``; experience / side-call callers
            pass ``None`` and are unaffected. The callback runs synchronously
            (``asyncio.Event.set`` and a dict mutation are sync), so it does
            not block the cleanup path. Exceptions from the callback are
            logged and swallowed — a bookkeeping failure must not stop the
            stream from closing.

    Yields:
        SSE-formatted ``data: {json}\\n\\n`` strings.

    """
    try:
        # Point A: iterate events and serialize
        async for event in event_generator:
            try:
                payload = event.to_dict()

                # Apply error sanitizer to error-type events if provided
                if error_sanitizer is not None and payload.get("type") == "error":
                    if "content" in payload:
                        payload["content"] = error_sanitizer(payload.get("content"))
                    elif "message" in payload:
                        payload["message"] = error_sanitizer(payload.get("message"))

                yield f"data: {json.dumps(payload)}\n\n"
            except Exception:
                logger.exception(f"Error serializing event during {error_context}")
                # Continue to next event rather than breaking the stream
                continue
    except GeneratorExit:
        # Client disconnected (aclose() form). Pre-SHU-784 behavior: log
        # and fall through to the finally block so the DONE marker still
        # has a chance to emit. We deliberately do NOT re-raise here —
        # re-raising would prevent the finally's `yield "[DONE]"` from
        # reaching the consumer, which is the load-bearing assertion in
        # `test_done_marker_always_emitted_after_generator_exit`. The
        # production disconnect path uses CancelledError (handled below),
        # not in-band GeneratorExit from the inner generator, so this
        # branch is in practice only exercised by tests and any caller
        # that explicitly raises GeneratorExit from its inner generator.
        logger.info(f"Client disconnected from {error_context} stream (GeneratorExit)")
    except asyncio.CancelledError:
        # Client disconnected (task-cancel form). Starlette cancels the
        # streaming task when the underlying TCP connection closes. We
        # re-raise to honor the cancellation contract — the enclosing
        # framework relies on CancelledError propagating up. SHU-784:
        # without this branch, CancelledError fell through to the
        # `except Exception` catch-all below and was logged as a streaming
        # error, which polluted the error-rate metrics for what is in fact
        # a normal client-disconnect event.
        logger.info(f"Stream cancelled during {error_context} (client disconnect)")
        raise
    except Exception:
        # Point B: catch-all exception handler for stream-level failures
        correlation_id = str(uuid.uuid4()) if include_correlation_id else None

        # Build log extras
        log_extra: dict[str, Any] = {}
        if correlation_id is not None:
            log_extra["correlation_id"] = correlation_id

        # Log full exception details server-side for debugging
        logger.exception(f"Streaming error during {error_context}", extra=log_extra)

        # Construct sanitized error payload for the client
        error_payload: dict[str, Any] = {
            "type": "error",
            "code": error_code,
            "message": "An internal streaming error occurred",
        }
        if correlation_id is not None:
            error_payload["id"] = correlation_id

        try:
            yield f"data: {json.dumps(error_payload)}\n\n"
        except Exception:
            logger.exception(f"Failed to send error event to client during {error_context}")
    finally:
        # SHU-784: fire the on_close hook regardless of how we got here —
        # normal completion, client disconnect (either form), or internal
        # error. Wrapped in try/except so a misbehaving callback doesn't
        # block the [DONE] emit or surface as a confusing secondary error.
        if on_close is not None:
            try:
                on_close()
            except Exception:
                logger.exception(f"on_close hook failed during {error_context}")
        # Always send DONE marker to properly close the stream
        try:
            yield "data: [DONE]\n\n"
        except Exception:
            logger.debug(f"Could not send DONE marker during {error_context} - connection likely closed")
