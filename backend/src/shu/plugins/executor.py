"""
Plugin executor: coordinates rate limiting, schema validation (if provided), and plugin execution.
"""
from __future__ import annotations
import json
import logging
from typing import Any, Dict, Optional, List
import sys
import importlib
from importlib.abc import MetaPathFinder
from datetime import datetime, timezone


from .host.host_builder import make_host
from .host.exceptions import HttpRequestFailed

from pydantic import BaseModel, ValidationError
from fastapi import HTTPException

# Optional JSON Schema validation support
try:
    import jsonschema  # type: ignore
except Exception:  # noqa: BLE001
    jsonschema = None  # type: ignore
from ..core.cache_backend import get_cache_backend, CacheBackend


from ..core.config import get_settings_instance  # type: ignore
from .base import ExecuteContext, PluginResult, Plugin

logger = logging.getLogger(__name__)


# Trusted module prefixes that are allowed to import shu.* even during plugin execution.
# These are host capabilities and core services that plugins call through the host API.
_TRUSTED_MODULE_PREFIXES = (
    "shu.plugins.host.",      # Host capabilities (kb, http, auth, etc.)
    "shu.services.",          # Core services called by host capabilities
    "shu.core.",              # Core infrastructure (database, config, logging)
    "shu.processors.",        # Text extraction, etc.
    "shu.knowledge.",         # Knowledge base utilities
    "shu.models.",            # SQLAlchemy models
)


class _DenyImportsFinder(MetaPathFinder):
    """Import finder that blocks certain imports during plugin execution.
    
    This finder is installed into sys.meta_path during plugin execution to prevent
    plugins from importing:
    - Direct HTTP clients (requests, httpx, urllib3) - must use host.http
    - Internal shu.* modules - must use host capabilities
    
    However, trusted host code (host capabilities, services, etc.) IS allowed to
    import shu.* modules. This is determined by inspecting the call stack.
    """
    # Deny direct HTTP clients from plugins at runtime.
    deny_always = {"requests", "httpx", "urllib3", "urllib.request"}
    # Deny shu.* imports only from untrusted (plugin) code
    deny_from_plugins = {"shu"}

    def _is_called_from_trusted_code(self) -> bool:
        """Check if the import is being called from trusted host code.
        
        Walks the call stack to find the module that initiated the import.
        If any frame in the stack is from a trusted module prefix, allow the import.
        
        Performance: Uses sys._getframe() for efficiency instead of traceback.extract_stack().
        Short-circuits as soon as a trusted frame is found.
        """
        import sys
        # Walk frames directly - more efficient than traceback.extract_stack()
        # which creates FrameSummary objects for every frame
        try:
            frame = sys._getframe(2)  # Skip this method and find_spec/caller
        except ValueError:
            return False
        
        while frame is not None:
            filename = frame.f_code.co_filename
            # Quick check: only inspect frames from shu modules
            if "/shu/" in filename:
                for prefix in _TRUSTED_MODULE_PREFIXES:
                    # Convert prefix to path fragment: "shu.services." -> "/shu/services/"
                    path_fragment = "/" + prefix.replace(".", "/").rstrip("/") + "/"
                    if path_fragment in filename:
                        return True
                    # Also check for exact module file matches
                    path_fragment_exact = "/" + prefix.replace(".", "/").rstrip("/")
                    if filename.endswith(path_fragment_exact + ".py"):
                        return True
            frame = frame.f_back
        return False

    def find_spec(self, fullname, path, target=None):  # type: ignore[override]
        # Block exact and submodule imports under denylisted packages
        name = str(fullname)
        
        # Always block HTTP clients - no exceptions
        for p in self.deny_always:
            if name == p or name.startswith(p + "."):
                raise ImportError(
                    f"Import of '{fullname}' is denied by host policy. Use host.http instead."
                )
        
        # Block shu.* imports only from plugin code, not from trusted host code
        for p in self.deny_from_plugins:
            if name == p or name.startswith(p + "."):
                if not self._is_called_from_trusted_code():
                    raise ImportError(
                        f"Import of '{fullname}' is denied by host policy. Use host.http instead."
                    )
        
        return None


class _DenyHttpImportsCtx:
    """Context manager to install/remove the deny-imports finder safely.
    Also patches importlib.import_module to deny disallowed names even if preloaded in sys.modules.

    The deny policy allows trusted host code (host capabilities, services, etc.) to import
    shu.* modules while blocking plugin code from doing so. HTTP client imports are always
    blocked regardless of caller.
    """
    def __init__(self):
        self._finder: Optional[_DenyImportsFinder] = None
        self._orig_import_module = None

    @staticmethod
    def _is_called_from_trusted_code() -> bool:
        """Check if the import is being called from trusted host code.
        
        Performance: Uses sys._getframe() for efficiency instead of traceback.extract_stack().
        """
        import sys
        try:
            frame = sys._getframe(2)
        except ValueError:
            return False
        
        while frame is not None:
            filename = frame.f_code.co_filename
            if "/shu/" in filename:
                for prefix in _TRUSTED_MODULE_PREFIXES:
                    path_fragment = "/" + prefix.replace(".", "/").rstrip("/") + "/"
                    if path_fragment in filename:
                        return True
                    path_fragment_exact = "/" + prefix.replace(".", "/").rstrip("/")
                    if filename.endswith(path_fragment_exact + ".py"):
                        return True
            frame = frame.f_back
        return False

    @staticmethod
    def _is_denied(name: str) -> bool:
        """Check if an import should be denied.
        
        HTTP clients are always denied. shu.* imports are denied only from plugin code.
        """
        try:
            n = str(name)
        except Exception:
            n = ""
        
        # Always deny HTTP clients
        for p in _DenyImportsFinder.deny_always:
            if n == p or n.startswith(p + "."):
                return True
        
        # Deny shu.* only from plugin code
        for p in _DenyImportsFinder.deny_from_plugins:
            if n == p or n.startswith(p + "."):
                if not _DenyHttpImportsCtx._is_called_from_trusted_code():
                    return True
        
        return False

    def __enter__(self):
        self._finder = _DenyImportsFinder()
        sys.meta_path.insert(0, self._finder)
        # Patch importlib.import_module to catch explicit dynamic imports
        try:
            self._orig_import_module = importlib.import_module
            # Capture in local variable to avoid closure referencing self._orig_import_module
            # which gets set to None in __exit__
            orig_import = self._orig_import_module

            def _guard(name, package=None):  # type: ignore[no-redef]
                if _DenyHttpImportsCtx._is_denied(name):
                    raise ImportError(
                        f"Import of '{name}' is denied by host policy. Use host.http instead."
                    )
                return orig_import(name, package)

            importlib.import_module = _guard  # type: ignore[assignment]
        except Exception:
            self._orig_import_module = None
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            # Restore importlib.import_module
            if self._orig_import_module is not None:
                importlib.import_module = self._orig_import_module  # type: ignore[assignment]
        except Exception:
            pass
        try:
            if self._finder is not None:
                if sys.meta_path and sys.meta_path[0] is self._finder:
                    sys.meta_path.pop(0)
                else:
                    if self._finder in sys.meta_path:
                        sys.meta_path.remove(self._finder)
        except Exception:
            pass
        self._finder = None
        self._orig_import_module = None
        return False


class Executor:
    def __init__(self, settings: Optional[Any] = None):
        """
        Initialize executor rate limiters from configuration.

        If rate limiting is enabled in settings, create a per-user/per-tool TokenBucketRateLimiter (namespace "rl:plugin:user")
        and a provider/model TokenBucketRateLimiter (namespace "rl:plugin:prov") using the configured requests-per-period and period to
        derive capacity and refill rate. On any initialization error, log the failure and leave both limiter attributes set to None.

        Args:
            settings: Application settings (uses get_settings_instance if not provided)
        """
        self._limiter = None  # per-user/per-tool limiter
        self._provider_limiter = None  # provider/model limiter
        try:
            if settings is None:
                settings = get_settings_instance()
            if settings.enable_rate_limiting:
                from ..core.rate_limiting import TokenBucketRateLimiter
                # Per-user defaults using settings directly
                rpm = settings.rate_limit_user_requests
                period = settings.rate_limit_user_period
                capacity = max(1, rpm)
                refill_per_second = max(1, int(rpm / max(1, period)))
                self._limiter = TokenBucketRateLimiter(
                    namespace="rl:plugin:user",
                    capacity=capacity,
                    refill_per_second=refill_per_second,
                )
                # Provider limiter defaults; per-call overrides will set actual caps
                self._provider_limiter = TokenBucketRateLimiter(
                    namespace="rl:plugin:prov",
                    capacity=capacity,
                    refill_per_second=refill_per_second,
                )
        except Exception:
            logger.exception("Failed to initialize rate limiter; proceeding without rate limiting")
            self._limiter = None
            self._provider_limiter = None

    def _validate(self, plugin: Plugin, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate the provided params against the plugin's input schema and return the params if validation succeeds.
        
        If the plugin exposes no schema, the params are returned unchanged. If jsonschema is available, perform full schema validation and raise an HTTPException with status 422 and a structured detail on validation failure. If jsonschema is not available, ensure all keys listed under the schema's "required" field are present and raise an HTTPException 422 identifying a missing key if not.
        
        Parameters:
            plugin (Plugin): Plugin instance whose schema will be used (via plugin.get_schema()).
            params (Dict[str, Any]): Input parameters to validate.
        
        Returns:
            Dict[str, Any]: The same `params` dictionary if validation passes.
        
        Raises:
            HTTPException: Raised with status code 422 and a structured detail when validation fails or required keys are missing.
        """
        schema = None
        try:
            schema = plugin.get_schema()
        except Exception:
            logger.exception("Plugin.get_schema failed for %s", getattr(plugin, "name", "?"))
        if not schema:
            return params
        # If jsonschema is available, perform full validation; otherwise minimal required check
        if jsonschema is not None:
            try:
                jsonschema.validate(instance=params, schema=schema)  # type: ignore[attr-defined]
                return params
            except Exception as e:  # noqa: BLE001
                # Normalize error surface
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "validation_error",
                        "message": str(e),
                    },
                )
        # Fallback: minimal check
        required = (schema or {}).get("required", [])
        for k in required:
            if k not in params:
                raise HTTPException(status_code=422, detail={"error": "validation_error", "missing": k})
        return params

    def _validate_output(self, plugin: Plugin, data: Optional[Dict[str, Any]]) -> None:
        schema = None
        try:
            get_out = getattr(plugin, "get_output_schema", None)
            if callable(get_out):
                schema = get_out()
        except Exception:
            logger.exception("Plugin.get_output_schema failed for %s", getattr(plugin, "name", "?"))
        if not schema:
            return
        if jsonschema is not None:
            try:
                jsonschema.validate(instance=data or {}, schema=schema)  # type: ignore[attr-defined]
            except Exception as e:  # noqa: BLE001
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": "output_validation_error",
                        "message": str(e),
                    },
                )
        else:
            # Minimal fallback: ensure required keys are present
            required = (schema or {}).get("required", [])
            for k in required:
                if not data or k not in data:
                    raise HTTPException(status_code=500, detail={"error": "output_validation_error", "missing": k})


    async def _enforce_quotas(self, *, bucket: str, daily_limit: int, monthly_limit: int) -> None:
        """Check and consume per-user/per-plugin quotas (daily/monthly).
        Raises HTTPException(429) with detail {error: quota_exceeded, period, reset_in} when exceeded.
        Uses atomic increment to avoid TOCTOU race conditions.
        """
        if daily_limit <= 0 and monthly_limit <= 0:
            return
        try:
            cache = await get_cache_backend()
        except Exception:
            # If we cannot get a cache backend and quotas are configured, be safe and allow (documented limitation)
            logger.exception("Quota enforcement unavailable; proceeding without quotas")
            return

        now = datetime.now(timezone.utc)
        # End of day
        end_of_day = datetime(now.year, now.month, now.day, 23, 59, 59, tzinfo=timezone.utc)
        reset_in_day = max(1, int((end_of_day - now).total_seconds()))
        # End of month (first day of next month at 00:00:00)
        if now.month == 12:
            next_month_start = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            next_month_start = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
        reset_in_month = max(1, int((next_month_start - now).total_seconds()))

        day_key = f"quota:d:{bucket}"
        month_key = f"quota:m:{bucket}"

        # Check daily quota
        if daily_limit > 0:
            await self._check_and_consume_quota(
                cache=cache,
                key=day_key,
                limit=daily_limit,
                reset_in=reset_in_day,
                period="daily",
                window_seconds=86400,
            )

        # Check monthly quota
        if monthly_limit > 0:
            try:
                await self._check_and_consume_quota(
                    cache=cache,
                    key=month_key,
                    limit=monthly_limit,
                    reset_in=reset_in_month,
                    period="monthly",
                    window_seconds=reset_in_month + 1,
                )
            except HTTPException:
                # Monthly quota exceeded - rollback daily increment if we made one
                if daily_limit > 0:
                    try:
                        await cache.decr(day_key)
                    except Exception as decr_err:
                        logger.error("Failed to decrement daily quota counter after monthly limit exceeded: key=%s, err=%s", day_key, decr_err)
                raise

    async def _check_and_consume_quota(
        self,
        *,
        cache: "CacheBackend",
        key: str,
        limit: int,
        reset_in: int,
        period: str,
        window_seconds: int,
    ) -> None:
        """Atomically check and consume a single quota counter.
        
        Uses increment-first pattern to avoid TOCTOU race conditions.
        Raises HTTPException(429) if quota is exceeded.
        """
        new_count = await cache.incr(key)
        # Set expiry only when key was just created (to avoid extending TTL on existing counters)
        if new_count == 1:
            await cache.expire(key, reset_in)
        
        if new_count > limit:
            # Over quota - decrement back and deny
            try:
                await cache.decr(key)
            except Exception as decr_err:
                logger.error("Failed to decrement %s quota counter after exceeding limit: key=%s, err=%s", period, key, decr_err)
            headers = {
                "Retry-After": str(reset_in),
                "RateLimit-Limit": f"{limit};w={window_seconds}",
                "RateLimit-Remaining": "0",
                "RateLimit-Reset": str(reset_in),
            }
            raise HTTPException(status_code=429, detail={"error": "quota_exceeded", "period": period, "reset_in": reset_in}, headers=headers)

    async def _acquire_provider_concurrency(self, *, provider: str, limit: int) -> bool:
        if limit <= 0:
            return True
        try:
            cache = await get_cache_backend()
        except Exception:
            logger.exception("Concurrency enforcement unavailable; allowing request")
            return True
        key = f"conc:{provider}"
        try:
            n = await cache.incr(key)
            # set short TTL to auto-recover from crashes
            await cache.expire(key, 30)
            if int(n) > int(limit):
                await cache.decr(key)
                return False
            return True
        except Exception:
            logger.exception("Concurrency counter failed; allowing request")
            return True

    async def _release_provider_concurrency(self, *, provider: str) -> None:
        try:
            cache = await get_cache_backend()
            await cache.decr(f"conc:{provider}")
        except Exception:
            pass

    async def execute(self, *, plugin: Plugin, user_id: str, user_email: Optional[str], agent_key: Optional[str], params: Dict[str, Any], limits: Optional[Dict[str, Any]] = None, provider_identities: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> PluginResult:
        # Split host-only overlay from plugin params (reserved key) BEFORE validation
        """
        Execute a plugin call with rate limiting, quotas, validation, and import-deny enforcement.
        
        This method enforces per-user and provider quotas/rate-limits, optionally acquires provider concurrency slots, validates input and output against plugin schemas when available, constructs the host execution context (including resolved provider auth and schedule id), runs the plugin under a runtime import deny policy, maps host HTTP failures to structured provider errors, and returns the plugin execution result.
        
        Parameters:
            plugin (Plugin): The plugin instance to execute.
            user_id (str): The invoking user's identifier used for quota and rate-limit scoping.
            user_email (Optional[str]): The invoking user's email for host context population.
            agent_key (Optional[str]): Optional agent key for the execution context.
            params (Dict[str, Any]): Plugin invocation parameters; a reserved "__host" dict may be supplied and will be removed from plugin-visible params and merged into the host context.
            limits (Optional[Dict[str, Any]]): Optional per-plugin overrides for quotas and rate limits. Recognized keys include "quota_daily_requests", "quota_monthly_requests", "rate_limit_user_requests", "rate_limit_user_period", "provider_name", "provider_rpm", "provider_window_seconds", and "provider_concurrency".
            provider_identities (Optional[Dict[str, List[Dict[str, Any]]]]): Optional provider identity mappings to include in the host context.
        
        Returns:
            PluginResult: The plugin's execution result. On host HTTP failures returns a PluginResult with code "provider_error" and structured details; on other plugin exceptions returns a PluginResult with code "plugin_execute_error".
        
        Raises:
            HTTPException: For quota, rate-limit, or provider concurrency violations (status 429) and for other HTTP-level rejections raised by the plugin execution path.
        """
        raw_params = dict(params or {})
        host_overlay = {}
        try:
            host_overlay = dict(raw_params.get("__host") or {}) if isinstance(raw_params.get("__host"), dict) else {}
        except Exception:
            host_overlay = {}
        # Remove reserved key from plugin-visible params
        if "__host" in raw_params:
            raw_params.pop("__host", None)

        # Resolve effective limits/quotas (per-tool overrides -> global defaults)
        limits = limits or {}
        try:
            s = get_settings_instance()
            # Quotas
            daily = int(limits.get("quota_daily_requests") or s.plugin_quota_daily_requests_default or 0)
            monthly = int(limits.get("quota_monthly_requests") or s.plugin_quota_monthly_requests_default or 0)
            # Rate limit using settings directly
            rl_req = int(limits.get("rate_limit_user_requests") or s.rate_limit_user_requests or 60)
            rl_period = int(limits.get("rate_limit_user_period") or s.rate_limit_user_period or 60)
            # Provider caps (optional per-tool override)
            provider_name = str(limits.get("provider_name") or "").strip()
            provider_rpm = int(limits.get("provider_rpm") or 0)
            provider_window = int(limits.get("provider_window_seconds") or 60)
            provider_concurrency = int(limits.get("provider_concurrency") or 0)
        except Exception:
            daily = 0
            monthly = 0
            rl_req = 60
            rl_period = 60
            provider_name = ""
            provider_rpm = 0
            provider_window = 60
            provider_concurrency = 0
        bucket = f"{plugin.name}:{plugin.version}:{user_id}"
        await self._enforce_quotas(bucket=bucket, daily_limit=daily, monthly_limit=monthly)

        # Rate limit per user+plugin
        if self._limiter:
            refill = max(1, int(rl_req / max(1, rl_period)))
            logger.debug("RateLimit check | bucket=%s capacity=%s refill_per_second=%s", bucket, max(1, rl_req), refill)
            result = await self._limiter.check(key=bucket, cost=1, capacity=max(1, rl_req), refill_per_second=refill)
            if not result.allowed:
                raise HTTPException(
                    status_code=429,
                    detail={"error": "rate_limited", "retry_after": result.retry_after_seconds},
                    headers=result.to_headers(),
                )

        # Provider RPM cap (shared across plugins using same provider name)
        acquired_concurrency = False
        try:
            if self._provider_limiter and provider_name and provider_rpm > 0:
                prov_refill = max(1, int(provider_rpm / max(1, provider_window)))
                result = await self._provider_limiter.check(
                    key=provider_name, cost=1, capacity=max(1, provider_rpm), refill_per_second=prov_refill
                )
                if not result.allowed:
                    raise HTTPException(
                        status_code=429,
                        detail={"error": "provider_rate_limited", "provider": provider_name, "retry_after": result.retry_after_seconds},
                        headers=result.to_headers(),
                    )
            # Provider concurrency cap
            if provider_name and provider_concurrency > 0:
                acquired_concurrency = await self._acquire_provider_concurrency(provider=provider_name, limit=provider_concurrency)
                if not acquired_concurrency:
                    headers = {"Retry-After": "1", "X-Provider-Concurrency-Limit": str(provider_concurrency)}
                    raise HTTPException(status_code=429, detail={"error": "provider_concurrency_limited", "provider": provider_name}, headers=headers)

            # Validate
            vparams = self._validate(plugin, raw_params)

            # Derive op_auth scopes into host overlay for host.auth resolution (AUTH-REF-001)
            try:
                op_name = str((vparams.get("op") or "")).lower()
            except Exception:
                op_name = ""
            try:
                op_auth_map = getattr(plugin, "_op_auth", None)
            except Exception:
                op_auth_map = None
            if isinstance(op_auth_map, dict) and op_name and (op_name in op_auth_map):
                try:
                    oa = op_auth_map.get(op_name) or {}
                    provider = str((oa.get("provider") or "")).lower().strip()
                    scopes = oa.get("scopes") or []
                    if provider and scopes:
                        if not isinstance(host_overlay, dict):
                            host_overlay = {}
                        auth_ctx = host_overlay.get("auth") if isinstance(host_overlay, dict) else None
                        if not isinstance(auth_ctx, dict):
                            auth_ctx = {}
                            host_overlay["auth"] = auth_ctx
                        prov_ctx = auth_ctx.get(provider)
                        if not isinstance(prov_ctx, dict):
                            prov_ctx = {}
                            auth_ctx[provider] = prov_ctx
                        if not prov_ctx.get("scopes"):
                            prov_ctx["scopes"] = list(scopes) if isinstance(scopes, (list, tuple)) else [str(scopes)]
                except Exception:
                    pass

            # Backfill auth mode/subject from params using resolver (AUTH-REF-001)
            try:
                from ..services.plugin_identity import resolve_auth_requirements
                provider_eff, mode_eff, subject_eff, scopes_eff = resolve_auth_requirements(plugin, vparams or {})
                if provider_eff:
                    if not isinstance(host_overlay, dict):
                        host_overlay = {}
                    auth_ctx = host_overlay.get("auth") if isinstance(host_overlay, dict) else None
                    if not isinstance(auth_ctx, dict):
                        auth_ctx = {}
                        host_overlay["auth"] = auth_ctx
                    prov_ctx = auth_ctx.get(provider_eff)
                    if not isinstance(prov_ctx, dict):
                        prov_ctx = {}
                        auth_ctx[provider_eff] = prov_ctx
                    # Do not overwrite UI-provided values
                    if mode_eff and not prov_ctx.get("mode"):
                        prov_ctx["mode"] = str(mode_eff)
                    if subject_eff and not prov_ctx.get("subject"):
                        prov_ctx["subject"] = str(subject_eff)
                    if scopes_eff and not prov_ctx.get("scopes"):
                        prov_ctx["scopes"] = list(scopes_eff)
            except Exception:
                pass

            # Thread schedule_id into host context for cursor capability
            try:
                sid = vparams.get("__schedule_id") if isinstance(vparams, dict) else None
                if sid:
                    if not isinstance(host_overlay, dict):
                        host_overlay = {}
                    exec_ctx = host_overlay.get("exec") if isinstance(host_overlay, dict) else None
                    if not isinstance(exec_ctx, dict):
                        exec_ctx = {}
                        host_overlay["exec"] = exec_ctx
                    if not exec_ctx.get("schedule_id"):
                        exec_ctx["schedule_id"] = str(sid)
            except Exception:
                pass

            # Build host with capability whitelist from plugin._capabilities if present
            capabilities: List[str] = []
            try:
                capabilities = list(getattr(plugin, "_capabilities", []) or [])
            except Exception:
                capabilities = []
            host = make_host(plugin_name=plugin.name, user_id=user_id, user_email=user_email, capabilities=capabilities, provider_identities=(provider_identities or {}), host_context=host_overlay)
            # Execute under import deny-hook for HTTP clients and host internals
            ctx = ExecuteContext(user_id=user_id, agent_key=agent_key)
            with _DenyHttpImportsCtx():
                try:
                    result = await plugin.execute(vparams, ctx, host)
                    # Validate output schema only on success payloads
                    try:
                        status = getattr(result, "status", None)
                    except Exception:
                        status = None
                    if status == "success":
                        self._validate_output(plugin, getattr(result, "data", None))
                    return result
                except HTTPException:
                    raise
                except Exception as e:  # noqa: BLE001
                    # Map host.http failures to a structured provider_error so callers get clear surfaces
                    if isinstance(e, HttpRequestFailed):
                        try:
                            body = e.body
                            # Try to extract a useful provider message
                            if isinstance(body, dict):
                                prov_msg = body.get("error_description") or body.get("error") or body.get("message") or str(body)
                            else:
                                prov_msg = str(body)[:400] if body is not None else ""
                        except Exception:
                            prov_msg = str(e)
                        details = {
                            "status_code": e.status_code,
                            "url": e.url,
                            "provider_message": prov_msg,
                        }
                        return PluginResult.err(message=f"Provider HTTP error ({e.status_code})", code="provider_error", details=details)
                    logger.exception("Plugin '%s' failed: %s", plugin.name, e)
                    return PluginResult.err(message=str(e), code="plugin_execute_error")
        finally:
            # Release provider concurrency if acquired
            try:
                if provider_name and acquired_concurrency:
                    await self._release_provider_concurrency(provider=provider_name)
            except Exception:
                pass


EXECUTOR = Executor()
