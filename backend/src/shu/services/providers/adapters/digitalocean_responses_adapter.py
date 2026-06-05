from typing import Any

from shu.core.logging import get_logger

from ..adapter_base import (
    ProviderCapabilities,
    ProviderInformation,
    register_adapter,
)
from ..parameter_definitions import (
    BooleanParameter,
    EnumParameter,
    IntegerParameter,
    NumberParameter,
    ObjectParameter,
    Option,
)
from .responses_adapter import ResponsesAdapter

logger = get_logger(__name__)


class DigitalOceanResponsesAdapter(ResponsesAdapter):
    """Adapter for DigitalOcean's /v1/responses endpoint."""

    def get_provider_information(self) -> ProviderInformation:
        return ProviderInformation(
            key="digitalocean",
            display_name="DigitalOcean",
        )

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tools=True, vision=True)

    def get_api_base_url(self) -> str:
        return "https://inference.do-ai.run/v1"

    def get_parameter_mapping(self) -> dict[str, Any]:
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
            "max_output_tokens": IntegerParameter(
                min=1,
                label="Max Output Tokens",
                description="Hard cap on the number of tokens the model can generate in this response.",
            ),
            "reasoning": ObjectParameter(
                label="Reasoning",
                description="Reasoning behavior for reasoning-capable models. Silently ignored otherwise.",
                properties={
                    "effort": EnumParameter(
                        label="Reasoning effort",
                        description="How much effort the model should spend on reasoning.",
                        options=[
                            Option(value="low", label="Low"),
                            Option(value="medium", label="Medium"),
                            Option(value="high", label="High"),
                        ],
                        default="medium",
                    ),
                    "summary": EnumParameter(
                        label="Reasoning summary level",
                        description="How detailed the reasoning summary should be.",
                        options=[
                            Option(value="concise", label="Concise"),
                            Option(value="detailed", label="Detailed"),
                        ],
                        default="concise",
                    ),
                },
            ),
            "parallel_tool_calls": BooleanParameter(
                label="Parallel Tool Calls",
                description="Allow the model to emit multiple tool calls in a single turn.",
                default=True,
            ),
            "tool_choice": EnumParameter(
                label="Tool Choice",
                description="Controls if and how tools are used. Auto lets the model decide; None disables tools.",
                options=[
                    Option(value="auto", label="Auto"),
                    Option(value="none", label="None"),
                ],
                default="auto",
            ),
            # SHU-816: the `int:` prefix on the key is both the routing
            # identifier and the toggle id — the framework lifts these
            # out of the payload before send and resolves them through
            # the InternalToolRouter. `label`/`description` carry the UI
            # text.
            "int:web_search": BooleanParameter(
                label="Web Search",
                description="Let the model search the web (via Brave Search) when answering.",
                default=False,
            ),
        }


register_adapter("digitalocean", DigitalOceanResponsesAdapter)
