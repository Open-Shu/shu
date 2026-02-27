"""Result envelope types for Shu plugin execute() return values.

Plugin authors import from here instead of defining their own shims:

    from shu_plugin_sdk import PluginResult, SkipReason, Skip

Typical usage::

    return PluginResult.ok(data={"items": results})
    return PluginResult.err("Provider rate limit exceeded", code="rate_limit")
    return PluginResult.ok(data={...}).with_skips([
        Skip(id=doc_id, reason=SkipReason.too_large, details={"size": size}),
    ])

For ingestion plugins that commonly produce skips, ``IngestionResult`` adds a
``skips`` parameter directly to ``ok()``::

    return IngestionResult.ok(data={"indexed": 42}, skips=skip_list)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class SkipReason(str, Enum):
    """Contract-defined reasons a plugin may skip an item during ingestion."""

    too_large = "too_large"
    ext_filtered = "ext_filtered"
    unsupported_format = "unsupported_format"
    empty_extraction = "empty_extraction"
    auth = "auth"
    network = "network"
    other = "other"


@dataclass
class Skip:
    """Structured skip entry matching the PLUGIN_CONTRACT skips[] schema."""

    id: str
    reason: SkipReason | str
    name: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        reason = self.reason.value if isinstance(self.reason, SkipReason) else str(self.reason)
        d: dict[str, Any] = {"id": self.id, "reason": reason}
        if self.name is not None:
            d["name"] = self.name
        if self.details is not None:
            d["details"] = self.details
        return d


class PluginResult:
    """Canonical result type for Shu plugin ``execute()`` return values.

    Matches the full PLUGIN_CONTRACT result shape::

        {
            "status": "success" | "error" | "timeout",
            "data": {...},
            "error": {"code": ..., "message": ..., "details": {...}} | null,
            "cost": {"tokens": ..., "api_calls": ...} | null,
            "diagnostics": [...] | null,
            "skips": [{...}] | null,
            "citations": [{...}] | null,
        }

    Prefer the classmethods ``ok()`` and ``err()`` over direct construction.
    """

    def __init__(
        self,
        status: str,
        data: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        cost: dict[str, Any] | None = None,
        diagnostics: list[str] | None = None,
        skips: list[dict[str, Any]] | None = None,
        citations: list[dict[str, Any]] | None = None,
    ) -> None:
        self.status = status
        self.data = data if data is not None else {}
        self.error = error
        self.cost = cost
        self.diagnostics = diagnostics
        self.skips = skips
        self.citations = citations

    @classmethod
    def ok(
        cls,
        data: dict[str, Any] | None = None,
        cost: dict[str, Any] | None = None,
        diagnostics: list[str] | None = None,
        citations: list[dict[str, Any]] | None = None,
    ) -> "PluginResult":
        """Return a successful result."""
        return cls("success", data=data or {}, cost=cost, diagnostics=diagnostics, citations=citations)

    @classmethod
    def err(
        cls,
        message: str,
        code: str = "tool_error",
        details: dict[str, Any] | None = None,
    ) -> "PluginResult":
        """Return an error result."""
        return cls(
            "error",
            data={},
            error={"code": code, "message": message, "details": details or {}},
        )

    def with_skips(self, skips: list[Skip | dict[str, Any]]) -> "PluginResult":
        """Attach skips to this result and return self (fluent builder).

        Accepts ``Skip`` dataclass instances or raw dicts.
        """
        self.skips = [s.to_dict() if isinstance(s, Skip) else s for s in skips]
        return self

    def __repr__(self) -> str:
        return f"PluginResult(status={self.status!r}, data={self.data!r})"


class IngestionResult(PluginResult):
    """``PluginResult`` subtype for ingestion-style operations that produce skips.

    Adds ``skips`` as a first-class parameter on ``ok()`` so ingestion plugins
    don't have to call ``.with_skips()`` separately::

        return IngestionResult.ok(data={"indexed": n}, skips=skip_list)
    """

    @classmethod
    def ok(  # type: ignore[override]
        cls,
        data: dict[str, Any] | None = None,
        skips: list[Skip | dict[str, Any]] | None = None,
        cost: dict[str, Any] | None = None,
        diagnostics: list[str] | None = None,
        citations: list[dict[str, Any]] | None = None,
    ) -> "IngestionResult":
        """Return a successful ingestion result, optionally with skips."""
        result = cls("success", data=data or {}, cost=cost, diagnostics=diagnostics, citations=citations)
        if skips:
            result.skips = [s.to_dict() if isinstance(s, Skip) else s for s in skips]
        return result
