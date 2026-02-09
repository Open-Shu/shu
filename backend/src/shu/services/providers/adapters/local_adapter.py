from typing import Any

from shu.services.providers.adapter_base import (
    BaseProviderAdapter,
    ChatContext,
    ProviderCapabilities,
    ProviderEventResult,
    ProviderInformation,
    register_adapter,
)


class LocalAdapter(BaseProviderAdapter):
    # General provider information
    def get_provider_information(self) -> ProviderInformation:
        return ProviderInformation(
            key="Local",
            display_name="Local (for tests)",
        )

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tools=False, vision=False)

    def get_api_base_url(self) -> str:
        return "https://localhost"

    def get_chat_endpoint(self) -> str:
        return "/responses"

    def get_models_endpoint(self) -> str:
        return "/models"

    def get_authorization_header(self) -> dict[str, Any]:
        return {"scheme": "bearer", "headers": {"Authorization": f"Bearer {self.api_key}"}}

    def get_parameter_mapping(self) -> dict[str, Any]:
        return {
            "temperature": {"type": "number"},
            "reasoning": {"type": "object"},
            "text": {"type": "object"},
            "tools": {"type": "array"},
        }

    async def set_messages_in_payload(self, messages: ChatContext, payload: dict[str, Any]) -> dict[str, Any]:
        payload["messages"] = self._flatten_chat_context(messages)
        return payload

    async def handle_provider_event(self, chunk: dict[str, Any]) -> ProviderEventResult | None:
        pass

    async def finalize_provider_events(self) -> list[ProviderEventResult]:
        pass

    async def handle_provider_completion(self, data: dict[str, Any]) -> list[ProviderEventResult]:
        pass


register_adapter("local", LocalAdapter)
