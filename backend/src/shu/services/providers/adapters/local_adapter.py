from typing import Any, Dict, List
from shu.services.providers.adapter_base import BaseProviderAdapter, ProviderCapabilities, ProviderEventResult, ProviderInformation, register_adapter


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
    
    def get_authorization_header(self) -> Dict[str, Any]:
        return {
            "scheme": "bearer",
            "headers": {
                "Authorization": f"Bearer {self.api_key}"
            }
        }

    def get_parameter_mapping(self) -> Dict[str, Any]:
        return {
            "temperature": {"type": "number"},
            "reasoning": {"type": "object"},
            "text": {"type": "object"},
            "tools": {"type": "array"}
        }
    
    async def set_messages_in_payload(self, messages: List[Dict[str, str]], payload: Dict[str, Any]) -> Dict[str, Any]:
        payload["messages"] = messages
        return payload
    
    async def handle_provider_event(self, chunk: Dict[str, Any]) -> ProviderEventResult:
        pass

    async def finalize_provider_events(self) -> List[ProviderEventResult]:
        pass

    async def handle_provider_completion(self, data: Dict[str, Any]) -> List[ProviderEventResult]:
        pass


register_adapter("local", LocalAdapter)
