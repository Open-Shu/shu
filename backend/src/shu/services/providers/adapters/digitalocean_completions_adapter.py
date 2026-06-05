from typing import Any

import jmespath

from shu.core.logging import get_logger

from ..adapter_base import (
    ProviderCapabilities,
    ProviderEventResult,
    ProviderInformation,
    ProviderReasoningDeltaEventResult,
    register_adapter,
)
from ..parameter_definitions import (
    BooleanParameter,
    EnumParameter,
    IntegerParameter,
    NumberParameter,
    Option,
)
from .completions_adapter import CompletionsAdapter

logger = get_logger(__name__)


class DigitalOceanCompletionsAdapter(CompletionsAdapter):
    """Adapter for DigitalOcean's /v1/chat/completions endpoint."""

    def get_provider_information(self) -> ProviderInformation:
        return ProviderInformation(
            key="digitalocean_completions",
            display_name="DigitalOcean (Completions)",
        )

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tools=True, vision=True)

    def supports_native_documents(self) -> bool:
        # DO has not documented the OpenAI `type: file` PDF attachment
        # shape; the base class falls back to text extraction.
        return False

    def get_api_base_url(self) -> str:
        return "https://inference.do-ai.run/v1"

    async def handle_provider_event(self, chunk: dict[str, Any]) -> ProviderEventResult | None:
        # `reasoning_content` is a DeepSeek-style convention (DeepSeek-R1,
        # Groq, OpenRouter routes, DO) — not part of OpenAI's chat-completions
        # canon, so the shared CompletionsAdapter ignores it. We surface it
        # as a reasoning delta and delegate the rest to the base class.
        reasoning = jmespath.search(
            "object == 'chat.completion.chunk' && choices[*].delta.reasoning_content | [0]",
            chunk,
        )
        if reasoning:
            return ProviderReasoningDeltaEventResult(content=reasoning)
        return await super().handle_provider_event(chunk)

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
            # DO deprecated `max_tokens` in favor of `max_completion_tokens`
            # (which also counts reasoning tokens against the budget).
            # Default when unset is ~2048 — usually enough, but reasoning
            # models with `reasoning_effort` set can exhaust that on a
            # single tool-call turn. Raise to 4096+ if you see truncated
            # tool calls or `finish_reason=length`.
            "max_completion_tokens": IntegerParameter(
                min=1,
                label="Max Completion Tokens",
                description=(
                    "Hard cap on output tokens (including reasoning tokens). "
                    "DO defaults to ~2048 when unset. Reasoning models may "
                    "need 4096+ for tool-use turns."
                ),
            ),
            "reasoning_effort": EnumParameter(
                label="Reasoning Effort",
                description="Hint for how much reasoning the model should perform. Silently ignored by models DO has not enabled reasoning for.",
                options=[
                    Option(value="low", label="Low"),
                    Option(value="medium", label="Medium"),
                    Option(value="high", label="High"),
                ],
            ),
            "parallel_tool_calls": BooleanParameter(
                label="Parallel Tool Calls",
                description="Allow the model to emit multiple tool calls in a single turn.",
                default=True,
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


register_adapter("digitalocean_completions", DigitalOceanCompletionsAdapter)
