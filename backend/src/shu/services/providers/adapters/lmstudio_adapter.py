from typing import Any

from shu.core.logging import get_logger

from ..adapter_base import ProviderCapabilities, ProviderInformation, register_adapter
from ..parameter_definitions import IntegerParameter, NumberParameter
from .completions_adapter import CompletionsAdapter

logger = get_logger(__name__)


class LMStudioAdapter(CompletionsAdapter):
    """Adapter for LM Studio's OpenAI-compatible Completions API."""

    def get_provider_information(self) -> ProviderInformation:
        return ProviderInformation(key="lm_studio", display_name="LM Studio")

    def get_capabilities(self) -> ProviderCapabilities:
        # LM Studio supports streaming and tool-calling compatible with OpenAI Completions.
        return ProviderCapabilities(streaming=True, tools=True, vision=True)

    def supports_native_documents(self) -> bool:
        """LM Studio does not support native file uploads."""
        return False

    def get_api_base_url(self) -> str:
        # Default local server; overrideable via provider config if needed.
        return "http://localhost:1234/v1"

    def get_authorization_header(self) -> dict[str, Any]:
        # No auth by default for local LM Studio server.
        return {"scheme": None, "headers": {}}

    def get_parameter_mapping(self) -> dict[str, Any]:
        # Minimal OpenAI-compatible knobs commonly supported by LM Studio.
        return {
            "temperature": NumberParameter(
                min=0,
                max=2,
                default=0.7,
                label="Temperature",
                description="Controls randomness; lower = more deterministic, higher = more creative.",
            ),
            "top_p": NumberParameter(
                min=0,
                max=1,
                default=1.0,
                label="Top P",
                description="Nucleus sampling cutoff; set to 1.0 to disable.",
            ),
            "max_output_tokens": IntegerParameter(
                min=1,
                label="Max Output Tokens",
                description="Hard cap on output tokens generated.",
            ),
        }


register_adapter("lm_studio", LMStudioAdapter)
