"""Cookie-cutter EchoPlugin — copy this file and rename to your plugin.

This template demonstrates the recommended plugin structure including both
``get_schema()`` (for backward compatibility) and ``get_schema_for_op()``
(the forward-compatible per-op interface).

It intentionally exercises two common host capabilities so the bundled tests
show real ``FakeHostBuilder`` patterns:
  - ``echo`` op  — uses ``host.log`` (simple, no external calls)
  - ``fetch`` op — reads an API key from ``host.secrets`` then calls
                   ``host.http.fetch()``, showing the secret + HTTP pattern

Steps to create your own plugin from this template:
1. Copy the entire ``_cookiecutter/`` directory to ``plugins/<your_plugin>/``
2. Rename ``EchoPlugin`` to ``<YourPlugin>``
3. Update ``manifest.py`` (see the MUST UPDATE comments there)
4. Replace the ``echo`` / ``fetch`` ops with your own ops
5. Implement ``execute()`` using ``host.*`` capabilities
6. Run ``pytest test_plugin.py`` — the contract check runs automatically
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Minimal result shim — no shu.* imports required in plugin code.
# The real Executor wraps your return value; using this shim keeps the plugin
# decoupled from the Shu backend and unit-testable in isolation.
# ---------------------------------------------------------------------------

class _Result:
    """Minimal stand-in for PluginResult used within this standalone template.

    The Shu Executor accepts any object with ``status`` and ``data`` attributes.
    """

    def __init__(self, status: str = "success", data: dict[str, Any] | None = None) -> None:
        self.status = status
        self.data = data or {}


# ---------------------------------------------------------------------------
# Per-op schemas
#
# get_schema_for_op() is the forward-compatible interface that lets Shu
# validate each op's parameters precisely. It is intended to eventually
# replace get_schema() for new plugins. Implement both during the transition:
#   - get_schema()         → keeps existing Loader compatibility
#   - get_schema_for_op()  → enables per-op validation in future Shu versions
# ---------------------------------------------------------------------------

_OP_SCHEMAS: dict[str, dict[str, Any]] = {
    "echo": {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": ["echo"]},
            "message": {"type": "string", "description": "Text to echo back"},
        },
        "required": ["op"],
        "additionalProperties": False,
    },
    "fetch": {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": ["fetch"]},
            "url": {"type": "string", "description": "URL to fetch"},
        },
        "required": ["op", "url"],
        "additionalProperties": False,
    },
}


class EchoPlugin:
    """Minimal echo plugin — replace with your own implementation.

    Implements the Shu Plugin Protocol:
    - ``name`` and ``version`` class attributes
    - ``get_schema()`` — combined JSON Schema for all ops (Loader compatibility)
    - ``get_output_schema()`` — constrained output schema (prevents passthrough)
    - ``get_schema_for_op()`` — per-op schema (forward-compatible interface)
    - ``execute()`` — async handler
    """

    name: str = "echo_template"
    version: str = "1"

    def get_schema(self) -> dict[str, Any]:
        """Return the combined JSON Schema for all ops.

        This method keeps the plugin compatible with the current Shu Loader
        which uses ``properties.op.enum`` to discover available operations.
        New plugins should also implement ``get_schema_for_op()`` below.
        """
        return {
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    # MUST UPDATE: list all ops your plugin supports
                    "enum": ["echo", "fetch"],
                },
                "message": {
                    "type": "string",
                    "description": "Text to echo back (echo op)",
                },
                "url": {
                    "type": "string",
                    "description": "URL to fetch (fetch op)",
                },
            },
            "required": ["op"],
            "additionalProperties": False,
        }

    def get_output_schema(self) -> dict[str, Any]:
        """Return the JSON Schema for the ``data`` field of a successful result.

        Always define explicit ``properties`` and set ``additionalProperties: false``
        to prevent unbounded data from flowing into the LLM context.
        """
        return {
            "type": "object",
            "properties": {
                # MUST UPDATE: replace with your plugin's output fields
                "echo": {"type": "string", "description": "The echoed message (echo op)"},
                "status_code": {"type": "integer", "description": "HTTP status code (fetch op)"},
                "body": {"type": ["object", "string", "null"], "description": "Response body (fetch op)"},
            },
            "additionalProperties": False,
        }

    def get_schema_for_op(self, op_name: str) -> dict[str, Any] | None:
        """Return the JSON Schema for a specific op's parameters, or None.

        This is the forward-compatible replacement for ``get_schema()``.
        Shu will use this method to validate op-specific parameters precisely
        once support is rolled out. Implement it now to be ready.

        Args:
            op_name: The op name to look up. Return ``None`` for unknown ops.

        Returns:
            A valid JSON Schema dict describing the op's parameters, or ``None``
            if the op takes no parameters beyond ``op`` or is unknown.
        """
        # MUST UPDATE: populate _OP_SCHEMAS with your own op schemas
        return _OP_SCHEMAS.get(op_name)

    async def execute(
        self,
        params: dict[str, Any],
        context: Any,
        host: Any,
    ) -> _Result:
        """Execute the requested op and return a result.

        Args:
            params: Validated input parameters dict (always contains ``op``).
            context: Execution context (user_id, agent_key, etc.).
            host: Host capability object — use ``host.log``, ``host.http``,
                  ``host.secrets``, etc. to access platform services.

        Returns:
            A ``_Result`` with ``status="success"`` and a ``data`` dict whose
            shape matches ``get_output_schema()``.
        """
        op = params.get("op")

        if op == "echo":
            message = params.get("message", "")
            await host.log.info(f"EchoPlugin.execute: echoing {message!r}")
            return _Result(status="success", data={"echo": message})

        if op == "fetch":
            url = params["url"]
            # Read an API key from the secrets store — returns None if not set.
            api_key = await host.secrets.get("api_key")
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            await host.log.info(f"EchoPlugin.fetch: GET {url}")
            response = await host.http.fetch("GET", url, headers=headers)
            return _Result(
                status="success",
                data={
                    "status_code": response.get("status_code"),
                    "body": response.get("body"),
                },
            )

        # MUST UPDATE: add your own op handlers here
        return _Result(
            status="error",
            data={"error": f"Unknown op: {op!r}"},
        )
