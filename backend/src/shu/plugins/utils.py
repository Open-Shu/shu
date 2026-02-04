"""Shared plugin utilities: diagnostics and error logging helpers."""

from __future__ import annotations

import logging
from typing import Any


# TODO: Refactor this function. It's too complex (number of branches and statements).
def log_plugin_diagnostics(  # noqa: PLR0912
    payload: dict[str, Any] | None,
    *,
    plugin_name: str,
    exec_id: str | None = None,
    user_id: str | None = None,
    _logger: logging.Logger | None = None,
) -> None:
    """Log plugin result diagnostics and errors consistently.

    - Logs diagnostics lines from payload.data.diagnostics
      - Lines starting with "skip:" at WARNING, others at INFO
    - If payload.status != success, logs an error summary at WARNING
    """
    if payload is None or not isinstance(payload, dict):
        return
    logger = _logger or logging.getLogger(__name__)

    # Diagnostics array
    try:
        data_block = payload.get("data") if isinstance(payload, dict) else None
        diags = []
        if isinstance(data_block, dict):
            diags = data_block.get("diagnostics") or []
        if isinstance(diags, list):
            for d in diags:
                try:
                    line = str(d)
                except Exception:
                    line = str(d)
                ctx = []
                if exec_id:
                    ctx.append(f"exec_id={exec_id}")
                if user_id:
                    ctx.append(f"user_id={user_id}")
                ctx_str = (" " + " ".join(ctx)) if ctx else ""
                if line.startswith("skip:"):
                    logger.warning("plugin.diagnostics | plugin=%s%s %s", plugin_name, ctx_str, line)
                else:
                    logger.info("plugin.diagnostics | plugin=%s%s %s", plugin_name, ctx_str, line)
    except Exception:
        pass

    # Error summary
    try:
        if payload.get("status") != "success":
            err_obj = payload.get("error")
            err_msg = err_obj.get("message") if isinstance(err_obj, dict) else str(err_obj)
            err_code = (err_obj.get("code") if isinstance(err_obj, dict) else None) or "plugin_error"
            if exec_id and user_id:
                logger.warning(
                    "Plugin execution error | plugin=%s exec_id=%s user_id=%s code=%s msg=%s",
                    plugin_name,
                    exec_id,
                    user_id,
                    err_code,
                    (err_msg or ""),
                )
            elif exec_id:
                logger.warning(
                    "Plugin execution error | plugin=%s exec_id=%s code=%s msg=%s",
                    plugin_name,
                    exec_id,
                    err_code,
                    (err_msg or ""),
                )
            elif user_id:
                logger.warning(
                    "Plugin execution error | plugin=%s user_id=%s code=%s msg=%s",
                    plugin_name,
                    user_id,
                    err_code,
                    (err_msg or ""),
                )
            else:
                logger.warning(
                    "Plugin execution error | plugin=%s code=%s msg=%s",
                    plugin_name,
                    err_code,
                    (err_msg or ""),
                )
    except Exception:
        pass
