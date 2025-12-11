"""
SequentialRunner v0: executes a linear list of steps with time/plugin-call limits.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from time import monotonic

from ..plugins.base import PluginInput, PluginResult
from ..plugins.registry import registry as plugin_registry
from ...core.logging import get_logger
from pydantic import BaseModel, ValidationError

logger = get_logger(__name__)


@dataclass
class Step:
    kind: str  # "plugin" | "llm"
    name: str
    params: Dict[str, Any]


class SequentialRunner:
    def __init__(self, *, max_total_seconds: int = 30, max_plugin_calls: int = 4):
        self.max_total_seconds = max_total_seconds
        self.max_plugin_calls = max_plugin_calls

    async def run(self, *, user_id: str, agent_key: str, steps: List[Step], ctx: Dict[str, Any]) -> Dict[str, Any]:
        start = monotonic()
        plugin_calls = 0
        artifacts: Dict[str, Any] = {}
        messages: List[Dict[str, str]] = ctx.get("messages", [])

        for step in steps:
            elapsed = monotonic() - start
            if elapsed > self.max_total_seconds:
                logger.warning("SequentialRunner time budget exceeded", extra={"elapsed_s": round(elapsed, 3)})
                break

            if step.kind == "plugin":
                if plugin_calls >= self.max_plugin_calls:
                    logger.info("Plugin call budget reached", extra={"max_plugin_calls": self.max_plugin_calls})
                    break
                # Validate/coerce params if plugin declares an input_model
                validated_params = step.params or {}
                try:
                    tool = await self._resolve_plugin(ctx, step.name)
                    if tool:
                        input_model = getattr(tool, "input_model", None)
                        if input_model is not None and isinstance(input_model, type) and issubclass(input_model, BaseModel):
                            model_instance = input_model(**validated_params)
                            validated_params = model_instance.model_dump()
                except ValidationError as ve:
                    res = PluginResult(ok=False, name=step.name, summary="input validation failed", error=str(ve))
                    artifacts[step.name] = res.dict()
                    logger.warning("Plugin input validation failed", extra={"plugin": step.name, "error": str(ve)})
                    continue
                except Exception as e:
                    # If validation plumbing itself fails, surface a clear error and skip execution
                    res = PluginResult(ok=False, name=step.name, summary="input validation error", error=str(e))
                    artifacts[step.name] = res.dict()
                    logger.error("Plugin input validation error", extra={"plugin": step.name, "error": str(e)})
                    continue

                t0 = monotonic()
                res: Optional[PluginResult] = None
                runner_adapter = ctx.get("plugin_runner")
                if callable(runner_adapter):
                    try:
                        res = await runner_adapter(step.name, validated_params)
                    except Exception as e:  # noqa: BLE001
                        logger.error("Plugin runner adapter failed", extra={"plugin": step.name, "error": str(e)})
                        res = PluginResult(ok=False, name=step.name, summary="plugin runner error", error=str(e))

                if res is None:
                    if not tool:
                        artifacts[step.name] = {"ok": False, "error": "plugin not enabled or not found"}
                        logger.warning("Plugin not enabled or not found", extra={"plugin": step.name})
                        continue
                    res = await tool.execute(
                        user_id=user_id,
                        agent_key=agent_key,
                        payload=PluginInput(params=validated_params),
                    )
                dt = monotonic() - t0
                plugin_calls += 1
                artifacts[step.name] = res.dict()
                logger.info("Plugin step completed", extra={"plugin": step.name, "ok": res.ok, "duration_s": round(dt, 3)})
                # Append a system message summarizing tool output for LLM context
                if res.ok:
                    messages.append({"role": "system", "content": f"[{step.name}] {res.summary}"})
            elif step.kind == "llm":
                # LLM handled by orchestrator; here we just pass the intent
                messages.append({"role": "system", "content": f"LLM step requested: {step.name}"})
            else:
                artifacts[step.name] = {"ok": False, "error": f"unknown step kind {step.kind}"}
                logger.warning("Unknown step kind", extra={"kind": step.kind})

        total = monotonic() - start
        logger.info("SequentialRunner completed", extra={"total_duration_s": round(total, 3), "plugin_calls": plugin_calls})
        return {"artifacts": artifacts, "messages": messages}

    async def _resolve_plugin(self, ctx: Dict[str, Any], name: str):
        db = ctx.get("db")
        tool = await plugin_registry.resolve_enabled(db, name, version="v0")
        if tool is None:
            try:
                allowlist = set(ctx.get("allowlist", []) or [])
                if name in allowlist:
                    # Fallback to code-registered plugin when explicitly allowlisted by the agent config
                    return plugin_registry.get_registered(name)
            except Exception:
                pass
        return tool
