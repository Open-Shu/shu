from typing import Any

import jmespath

from shu.core.logging import get_logger

from ..adapter_base import (
    ProviderCapabilities,
    ProviderContentDeltaEventResult,
    ProviderInformation,
    register_adapter,
)
from ..parameter_definitions import (
    ArrayParameter,
    BooleanParameter,
    EnumParameter,
    IntegerParameter,
    NumberParameter,
    Option,
    StringParameter,
)
from .completions_adapter import CompletionsAdapter

logger = get_logger(__name__)


class PerplexityAdapter(CompletionsAdapter):
    """Adapter for Perplexity chat completions (OpenAI-compatible)."""

    citations = []

    def _get_citations_markdown(self) -> str:
        citations = ""
        if not self.citations:
            return ""

        for index, citation in enumerate(self.citations, start=1):
            citations += f"\n\n[{index}] {citation}"

        self.citations = []
        return f"\n\n**Citations**\n{citations}"

    def get_provider_information(self) -> ProviderInformation:
        return ProviderInformation(key="perplexity", display_name="Perplexity")

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tools=False, vision=False)

    def supports_native_documents(self) -> bool:
        """Perplexity API does not support native document uploads."""
        return False

    def get_api_base_url(self) -> str:
        return "https://api.perplexity.ai"

    def get_authorization_header(self) -> dict[str, Any]:
        return {"scheme": "bearer", "headers": {"Authorization": f"Bearer {self.api_key}"}}

    def get_finish_reason_path(self):
        return "object == 'chat.completion.done' && choices[*].finish_reason | [0]"

    async def handle_provider_event(self, chunk):
        self.citations = jmespath.search("object == 'chat.completion.done' && citations", chunk)
        return await super().handle_provider_event(chunk)

    async def finalize_provider_events(self):
        res = []
        citations = self._get_citations_markdown()
        self._stream_content.append(citations)
        if self.citations:
            res.append(ProviderContentDeltaEventResult(content=citations))

        remaining_events = await super().finalize_provider_events()

        return res + remaining_events

    def get_parameter_mapping(self) -> dict[str, Any]:
        return {
            # --- Sampling / generation controls ---
            "temperature": NumberParameter(
                min=0,
                max=2,
                default=0.7,
                label="Temperature",
                description="Controls randomness in the model's output (0–2). Lower = more deterministic, higher = more creative.",
            ),
            "top_p": NumberParameter(
                min=0,
                max=1,
                default=1.0,
                label="Top P",
                description="Nucleus sampling (0–1). The model considers only the smallest set of tokens whose cumulative probability ≥ top_p.",
            ),
            "top_k": IntegerParameter(
                min=1,
                label="Top K",
                description="Optional top-k sampling. Restricts generation to the k most likely tokens at each step. Leave unset to use Perplexity defaults.",
            ),
            "max_tokens": IntegerParameter(
                min=1,
                label="Max Tokens",
                description="Maximum number of tokens the model can generate in this response (output tokens only).",
            ),
            # --- Search / grounding controls (Perplexity-specific) ---
            "search_mode": EnumParameter(
                label="Search Mode",
                description="Controls which search backend to use. 'web' = general web search (default). 'academic' = academic / scholarly-leaning sources.",
                options=[
                    Option(value="web", label="Web"),
                    Option(value="academic", label="Academic"),
                ],
                default="web",
            ),
            "search_recency_filter": EnumParameter(
                label="Search Recency Filter",
                description="Filter web results by recency. If unset, Perplexity chooses automatically.",
                options=[
                    Option(value="day", label="Past day"),
                    Option(value="week", label="Past week"),
                    Option(value="month", label="Past month"),
                    Option(value="year", label="Past year"),
                ],
            ),
            "search_domain_filter": ArrayParameter(
                label="Search Domain Filter",
                description="Limit or exclude specific domains. Exact semantics follow Perplexity's API (e.g. restricting to or excluding certain sites).",
                items=StringParameter(
                    label="Domain",
                    placeholder="e.g. nature.com or -example.com",
                ),
            ),
            "return_images": BooleanParameter(
                label="Return Images",
                description="If true, include image URLs in the response when relevant.",
                default=False,
            ),
            "return_related_questions": BooleanParameter(
                label="Return Related Questions",
                description="If true, include suggested related questions alongside the answer.",
                default=False,
            ),
            "disable_search": BooleanParameter(
                label="Disable Search",
                description="If true, disables Perplexity’s web search and forces pure LLM generation from the prompt and context only.",
                default=False,
            ),
            "enable_search_classifier": BooleanParameter(
                label="Enable Search Classifier",
                description="If true, enables automatic classification of which queries should trigger search vs. pure LLM generation.",
                default=True,
            ),
            "reasoning_effort": EnumParameter(
                label="Reasoning Effort",
                description="Optional hint for how much reasoning the model should perform. Support may vary by model.",
                options=[
                    Option(value="low", label="Low"),
                    Option(value="medium", label="Medium"),
                    Option(value="high", label="High"),
                ],
            ),
        }


register_adapter("perplexity", PerplexityAdapter)
