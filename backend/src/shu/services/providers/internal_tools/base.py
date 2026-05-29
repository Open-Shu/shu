"""Base class for framework-internal tools (SHU-816)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any, ClassVar


class InternalTool(ABC):
    """Common shape for framework-internal tools.

    Subclasses declare their bare identity via the class attributes
    ``name`` and ``description``, the argument schema via
    ``parameter_schema``, and the in-process work via ``execute``. The
    ``int:`` prefix is NOT part of ``name`` — the router owns that
    namespace and prepends/strips it at the boundary.
    """

    # Subclasses MUST set these.
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Validate the subclass implementation."""
        super().__init_subclass__(**kwargs)
        # Skip validation for intermediate abstract classes that haven't
        # set a name yet — concrete subclasses that forget will fail at
        # router-construction time (empty key collisions).
        if not cls.name:
            return
        # Fail fast on shapes that collide with the routing prefix or the
        # plugin-operation separator.
        if ":" in cls.name:
            raise TypeError(
                f"{cls.__name__}.name = {cls.name!r}: internal tool names must be "
                f"bare; the `int:` prefix is owned by InternalToolRouter."
            )
        if "__" in cls.name:
            raise TypeError(
                f"{cls.__name__}.name = {cls.name!r}: internal tool names must not "
                f"contain `__` — that's the plugin-operation separator."
            )

    @abstractmethod
    def parameter_schema(self) -> dict[str, Any]:
        """Return the JSON Schema describing the tool's arguments."""

    @abstractmethod
    async def execute(self, args: dict[str, Any]) -> tuple[str, bool, Decimal]:
        """Run the tool. Return ``(content, is_error, cost)``.

        ``content`` is the string the model sees as the tool-role message
        — error strings, formatted results, etc.

        ``is_error`` is the authoritative success/failure signal. Kept
        separate from ``cost`` so a tool that legitimately costs zero (a
        free successful call — calculator, current_time, etc.) is still
        recorded as a success. Inferring success from cost would mis-
        record those rows as failures and dump their output into
        ``llm_usage.error_message``.

        ``cost`` is the USD cost of this specific invocation. Tools without
        a metered upstream return ``Decimal("0")``; tools whose upstream
        bills per call return the per-call rate; tools whose upstream
        bills per unit return the rate * units. Failed calls always
        return ``Decimal("0")`` regardless of the tool's configured rate.
        """
