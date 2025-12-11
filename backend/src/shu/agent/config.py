"""
Agent configuration v0 (contract-first) for Agent Foundation MVP.

Intentionally minimal: config-driven plugin allowlist and basic budgets.
"""
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import List, Dict, Optional


class PluginPolicy(BaseModel):
    name: str
    version: Optional[str] = Field(default="v0")


class AgentConfiguration(BaseModel):
    key: str
    description: Optional[str] = None
    allowed_plugins: List[PluginPolicy] = Field(default_factory=list)
    max_total_seconds: int = 30
    max_plugin_calls: int = 4


# Hard-coded v0 presets (can be moved to DB later)
MORNING_BRIEFING_V0 = AgentConfiguration(
    key="morning_briefing",
    description="Single-agent sequential briefing with Gmail + Calendar + Google Chat insights + KB insights",
    allowed_plugins=[
        PluginPolicy(name="gmail_digest"),
        PluginPolicy(name="calendar_events"),
        PluginPolicy(name="kb_insights"),
        PluginPolicy(name="gchat_digest"),
    ],
    max_total_seconds=180,
    max_plugin_calls=5,
)


def get_agent_config(key: str) -> AgentConfiguration:
    if key == MORNING_BRIEFING_V0.key:
        return MORNING_BRIEFING_V0
    raise ValueError(f"Unknown agent key: {key}")
