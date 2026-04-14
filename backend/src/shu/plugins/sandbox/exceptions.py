"""Cross-IPC exception contract for the plugin sandbox.

Capability exceptions raised in the parent (e.g. ``HttpRequestFailed``,
``CapabilityDenied``) are serialized into a JSON-friendly payload, shipped
across the Unix domain socket to the child, and re-raised there as an
instance of the original type so plugin ``except``-blocks keep working
unchanged.

On-wire shape returned by :func:`serialize_exc`::

    {"exc_type": str, "payload": dict, "traceback": str}

``exc_type`` is the short class name used as the key in
:data:`SERIALIZABLE`. Types not in the registry serialize as
``exc_type="PluginError"`` with ``payload`` carrying the original type
name and formatted traceback so diagnostics survive even when the
original class cannot be reconstructed.
"""

import traceback
from typing import Any

from shu.plugins.host.exceptions import CapabilityDenied, EgressDenied, HttpRequestFailed


SERIALIZABLE: dict[str, type[Exception]] = {
    "CapabilityDenied": CapabilityDenied,
    "HttpRequestFailed": HttpRequestFailed,
    "EgressDenied": EgressDenied,
    # Builtins: caught by specific plugins and also raised by host capabilities
    # (e.g. storage_capability for oversized payloads), so they must round-trip
    # as the original type rather than collapsing to PluginError.
    # See task-4 audit of plugins/ for the list of catch sites.
    "ValueError": ValueError,
    "TypeError": TypeError,
}


class PluginError(Exception):
    """Fallback for exception types that are not in :data:`SERIALIZABLE`.

    Raised in the child when the parent reports an exception whose type is
    not on the round-trip allow-list. Preserves the original type name and
    a formatted traceback so the failure is still diagnosable even though
    the original type cannot be reconstructed.

    Attributes:
        message: Short human-readable message (the original ``str(exc)``).
        traceback_text: ``traceback.format_exception(...)`` output from the
            parent, included verbatim for log/diagnostic purposes.
        original_type: Short name of the parent-side exception class that
            could not be deserialized.
    """

    def __init__(self, message: str, traceback_text: str, original_type: str) -> None:
        super().__init__(message)
        self.message = message
        self.traceback_text = traceback_text
        self.original_type = original_type


def _format_traceback(exc: Exception) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def _serialize_payload(exc: Exception) -> dict[str, Any]:
    """Return the per-type ctor-arg payload for a registered exception.

    Types whose ctor takes more than a single message (``HttpRequestFailed``)
    or whose ctor transforms its arg into a formatted message
    (``CapabilityDenied``) get custom handling. Everything else falls
    through to the default ``{"message": str(exc)}`` shape, which is what
    every builtin Exception and any user-defined single-message exception
    supports.
    """
    if isinstance(exc, HttpRequestFailed):
        return {
            "status_code": exc.status_code,
            "url": exc.url,
            "body": exc.body,
            "headers": dict(exc.headers),
        }
    if isinstance(exc, CapabilityDenied):
        # Can't use str(exc) as the message: CapabilityDenied's ctor wraps
        # its arg in "Host capability '{x}' not declared..." formatting, so
        # round-tripping the formatted message would double-wrap it.
        return {"capability": exc.capability}
    return {"message": str(exc)}


def _deserialize_registered(cls: type[Exception], data: dict[str, Any]) -> Exception:
    """Reconstruct a registered exception from its serialized payload."""
    if cls is HttpRequestFailed:
        return HttpRequestFailed(
            status_code=data["status_code"],
            url=data["url"],
            body=data.get("body"),
            headers=data.get("headers"),
        )
    if cls is CapabilityDenied:
        return CapabilityDenied(data["capability"])
    return cls(data.get("message", ""))


def serialize_exc(exc: Exception) -> dict[str, Any]:
    """Serialize ``exc`` into a JSON-friendly dict for transport over IPC.

    Registered types (keys of :data:`SERIALIZABLE`) keep their identity;
    everything else collapses into the :class:`PluginError` shape with the
    original type name and formatted traceback preserved.

    Registry lookup uses exact type name (``type(exc).__name__``) rather
    than ``isinstance``, so subclasses of registered types (e.g. a
    user-defined ``MyValueError(ValueError)``) do NOT masquerade as the
    base type — they fall back to :class:`PluginError` with their real
    class name preserved in ``original_type``.
    """
    tb_text = _format_traceback(exc)
    exc_type_name = type(exc).__name__
    if exc_type_name in SERIALIZABLE:
        return {
            "exc_type": exc_type_name,
            "payload": _serialize_payload(exc),
            "traceback": tb_text,
        }
    return {
        "exc_type": "PluginError",
        "payload": {
            "message": str(exc),
            "traceback_text": tb_text,
            "original_type": exc_type_name,
        },
        "traceback": tb_text,
    }


def deserialize_exc(payload: dict[str, Any]) -> Exception:
    """Reconstruct an exception instance from a :func:`serialize_exc` payload.

    Unknown ``exc_type`` values fall back to :class:`PluginError` so the
    child never fails to raise *something* on a capability error.
    """
    exc_type = payload.get("exc_type", "PluginError")
    data: dict[str, Any] = payload.get("payload", {}) or {}
    tb_text = payload.get("traceback", "") or ""

    cls = SERIALIZABLE.get(exc_type)
    if cls is not None:
        return _deserialize_registered(cls, data)

    # Unknown type — or explicit PluginError payload. Accept both shapes:
    # the serialize_exc-produced payload has message/traceback_text/original_type,
    # and a raw unknown-type payload may only have exc_type set.
    return PluginError(
        message=data.get("message", ""),
        traceback_text=data.get("traceback_text", tb_text),
        original_type=data.get("original_type", exc_type),
    )
