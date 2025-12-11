from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional


@dataclass
class ProviderStreamEvent:
    """Normalized chat completion stream event."""

    type: Literal["content_delta", "reasoning_delta", "function_call", "final_message", "error"]
    model_name: str
    provider_name: str
    content: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
