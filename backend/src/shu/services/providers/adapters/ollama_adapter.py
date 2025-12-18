from typing import Any, Dict

from shu.core.logging import get_logger

from ..adapter_base import ProviderCapabilities, ProviderInformation, register_adapter
from .completions_adapter import CompletionsAdapter
from ..parameter_definitions import IntegerParameter, NumberParameter

logger = get_logger(__name__)


class OllamaAdapter(CompletionsAdapter):
    """Adapter for Ollama's OpenAI-compatible chat completions endpoint."""

    def get_provider_information(self) -> ProviderInformation:
        return ProviderInformation(key="ollama", display_name="Ollama")

    def get_capabilities(self) -> ProviderCapabilities:
        # Ollama supports streaming; tool calling support varies, keep disabled by default.
        return ProviderCapabilities(streaming=True, tools=True, vision=True)

    def supports_native_documents(self) -> bool:
        """Ollama does not support native file uploads."""
        return False

    def get_api_base_url(self) -> str:
        return "http://localhost:11434/v1"

    def get_authorization_header(self) -> Dict[str, Any]:
        # Local default: no auth header.
        return {"scheme": None, "headers": {}}

    def get_parameter_mapping(self) -> Dict[str, Any]:
        return {
            "temperature": NumberParameter(
                min=0,
                max=2,
                default=0.7,
                label="Temperature",
                description="Controls randomness; lower = deterministic, higher = more creative.",
            ),
            "top_p": NumberParameter(
                min=0,
                max=1,
                default=1.0,
                label="Top P",
                description="Nucleus sampling cutoff; set to 1.0 to disable.",
            ),
            "max_tokens": IntegerParameter(
                min=1,
                label="Max Tokens",
                description="Hard cap on output tokens generated.",
            ),
        }


register_adapter("ollama", OllamaAdapter)
