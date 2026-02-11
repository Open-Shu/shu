"""Shared plugin execution logic for a single PluginExecution record.

Both the queue-based worker (_handle_plugin_execution_job) and the scheduler's
run_pending() method delegate to execute_plugin_record() for the core execution
flow. Callers retain their own claim logic, session management, retry semantics,
and error handling.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.plugin_execution import PluginExecution, PluginExecutionStatus
from ..models.plugin_feed import PluginFeed
from ..models.plugin_registry import PluginDefinition
from ..models.provider_identity import ProviderIdentity
from ..plugins.executor import EXECUTOR
from ..plugins.host.auth_capability import AuthCapability
from ..plugins.registry import REGISTRY
from ..services.plugin_identity import (
    PluginIdentityError,
    ensure_secrets_for_plugin,
    resolve_auth_requirements,
)

logger = logging.getLogger(__name__)

# Feed params that should be automatically cleared after successful execution.
ONE_SHOT_FEED_PARAMS = ("reset_cursor",)


@dataclass
class PluginExecutionResult:
    """Result of executing a single PluginExecution record.

    Callers inspect this to decide how to handle the outcome (commit, retry, etc.).
    """

    status: PluginExecutionStatus
    result: dict[str, Any] | None = None
    error: str | None = None
    error_code: str | None = None
    completed_at: datetime | None = None
    skipped: bool = False
    feed_updates: dict[str, Any] = field(default_factory=dict)


async def execute_plugin_record(  # noqa: PLR0912, PLR0915
    session: AsyncSession,
    rec: PluginExecution,
    settings: Any,
) -> PluginExecutionResult:
    """Execute a single PluginExecution record through the full plugin lifecycle.

    Covers:
        1. Check if associated PluginFeed is disabled
        2. Resolve plugin via REGISTRY
        3. Load per-plugin limits from PluginDefinition
        4. Resolve user_email / auth_mode / impersonate_email
        5. Build providers_map from ProviderIdentity rows
        6. Auth preflight (user token, delegation, service account, subscription)
        7. Secrets preflight
        8. Inject __schedule_id into effective params
        9. Call EXECUTOR.execute()
        10. Normalize result via model_dump() with fallback
        11. Enforce output byte cap
        12. Set PluginExecution status to COMPLETED/FAILED
        13. Diagnostics logging
        14. Clear one-shot feed params on success
        15. Update PluginFeed.last_run_at on success

    Does NOT commit the session — callers are responsible for committing.
    Does NOT handle exceptions — callers wrap this in their own try/except
    for retry semantics and error handling.

    Args:
        session: Active async database session (caller manages lifecycle).
        rec: The PluginExecution record (already marked RUNNING by caller).
        settings: Application settings instance.

    Returns:
        PluginExecutionResult describing the outcome. The caller should apply
        rec.status, rec.error, rec.result, rec.completed_at from this result
        before committing.

    Raises:
        HTTPException: Propagated from EXECUTOR (e.g. 429 rate limiting).
        Exception: Any unhandled plugin execution failure.

    """
    # Load the associated PluginFeed once (reused in steps 1, 2, 14-15)
    feed: PluginFeed | None = None
    if rec.schedule_id:
        srow = await session.execute(select(PluginFeed).where(PluginFeed.id == rec.schedule_id))
        feed = srow.scalars().first()

    # Step 1: Check if schedule is disabled
    if feed and not feed.enabled:
        return _preflight_failure(rec, "schedule_disabled")

    # Step 2: Resolve plugin
    plugin = await REGISTRY.resolve(rec.plugin_name, session)
    if not plugin:
        # Auto-disable the feed to prevent repeated failures
        if feed and feed.enabled:
            try:
                feed.enabled = False
            except Exception:
                pass
        return _preflight_failure(rec, "plugin_not_found")

    # Step 3: Per-plugin limits
    lrow = await session.execute(select(PluginDefinition).where(PluginDefinition.name == rec.plugin_name))
    ldef = lrow.scalars().first()
    per_plugin_limits = getattr(ldef, "limits", None) or {}

    # Step 4: Identity resolution
    p = rec.params or {}
    mode = str(p.get("auth_mode") or "").lower()
    user_email_val = p.get("user_email")
    if not user_email_val and mode == "domain_delegate":
        imp = p.get("impersonate_email")
        if imp:
            user_email_val = imp

    # Step 5: Build provider identities map
    providers_map: dict[str, list[dict[str, Any]]] = {}
    try:
        q_pi = select(ProviderIdentity).where(ProviderIdentity.user_id == str(rec.user_id))
        pi_res = await session.execute(q_pi)
        for pi in pi_res.scalars().all():
            providers_map.setdefault(pi.provider_key, []).append(pi.to_dict())
    except Exception:
        logger.warning(
            "Failed to load provider identities, proceeding with empty map | exec_id=%s user=%s",
            rec.id,
            rec.user_id,
            exc_info=True,
        )
        providers_map = {}

    # Step 6: Auth preflight
    auth_failure = await _auth_preflight(session, rec, plugin)
    if auth_failure:
        return auth_failure

    # Step 7: Secrets preflight
    try:
        await ensure_secrets_for_plugin(plugin, str(rec.plugin_name), str(rec.user_id), rec.params or {})
    except PluginIdentityError:
        return _preflight_failure(rec, "missing_secrets")
    except Exception as e:
        logger.warning(
            "Secrets preflight check failed unexpectedly for exec %s plugin %s: %s",
            rec.id,
            rec.plugin_name,
            e,
        )

    # Step 8: Inject schedule_id into params
    base_params = rec.params or {}
    eff_params = dict(base_params) if isinstance(base_params, dict) else {}
    if rec.schedule_id:
        eff_params["__schedule_id"] = str(rec.schedule_id)

    # Step 9: Execute plugin (may raise HTTPException or other exceptions)
    exec_result = await EXECUTOR.execute(
        plugin=plugin,
        user_id=str(rec.user_id),
        user_email=user_email_val,
        agent_key=rec.agent_key,
        params=eff_params,
        limits=per_plugin_limits,
        provider_identities=providers_map,
    )

    # Step 10: Normalize result to dict
    try:
        payload = exec_result.model_dump()
    except Exception:
        if isinstance(exec_result, dict):
            payload = exec_result
        else:
            payload = {
                "status": getattr(exec_result, "status", None),
                "data": getattr(exec_result, "data", None),
                "error": getattr(exec_result, "error", None),
            }

    # Step 11: Enforce output byte cap
    max_bytes = getattr(settings, "plugin_exec_output_max_bytes", 0) or 0
    try:
        payload_json = json.dumps(payload, separators=(",", ":"), default=str)
        payload_size = len(payload_json.encode("utf-8"))
    except Exception:
        logger.warning(
            "Failed to serialize plugin output for size check | exec_id=%s plugin=%s",
            rec.id,
            rec.plugin_name,
            exc_info=True,
        )
        # Treat unserializable output as exceeding the cap
        payload_size = max_bytes + 1 if max_bytes > 0 else 0
    if max_bytes > 0 and payload_size > max_bytes:
        now = datetime.now(UTC)
        _apply_to_record(
            rec,
            status=PluginExecutionStatus.FAILED,
            error=f"output exceeds max bytes ({payload_size} > {max_bytes})",
            result={"status": "error", "error": "output_too_large"},
            completed_at=now,
        )
        return PluginExecutionResult(
            status=PluginExecutionStatus.FAILED,
            error=f"output exceeds max bytes ({payload_size} > {max_bytes})",
            result={"status": "error", "error": "output_too_large"},
            completed_at=now,
        )

    # Step 12: Set execution status
    now = datetime.now(UTC)
    status = PluginExecutionStatus.COMPLETED if payload.get("status") == "success" else PluginExecutionStatus.FAILED
    _err_val = payload.get("error") if payload.get("status") != "success" else None
    if isinstance(_err_val, (dict, list)):
        error_str = json.dumps(_err_val, separators=(",", ":"), default=str)
    else:
        error_str = _err_val

    _apply_to_record(rec, status=status, error=error_str, result=payload, completed_at=now)

    # Step 13: Diagnostics logging
    try:
        from ..plugins.utils import log_plugin_diagnostics as _log_diags
    except Exception:
        _log_diags = None
    if _log_diags:
        _log_diags(payload, plugin_name=str(rec.plugin_name), exec_id=str(rec.id))

    # Steps 14-15: Feed updates on success (one-shot params + last_run_at)
    feed_updates: dict[str, Any] = {}
    if feed and status == PluginExecutionStatus.COMPLETED:
        try:
            feed.last_run_at = now
            feed_updates["last_run_at"] = now
            # Clear one-shot params
            if feed.params:
                params_dict = dict(feed.params) if isinstance(feed.params, dict) else {}
                modified = False
                for key in ONE_SHOT_FEED_PARAMS:
                    if key in params_dict:
                        del params_dict[key]
                        modified = True
                if modified:
                    feed.params = params_dict
                    feed_updates["one_shot_cleared"] = True
        except Exception:
            logger.warning(
                "Failed to update feed after execution | exec_id=%s schedule_id=%s",
                rec.id,
                rec.schedule_id,
                exc_info=True,
            )

    return PluginExecutionResult(
        status=status,
        result=payload,
        error=error_str,
        completed_at=now,
        feed_updates=feed_updates,
    )


def _apply_to_record(
    rec: PluginExecution,
    *,
    status: PluginExecutionStatus,
    error: str | None,
    result: dict[str, Any] | None,
    completed_at: datetime | None,
) -> None:
    """Apply execution outcome fields to the PluginExecution record."""
    rec.status = status
    rec.error = error
    rec.result = result
    rec.completed_at = completed_at


def _preflight_failure(rec: PluginExecution, error_code: str) -> PluginExecutionResult:
    """Build a preflight failure result and apply it to the record."""
    now = datetime.now(UTC)
    _apply_to_record(
        rec,
        status=PluginExecutionStatus.FAILED,
        error=error_code,
        result={"status": "error", "error": error_code},
        completed_at=now,
    )
    return PluginExecutionResult(
        status=PluginExecutionStatus.FAILED,
        error=error_code,
        error_code=error_code,
        result={"status": "error", "error": error_code},
        completed_at=now,
        skipped=True,
    )


async def _auth_preflight(
    session: AsyncSession,
    rec: PluginExecution,
    plugin: Any,
) -> PluginExecutionResult | None:
    """Run auth preflight checks. Returns a failure result if auth fails, else None."""
    try:
        provider, mode_eff, subject, scopes = resolve_auth_requirements(plugin, rec.params or {})
        if not provider:
            return None

        auth = AuthCapability(plugin_name=str(rec.plugin_name), user_id=str(rec.user_id))
        mode_str = (mode_eff or "").strip().lower()
        sc = scopes or []

        if mode_str == "user":
            # Subscription enforcement
            sub_failure = await _check_subscription(session, rec, provider)
            if sub_failure:
                return sub_failure
            tok = await auth.provider_user_token(provider, required_scopes=sc or None)
            if not tok:
                return _preflight_failure(rec, "identity_required")

        elif mode_str == "domain_delegate":
            subj = (subject or "").strip()
            if not subj:
                return _preflight_failure(rec, "identity_required")
            resp = await auth.provider_delegation_check(provider, scopes=sc, subject=subj)
            if not (isinstance(resp, dict) and resp.get("ready") is True):
                return _preflight_failure(rec, "identity_required")

        elif mode_str == "service_account":
            try:
                _ = await auth.provider_service_account_token(provider, scopes=sc, subject=None)
            except Exception:
                return _preflight_failure(rec, "identity_required")

    except Exception:
        # Resolution/setup failure defaults to allow — inner checks handle fail-closed.
        logger.warning(
            "Auth preflight resolution failed, defaulting to allow | exec_id=%s plugin=%s",
            rec.id,
            rec.plugin_name,
            exc_info=True,
        )

    return None


async def _check_subscription(
    session: AsyncSession,
    rec: PluginExecution,
    provider: str,
) -> PluginExecutionResult | None:
    """Check subscription enforcement for user auth mode. Returns failure result or None."""
    try:
        from ..services.host_auth_service import HostAuthService

        subs = await HostAuthService.list_subscriptions(session, str(rec.user_id), provider, None)
        if subs:
            subscribed_names = {s.plugin_name for s in subs}
            if str(rec.plugin_name) not in subscribed_names:
                logger.warning(
                    "subscription.enforced | user=%s provider=%s plugin=%s path=plugin_runner",
                    str(rec.user_id),
                    provider,
                    str(rec.plugin_name),
                )
                return _preflight_failure(rec, "subscription_required")
    except Exception:
        # Do not block execution if enforcement check fails unexpectedly
        pass
    return None
