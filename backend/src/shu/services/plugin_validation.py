"""
Shared validation helpers for plugin execution request/response size enforcement.
"""
from __future__ import annotations
from typing import Any, Dict, Optional
import json
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession


def json_size_bytes(obj: Any) -> int:
    """Return UTF-8 JSON byte length for obj; return 0 on serialization failure."""
    try:
        s = json.dumps(obj, separators=(",", ":"), default=str)
        return len(s.encode("utf-8"))
    except Exception:
        return 0


def enforce_input_limit(obj: Any, max_bytes: int) -> None:
    """Raise HTTPException 413 if serialized obj exceeds max_bytes (when > 0)."""
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        return
    size = json_size_bytes(obj)
    if size > max_bytes:
        raise HTTPException(
            status_code=413,
            detail={
                "error": "input_too_large",
                "message": f"request body exceeds max bytes ({size} > {max_bytes})",
                "size": size,
                "limit": max_bytes,
            },
        )


async def enforce_output_limit(
    payload: Dict[str, Any],
    max_bytes: int,
    exec_rec: Any,
    db: AsyncSession,
) -> None:
    """
    Enforce response payload size limit. On limit breach:
    - mark execution FAILED with output_too_large
    - commit exec record
    - raise HTTPException 413 with envelope-aligned error
    """
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        return
    size = json_size_bytes(payload)
    if size > max_bytes:
        # Local import to avoid circulars
        try:
            from ..models.plugin_execution import PluginExecutionStatus  # type: ignore
        except Exception:
            PluginExecutionStatus = None  # best-effort fallback
        # Update execution record similarly to existing behavior
        try:
            exec_rec.completed_at = datetime.now(timezone.utc)
            if PluginExecutionStatus is not None:
                exec_rec.status = PluginExecutionStatus.FAILED
            exec_rec.error = f"output exceeds max bytes ({size} > {max_bytes})"
            exec_rec.result = {"status": "error", "error": "output_too_large"}
            await db.commit()
        except Exception:
            # Don't mask the primary 413 error if commit/update fails
            pass
        raise HTTPException(
            status_code=413,
            detail={
                "error": "output_too_large",
                "message": f"response body exceeds max bytes ({size} > {max_bytes})",
                "size": size,
                "limit": max_bytes,
            },
        )

