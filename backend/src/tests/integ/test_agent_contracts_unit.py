"""
Unit-style contract tests for Agent MVP core contracts.

- PluginResult shape via SequentialRunner artifacts
- SequentialRunner sequencing (plugin order and LLM step marker)
- Orchestrator prompt composition uses only successful plugin summaries
"""
import sys
import os
import asyncio
from typing import List, Callable

from integ.base_unit_test import BaseUnitTestSuite


# --- Test helpers (dummy tools) ---
from shu.agent.plugins.base import PluginInput, PluginResult
from shu.agent.workflow.runner import SequentialRunner, Step
from shu.agent.plugins.registry import registry as plugin_registry
from shu.agent.orchestrator import MorningBriefingOrchestrator


class DummyPluginA:
    name = "test_plugin_a"

    async def execute(self, *, user_id: str, agent_key: str, payload: PluginInput) -> PluginResult:
        return PluginResult(ok=True, name=self.name, summary="A ran", data={"a": 1})


class DummyPluginB:
    name = "test_plugin_b"

    async def execute(self, *, user_id: str, agent_key: str, payload: PluginInput) -> PluginResult:
        return PluginResult(ok=True, name=self.name, summary="B ran", data={"b": 2})


class DummyFailPlugin:
    name = "test_fail_plugin"

    async def execute(self, *, user_id: str, agent_key: str, payload: PluginInput) -> PluginResult:
        return PluginResult(ok=False, name=self.name, summary="failed", error="boom")


# --- Tests ---

def test_sequential_runner_contracts_and_order():
    async def _run():
        # Register dummy plugins
        plugin_registry.register(DummyPluginA())
        plugin_registry.register(DummyPluginB())

        # Monkeypatch registry to bypass DB enablement for this unit test
        orig_resolve = plugin_registry.resolve_enabled
        async def _fake_resolve(db, name, version="v0"):
            return tool_registry.get_registered(name)
        tool_registry.resolve_enabled = _fake_resolve

        class _FakeAsyncResult:
            def scalars(self):
                return self
            def first(self):
                return None
        class _FakeDB:
            async def execute(self, *args, **kwargs):
                return _FakeAsyncResult()

        try:
            steps = [
                Step(kind="plugin", name="test_plugin_a", params={}),
                Step(kind="plugin", name="test_plugin_b", params={}),
                Step(kind="llm", name="synthesize", params={}),
            ]
            runner = SequentialRunner(max_total_seconds=10, max_plugin_calls=4)
            result = await runner.run(user_id="test_user", agent_key="test_agent", steps=steps, ctx={"messages": [], "db": _FakeDB()})
        finally:
            plugin_registry.resolve_enabled = orig_resolve

        # Artifacts contain both plugins in order
        artifacts = result.get("artifacts", {})
        keys = list(artifacts.keys())
        assert keys[:2] == ["test_plugin_a", "test_plugin_b"]

        # Each artifact matches PluginResult-like dict contract
        for k in ["test_tool_a", "test_tool_b"]:
            v = artifacts[k]
            assert v.get("name") == k
            assert "ok" in v and "summary" in v

        # Messages include system messages for tools and an LLM step marker at the end
        messages = result.get("messages", [])
        assert messages and messages[-1]["content"].startswith("LLM step requested:")

    asyncio.run(_run())


def test_orchestrator_compose_prompt_uses_only_success_summaries():
    # Build a synthetic run_result
    run_result = {
        "messages": [{"role": "system", "content": "base"}],
        "artifacts": {
            "gmail_digest": {"ok": True, "name": "gmail_digest", "summary": "Collected 0 messages"},
            "kb_insights": {"ok": False, "name": "kb_insights", "summary": "failed"},
        },
    }
    orch = MorningBriefingOrchestrator(db=None)
    messages = orch._compose_prompt(run_result)

    assert messages[-1]["role"] == "user"
    content = messages[-1]["content"]
    assert "Synthesize the morning briefing" in content
    assert "- gmail_digest: Collected 0 messages" in content
    assert "kb_insights" not in content  # failed tool should not be included


def test_orchestrator_compose_prompt_no_data_path():
    run_result = {"messages": [], "artifacts": {}}
    orch = MorningBriefingOrchestrator(db=None)
    messages = orch._compose_prompt(run_result)
    assert "No plugin data available." in messages[-1]["content"]


class AgentContractsUnitTestSuite(BaseUnitTestSuite):
    def get_test_functions(self) -> List[Callable]:
        return [
            test_sequential_runner_contracts_and_order,
            test_orchestrator_compose_prompt_uses_only_success_summaries,
            test_orchestrator_compose_prompt_no_data_path,
        ]

    def get_suite_name(self) -> str:
        return "Agent Contracts Unit Tests"

    def get_suite_description(self) -> str:
        return "Contract tests for Tool, SequentialRunner, and Orchestrator prompt composition"


if __name__ == "__main__":
    suite = AgentContractsUnitTestSuite()
    code = suite.run()
    sys.exit(code)

