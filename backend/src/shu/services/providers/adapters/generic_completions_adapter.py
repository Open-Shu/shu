from typing import Any, Dict

from shu.core.logging import get_logger

from ..adapter_base import ProviderCapabilities, ProviderInformation, register_adapter
from .completions_adapter import CompletionsAdapter
from ..parameter_definitions import IntegerParameter, NumberParameter

logger = get_logger(__name__)


class GenericCompletionsAdapter(CompletionsAdapter):
    """Generic adapter for all providers that are OpenAI completions compatible."""

    def get_provider_information(self) -> ProviderInformation:
        return ProviderInformation(key="generic_completions", display_name="Generic Completions")

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tools=True, vision=True)

    def get_api_base_url(self) -> str:
        return "http://localhost:11434/v1"

    def get_authorization_header(self) -> Dict[str, Any]:
        return {"scheme": "bearer", "headers": {"Authorization": f"Bearer {self.api_key}"}}

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


register_adapter("generic_completions", GenericCompletionsAdapter)
