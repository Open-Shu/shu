from typing import Any

from shu.core.logging import get_logger

from ..adapter_base import (
    ProviderCapabilities,
    ProviderInformation,
    register_adapter,
)
from ..parameter_definitions import (
    ArrayParameter,
    BooleanParameter,
    EnumParameter,
    InputField,
    IntegerParameter,
    NumberParameter,
    ObjectParameter,
    Option,
    StringParameter,
)
from .responses_adapter import ResponsesAdapter

logger = get_logger(__name__)


class OpenAIAdapter(ResponsesAdapter):
    # General provider information
    def get_provider_information(self) -> ProviderInformation:
        return ProviderInformation(
            key="openai",
            display_name="OpenAI",
        )

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tools=True, vision=True)

    def get_api_base_url(self) -> str:
        return "https://api.openai.com/v1"

    def get_parameter_mapping(self) -> dict[str, Any]:
        return {
            "temperature": NumberParameter(
                min=0,
                max=2,
                default=0.7,
                label="Temperature",
                description=(
                    "Controls randomness in the model's output. Lower values are more deterministic, higher values are more creative."
                ),
            ),
            "top_p": NumberParameter(
                min=0,
                max=1,
                default=1.0,
                label="Top P",
                description=(
                    "Nucleus sampling: the model considers tokens whose cumulative probability is <= top_p. Set to 1.0 to disable."
                ),
            ),
            "max_output_tokens": IntegerParameter(
                min=1,
                label="Max Output Tokens",
                description=("Hard cap on the number of tokens the model can generate in this response."),
            ),
            "top_logprobs": IntegerParameter(
                min=0,
                max=20,
                label="Top Logprobs",
                description=(
                    "Number of top token candidates to return log probabilities for. Set to 0 (or leave unset) to disable."
                ),
            ),
            "reasoning": ObjectParameter(
                label="Reasoning",
                description=(
                    "Reasoning behavior presets (used by reasoning-capable models). Configure summary verbosity and effort level."
                ),
                properties={
                    "summary": EnumParameter(
                        label="Reasoning summary level",
                        description="How detailed the reasoning summary should be.",
                        options=[
                            Option(value="concise", label="Concise"),
                            Option(value="detailed", label="Detailed"),
                        ],
                        default="concise",
                    ),
                    "effort": EnumParameter(
                        label="Reasoning effort",
                        description="How much effort the model should spend on reasoning.",
                        options=[
                            Option(value="none", label="None"),
                            Option(value="minimal", label="Minimal"),
                            Option(value="low", label="Low"),
                            Option(value="medium", label="Medium"),
                            Option(value="high", label="High"),
                            Option(value="xhigh", label="Extra high"),
                        ],
                        default="medium",
                    ),
                },
            ),
            "text": ObjectParameter(
                label="Text Output",
                description=(
                    "Text output configuration, including verbosity / structured output behavior. Applied to the text the model returns."
                ),
                properties={
                    "verbosity": EnumParameter(
                        label="Model verbosity setting",
                        description="Constrains how verbose the model's response should be. Low = concise, High = more detailed answers.",
                        options=[
                            Option(value="low", label="Low"),
                            Option(value="medium", label="Medium"),
                            Option(value="high", label="High"),
                        ],
                        default="medium",
                    )
                },
            ),
            "tools": ArrayParameter(
                label="Tools",
                description="Built-in tools to enable for this response.",
                options=[
                    Option(
                        value={"type": "web_search"},
                        label="Web Search",
                        help="Allow the model to call the built-in web_search tool.",
                        input_schema=ObjectParameter(
                            label="Web Search Configuration",
                            description="Optional configuration for the web_search tool.",
                            properties={
                                "filters": ObjectParameter(
                                    label="Filters",
                                    description=(
                                        "Optional provider-specific filters for web search (e.g. domains, date ranges)."
                                    ),
                                ),
                                "search_context_size": EnumParameter(
                                    label="Search Context Size",
                                    description=("How much retrieved web content is fed back to the model."),
                                    options=[
                                        Option(value="low", label="Low"),
                                        Option(value="medium", label="Medium"),
                                        Option(value="high", label="High"),
                                    ],
                                    default="medium",
                                ),
                                "user_location": ObjectParameter(
                                    label="User Location",
                                    description="Optional structured location information to bias results toward the user's region.",
                                ),
                            },
                        ),
                    ),
                    Option(
                        value={"type": "code_interpreter"},
                        label="Code Interpreter",
                        help="Allow the model to call the built-in Python code interpreter tool.",
                    ),
                ],
            ),
            "max_tool_calls": IntegerParameter(
                min=1,
                label="Max Tool Calls",
                description=(
                    "Maximum number of tool calls the model is allowed to make in its response. Further tool calls are suppressed once this limit is reached."
                ),
            ),
            "parallel_tool_calls": BooleanParameter(
                label="Parallel Tool Calls",
                default=True,
                description=("If enabled, the model may request multiple tool calls in parallel during a single step."),
            ),
            "tool_choice": EnumParameter(
                label="Tool Choice",
                description=(
                    "Controls if and how tools are used. 'Auto' lets the model decide. 'None' disables tools. 'Specific function' forces a particular function by name."
                ),
                options=[
                    Option(
                        value="auto",
                        label="Auto",
                        help="Model decides whether and which tools to call.",
                    ),
                    Option(
                        value="none",
                        label="None",
                        help="Disable all tool calls for this response.",
                    ),
                    Option(
                        value={"type": "function", "name": ""},
                        label="Specific function (set name)",
                        help=(
                            "Force the model to call a specific function. The function must also be present in the 'tools' array."
                        ),
                        input_fields=[
                            InputField(
                                path="name",
                                type="string",
                                label="Function name",
                                required=True,
                            )
                        ],
                    ),
                ],
            ),
            "metadata": ObjectParameter(
                label="Metadata",
                description=(
                    "Arbitrary JSON object stored with the response. Up to 16 keys; keys up to 64 chars, values up to 512 chars."
                ),
            ),
            "truncation": EnumParameter(
                label="Truncation",
                description=(
                    "How to handle inputs that would exceed the model's context window. Disabled = error; Auto = drop oldest items to fit."
                ),
                options=[
                    Option(value="disabled", label="Disabled"),
                    Option(value="auto", label="Auto"),
                ],
                default="disabled",
            ),
            "prompt_cache_key": StringParameter(
                label="Prompt Cache Key",
                description=(
                    "Stable identifier for prompt caching. Use the same key for identical prompts to reuse cached results."
                ),
                placeholder="e.g. user123:invoice_extraction:v1",
            ),
            "prompt_cache_retention": StringParameter(
                label="Prompt Cache Retention",
                default="24h",
                description=(
                    "How long the prompt cache entry can be retained (e.g. '1h', '24h', '7d'), subject to platform limits."
                ),
                placeholder="e.g. 24h",
            ),
            "service_tier": EnumParameter(
                label="Service Tier",
                description=(
                    "Requested serving tier for this request. Auto uses the project default; Priority tiers may have higher cost."
                ),
                options=[
                    Option(value="auto", label="Auto"),
                    Option(value="default", label="Default"),
                    Option(value="flex", label="Flex"),
                    Option(value="priority", label="Priority"),
                ],
                default="auto",
            ),
        }


register_adapter("openai", OpenAIAdapter)
