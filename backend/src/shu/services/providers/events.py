from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ProviderStreamEvent:
    """Normalized chat completion stream event."""

    type: Literal["content_delta", "reasoning_delta", "function_call", "final_message", "error"]
    model_name: str
    provider_name: str
    content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
