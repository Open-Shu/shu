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

    - ``PREFIX = "int:"`` — the **user-facing** prefix on parameter-mapping
      keys (e.g. ``"int:web_search": BooleanParameter(...)``). Persists on
      ``ModelConfiguration.parameter_overrides``. Never goes on the wire.
    - ``NAMESPACE = "int"`` — the **wire-format** plugin name. Every
      internal tool becomes an "op" under this virtual plugin, so the
      function name the model sees is ``int__<bare_tool_name>`` — exactly
      the same shape as a plugin (``gmail_digest__list``). Models truncate
      tool-call argument streaming when they encounter unfamiliar function-
      name shapes; matching the plugin convention fixes that.

    The router translates between the two: user-facing key
    ``"int:web_search"`` → bare op ``"web_search"`` → wire-format function
    name ``"int__web_search"`` (built by ``inject_tool_payload`` from
    ``name="int"`` + ``op="web_search"``).
    """

    PREFIX = "int:"  # user-facing param-mapping key prefix
    NAMESPACE = "int"  # wire-format plugin-name; also the dispatch key

    def __init__(self, settings: Settings) -> None:
        # Bare-name keyed map. Both the prefix and the namespace are wire
        # concerns — never a storage concern.
        self._tools: dict[str, InternalTool] = {
            "web_search": WebSearchTool(
                api_key=settings.brave_search_api_key,
                cost_per_query=settings.brave_search_cost_per_query,
            ),
        }

    def get_callable(self, prefixed_name: str) -> CallableTool | None:
        """Return a ``CallableTool`` for injection into ``payload["tools"]``.

        ``prefixed_name`` is the param-mapping key (e.g. ``"int:web_search"``).
        The returned ``CallableTool`` uses ``name=NAMESPACE`` and
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
            name=self.NAMESPACE,
            op=bare,
            plugin=None,
            schema=tool.parameter_schema(),
            title=tool.description,
        )

    async def execute(self, bare_op: str, args: dict[str, Any]) -> tuple[str, Decimal]:
        """Dispatch a tool call. ``bare_op`` is the operation name parsed
        out of the wire function name (e.g. ``"web_search"`` from
        ``"int__web_search"``). Returns ``(content, cost)`` — the model-
        readable result string plus the per-call USD cost the tool
        reported. Unknown tools and runtime failures return
        ``Decimal("0")`` for cost: we record the call as a failure row
        but don't bill the user for an attempt that produced no data.

        Never raises; returns a structured error string on unknown
        tool / runtime failure so a misbehaving tool never crashes a
        conversation turn.
        """
        tool = self._tools.get(bare_op)
        if tool is None:
            return (f"unknown internal tool: {self.NAMESPACE}__{bare_op}", Decimal("0"))
        try:
            return await tool.execute(args)
        except Exception as exc:
            # Log with traceback for ops; return a model-readable string so
            # the conversation continues instead of crashing the turn.
            logger.exception("internal tool %s__%s failed", self.NAMESPACE, bare_op)
            return (f"{self.NAMESPACE}__{bare_op} failed: {exc}", Decimal("0"))

    def _strip_prefix(self, prefixed_name: str) -> str | None:
        if not prefixed_name.startswith(self.PREFIX):
            return None
        return prefixed_name[len(self.PREFIX) :]
