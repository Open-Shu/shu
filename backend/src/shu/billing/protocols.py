"""Protocol interfaces for billing integration.

These protocols define the data structures used by the billing module.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class UsageRecord(Protocol):
    """A single usage record for billing purposes."""

    @property
    def timestamp(self) -> datetime:
        """When this usage occurred."""
        ...

    @property
    def model_id(self) -> str:
        """LLM model identifier (e.g., 'claude-haiku-4-5')."""
        ...

    @property
    def input_tokens(self) -> int:
        """Number of input/prompt tokens consumed."""
        ...

    @property
    def output_tokens(self) -> int:
        """Number of output/completion tokens consumed."""
        ...

    @property
    def cost_usd(self) -> float:
        """Calculated cost in USD based on model pricing."""
        ...

    @property
    def usage_type(self) -> str:
        """Type of usage: 'chat', 'profiling', 'side_call', etc."""
        ...


class UsageSummary(Protocol):
    """Aggregated usage data for a billing period."""

    @property
    def total_input_tokens(self) -> int:
        """Total input tokens across all models."""
        ...

    @property
    def total_output_tokens(self) -> int:
        """Total output tokens across all models."""
        ...

    @property
    def total_cost_usd(self) -> float:
        """Total cost in USD."""
        ...

    @property
    def by_model(self) -> dict[str, ModelUsage]:
        """Usage broken down by model ID."""
        ...


class ModelUsage(Protocol):
    """Usage data for a single model."""

    @property
    def model_id(self) -> str:
        ...

    @property
    def input_tokens(self) -> int:
        ...

    @property
    def output_tokens(self) -> int:
        ...

    @property
    def cost_usd(self) -> float:
        ...

    @property
    def request_count(self) -> int:
        ...
