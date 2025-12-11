"""
Feed op policy enforcement for plugin feeds.
- Reads allowed_feed_ops and default_feed_op from registry manifest
- Validates/sets params["op"] accordingly
"""
from __future__ import annotations
from typing import Dict, Any, Optional
from fastapi import HTTPException

from ..plugins.registry import REGISTRY


def enforce_feed_op(plugin_name: str, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    p = dict(params or {})
    try:
        manifest = REGISTRY.get_manifest(refresh_if_empty=True)
        rec = manifest.get(plugin_name)
        allowed = list(rec.allowed_feed_ops or []) if rec and getattr(rec, "allowed_feed_ops", None) is not None else []
        default_op = rec.default_feed_op if rec and getattr(rec, "default_feed_op", None) is not None else None
    except Exception:
        allowed = []
        default_op = None

    if not allowed:
        return p

    op = p.get("op")
    if not op:
        if default_op:
            p["op"] = default_op
        else:
            raise HTTPException(status_code=400, detail={
                "error": "invalid_feed_op",
                "message": f"op is required; allowed: {allowed}",
            })
    elif op not in allowed:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_feed_op",
            "message": f"op '{op}' not allowed for feeds; allowed: {allowed}",
        })
    return p

