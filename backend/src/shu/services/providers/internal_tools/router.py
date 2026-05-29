"""Internal tool router (SHU-816).

Single owner of all framework-internal tools and the only module in the
codebase that knows the ``int:`` prefix literal. Tools register under
their bare name; the router prepends ``int:`` on the way out and strips
it on the way in. Adapters delegate prefix-based dispatch and tool
definition lookup here without ever touching the prefix themselves.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from shu.core.logging import get_logger
from shu.models.plugin_execution import CallableTool

from .base import InternalTool
from .web_search import WebSearchTool

if TYPE_CHECKING:
    from shu.core.config import Settings

logger = get_logger(__name__)


class InternalToolRouter:
    """Owns framework-internal tools with two intentionally distinct namespaces.

    - ``_PREFIX = "int:"`` — the **user-facing** prefix on parameter-mapping
      keys (e.g. ``"int:web_search": BooleanParameter(...)``). Persists on
      ``ModelConfiguration.parameter_overrides``. Never goes on the wire.
    - ``_NAMESPACE = "int"`` — the **wire-format** plugin name. Every
      internal tool becomes an "op" under this virtual plugin, so the
      function name the model sees is ``int__<bare_tool_name>`` — exactly
      the same shape as a plugin (``gmail_digest__list``). Models truncate
      tool-call argument streaming when they encounter unfamiliar function-
      name shapes; matching the plugin convention fixes that.

    Both constants are intentionally private — callers reach for the
    predicate methods (``is_internal_plugin``, ``pop_toggle_keys``)
    rather than reading the literals directly, so the prefix stays a
    single source of truth.
    """

    _PREFIX = "int:"  # user-facing param-mapping key prefix
    _NAMESPACE = "int"  # wire-format plugin-name; also the dispatch key

    def __init__(self, settings: Settings) -> None:
        # Bare-name keyed map. Both the prefix and the namespace are wire
        # concerns — never a storage concern.
        self._tools: dict[str, InternalTool] = {
            "web_search": WebSearchTool(
                api_key=settings.brave_search_api_key,
                cost_per_query=settings.brave_search_cost_per_query,
            ),
        }

    @classmethod
    def is_internal_plugin(cls, plugin_name: str) -> bool:
        """Whether ``plugin_name`` (the LHS of ``__`` in the wire function name)
        is the internal-tool namespace and should be dispatched in-process.

        Class-level so callers can short-circuit on the predicate without
        constructing a router instance — relevant for any code path that
        invokes ``_call_plugin`` with a non-``int`` name and shouldn't pay
        for tool-registry construction.
        """
        return plugin_name == cls._NAMESPACE

    def pop_toggle_keys(self, params: dict[str, Any]) -> dict[str, bool]:
        """Lift every ``int:*`` toggle key out of ``params`` in place.

        Returns ``{prefixed_name: bool(value)}`` for each key removed.
        Mutates ``params`` so the toggles never reach the wire — provider
        APIs would reject them as unknown top-level fields.
        """
        return {k: bool(params.pop(k)) for k in list(params) if k.startswith(self._PREFIX)}

    def get_callable(self, prefixed_name: str) -> CallableTool | None:
        """Return a ``CallableTool`` for injection into ``payload["tools"]``.

        ``prefixed_name`` is the param-mapping key (e.g. ``"int:web_search"``).
        The returned ``CallableTool`` uses ``name=_NAMESPACE`` and
        ``op=<bare>`` so that ``inject_tool_payload`` produces the wire
        name ``int__<bare>`` with the standard synthetic ``op``
        discriminator — byte-identical in shape to a plugin.

        Returns ``None`` for unknown / non-prefixed names so callers
        (e.g. ``_build_tool_context``) can skip cleanly.
        """
        bare = self._strip_prefix(prefixed_name)
        if bare is None:
            return None
        tool = self._tools.get(bare)
        if tool is None:
            return None
        return CallableTool(
            name=self._NAMESPACE,
            op=bare,
            plugin=None,
            schema=tool.parameter_schema(),
            title=tool.description,
        )

    async def execute(self, bare_op: str, args: dict[str, Any]) -> tuple[str, bool, Decimal]:
        """Dispatch a tool call. ``bare_op`` is the operation name parsed
        out of the wire function name (e.g. ``"web_search"`` from
        ``"int__web_search"``). Returns ``(content, is_error, cost)``.

        ``is_error`` is the authoritative success/failure signal. Unknown
        tools and caught exceptions return ``True`` with zero cost.
        Successful tool runs forward whatever the tool itself returned —
        so a tool that legitimately costs nothing (e.g. calculator) can
        still be recorded as a success.

        Never raises; a misbehaving tool produces a structured error
        string instead of crashing a conversation turn.
        """
        tool = self._tools.get(bare_op)
        if tool is None:
            return (f"unknown internal tool: {self._NAMESPACE}__{bare_op}", True, Decimal("0"))
        try:
            return await tool.execute(args)
        except Exception as exc:
            # Log with traceback for ops; return a model-readable string so
            # the conversation continues instead of crashing the turn.
            logger.exception("internal tool %s__%s failed", self._NAMESPACE, bare_op)
            return (f"{self._NAMESPACE}__{bare_op} failed: {exc}", True, Decimal("0"))

    def wire_name(self, bare_op: str) -> str:
        """Build the wire-format function name (``int__<bare_op>``).

        Used for ``llm_usage.request_metadata.tool_name`` and other
        callers that need to name the dispatched tool without reaching
        for ``_NAMESPACE`` directly.
        """
        return f"{self._NAMESPACE}__{bare_op}"

    def _strip_prefix(self, prefixed_name: str) -> str | None:
        if not prefixed_name.startswith(self._PREFIX):
            return None
        return prefixed_name[len(self._PREFIX) :]
