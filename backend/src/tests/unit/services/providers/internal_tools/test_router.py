import types
from decimal import Decimal

import pytest

from shu.services.providers.internal_tools.base import InternalTool
from shu.services.providers.internal_tools.router import InternalToolRouter


class _StubTool(InternalTool):
    """Tool stub for router tests. Records what it was called with."""

    name = "stub_tool"
    description = "A stub for tests."

    def __init__(self) -> None:
        self.called_with: list[dict] = []
        self.next_result: str = "ok"
        self.next_is_error: bool = False
        self.next_cost: Decimal = Decimal("0.001")
        self.raise_next: Exception | None = None

    def parameter_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, args: dict) -> tuple[str, bool, Decimal]:
        self.called_with.append(args)
        if self.raise_next is not None:
            raise self.raise_next
        return (self.next_result, self.next_is_error, self.next_cost)


@pytest.fixture(scope="function")
def stub_tool():
    return _StubTool()


@pytest.fixture(scope="function")
def router(stub_tool):
    # Construct a router with our stub instead of the real WebSearchTool. We
    # bypass the constructor's default tool wiring by mutating _tools — this
    # is the test-only seam; production code never touches it.
    settings = types.SimpleNamespace(
        brave_search_api_key=None,
        brave_search_cost_per_query=Decimal("0"),
    )
    r = InternalToolRouter(settings)
    r._tools = {"stub_tool": stub_tool}
    return r


def test_is_internal_plugin(router):
    # `int` is the wire-format virtual plugin name dispatched in-process;
    # anything else falls through to the plugin registry.
    assert router.is_internal_plugin("int") is True
    assert router.is_internal_plugin("gmail") is False
    assert router.is_internal_plugin("") is False
    assert router.is_internal_plugin("int:web_search") is False  # toggle key, not plugin name


def test_pop_toggle_keys_strips_int_prefix_keys_and_mutates_in_place(router):
    params = {
        "temperature": 0.7,
        "int:web_search": True,
        "int:other_tool": False,
        "max_tokens": 2048,
    }
    toggles = router.pop_toggle_keys(params)
    assert toggles == {"int:web_search": True, "int:other_tool": False}
    # In-place mutation: the toggles are removed from params so they
    # never reach the wire.
    assert params == {"temperature": 0.7, "max_tokens": 2048}


def test_pop_toggle_keys_returns_empty_when_no_toggles(router):
    params = {"temperature": 0.7}
    assert router.pop_toggle_keys(params) == {}
    assert params == {"temperature": 0.7}


def test_pop_toggle_keys_coerces_truthy_values_to_bool(router):
    # Toggle values can land as JSON-ish primitives off ModelConfiguration;
    # the contract is they read as bool downstream.
    params = {"int:web_search": 1, "int:other": ""}
    assert router.pop_toggle_keys(params) == {"int:web_search": True, "int:other": False}


def test_wire_name_uses_namespace(router):
    assert router.wire_name("web_search") == "int__web_search"


def test_get_callable_returns_plugin_shaped_callable_tool(router):
    callable_tool = router.get_callable("int:stub_tool")
    assert callable_tool is not None
    # CallableTool.name is the namespace ("int") so the wire-format function
    # name produced by inject_tool_payload (`<name>__<op>`) is
    # "int__stub_tool" — byte-identical in shape to a plugin tool.
    assert callable_tool.name == "int"
    # op is the bare tool name — what the dispatch path uses to look up
    # the tool when the model emits a call.
    assert callable_tool.op == "stub_tool"
    # plugin is None — internal tools aren't plugin-routed.
    assert callable_tool.plugin is None
    # Title is the tool's description (becomes the function description).
    assert callable_tool.title == "A stub for tests."


def test_get_callable_unknown_prefixed_name_returns_none(router):
    assert router.get_callable("int:unknown_tool") is None


def test_get_callable_rejects_non_prefixed_names(router):
    # The router only accepts prefixed lookups at its public surface, so
    # even calling get_callable with a bare or plugin-style name returns
    # None — guards against the lift in client.py passing wrong keys.
    assert router.get_callable("stub_tool") is None
    assert router.get_callable("plugin__op") is None
    assert router.get_callable("") is None


@pytest.mark.asyncio
async def test_execute_invokes_tool_by_bare_op(router, stub_tool):
    # execute() takes the bare op (what _call_plugin gets from the model's
    # tool_call name after splitting on `__`) and returns
    # (content, is_error, cost).
    stub_tool.next_result = "search results here"
    stub_tool.next_is_error = False
    stub_tool.next_cost = Decimal("0.005")
    content, is_error, cost = await router.execute("stub_tool", {"query": "anything"})
    assert content == "search results here"
    assert is_error is False
    assert cost == Decimal("0.005")
    assert stub_tool.called_with == [{"query": "anything"}]


@pytest.mark.asyncio
async def test_execute_forwards_zero_cost_success(router, stub_tool):
    # A successful free tool (e.g. calculator, current_time) must record
    # as success even though the cost is zero — separating is_error from
    # cost is the whole point of the 3-tuple return.
    stub_tool.next_result = "42"
    stub_tool.next_is_error = False
    stub_tool.next_cost = Decimal("0")
    content, is_error, cost = await router.execute("stub_tool", {})
    assert content == "42"
    assert is_error is False
    assert cost == Decimal("0")


@pytest.mark.asyncio
async def test_execute_unknown_op_returns_structured_error_and_zero_cost(router):
    content, is_error, cost = await router.execute("unknown", {})
    # Error string uses the wire-format name (int__<op>) so logs are
    # consistent with what the model emitted. Cost is always zero for
    # error paths — we don't bill the user for an unknown-tool attempt.
    assert content == "unknown internal tool: int__unknown"
    assert is_error is True
    assert cost == Decimal("0")


@pytest.mark.asyncio
async def test_execute_wraps_tool_exception_in_structured_error_and_zero_cost(router, stub_tool):
    # A tool that raises must never crash the conversation turn — the
    # router catches and returns a model-readable error string with
    # is_error=True and zero cost.
    stub_tool.raise_next = RuntimeError("brave is on fire")
    content, is_error, cost = await router.execute("stub_tool", {})
    assert content == "int__stub_tool failed: brave is on fire"
    assert is_error is True
    assert cost == Decimal("0")


def test_router_constructor_wires_real_web_search_tool():
    # The default constructor builds the WebSearchTool registry — verify
    # that integration without touching the network.
    settings = types.SimpleNamespace(
        brave_search_api_key=None,
        brave_search_cost_per_query=Decimal("0"),
    )
    r = InternalToolRouter(settings)
    assert "web_search" in r._tools
    # Tools are stored by their bare name; both the prefix and the
    # namespace are wire concerns the router resolves on its own.
    assert r._tools["web_search"].name == "web_search"


# ----------------------------------------------------------------------
# InternalTool subclass validation (covers base.__init_subclass__ — L2).
# ----------------------------------------------------------------------


def test_subclass_with_colon_in_name_raises():
    # The `int:` prefix is owned by the router; tools can't claim it.
    with pytest.raises(TypeError, match=r"int:` prefix is owned"):

        class _Bad(InternalTool):
            name = "ns:foo"
            description = "x"

            def parameter_schema(self):
                return {}

            async def execute(self, args):
                return ("", False, Decimal("0"))


def test_subclass_with_double_underscore_in_name_raises():
    # `__` is the plugin-operation separator on the wire — putting it in
    # a tool name would shred dispatch.
    with pytest.raises(TypeError, match=r"plugin-operation separator"):

        class _Bad(InternalTool):
            name = "foo__bar"
            description = "x"

            def parameter_schema(self):
                return {}

            async def execute(self, args):
                return ("", False, Decimal("0"))
