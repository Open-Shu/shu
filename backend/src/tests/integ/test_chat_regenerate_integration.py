from integ.helpers.api_helpers import process_streaming_result
from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script
from integ.response_utils import extract_data
from integ.expected_error_context import expect_test_suite_errors
from integ.helpers.auth import create_active_user_headers
import json
import asyncio
import uuid

PROVIDER_DATA = {
    "name": "Test Local Provider",
    "provider_type": "local",
    "api_endpoint": "endpoint",
    "is_active": True
}

MODEL_DATA = {
    "model_name": "local-echo",
    "display_name": "Local Echo Test Model",
    "description": "Local echo model for integration testing",
    "context_window": 8192,
    "max_tokens": 4096,
    "supports_streaming": True,
    "supports_functions": False,
    "supports_vision": False
}

MODEL_CONFIG_DATA = {
    "name": "Test Chat Assistant",
    "description": "Test model configuration for chat integration",
    "is_active": True,
    "created_by": "test-user",
    "knowledge_base_ids": []
}

class ChatRegenerateIntegrationTest(BaseIntegrationTestSuite):
    """Integration tests for chat message regeneration and variant lineage."""

    def get_suite_name(self) -> str:
        return "Chat Regenerate Integration"

    def get_suite_description(self) -> str:
        return "Validates regenerate endpoint (streaming and non-streaming) and message variant lineage."

    def get_test_functions(self):
        return [
            self.test_regenerate_creates_variant_and_links_lineage,
        ]


    async def _create_user_and_auth(self, client, admin_headers):
        # Delegates to shared helper to avoid per-test duplication
        return await create_active_user_headers(client, admin_headers)

    async def _create_conversation(self, client, admin_headers, user_headers):
        # Unique suffix to avoid name collisions
        suffix = uuid.uuid4().hex[:8]
        provider_payload = {**PROVIDER_DATA, "name": f"{PROVIDER_DATA['name']} {suffix}"}

        # Create provider with admin auth
        provider_response = await client.post("/api/v1/llm/providers", json=provider_payload, headers=admin_headers)
        print("DEBUG provider_response:", provider_response.status_code, provider_response.text)
        assert provider_response.status_code == 201, provider_response.text
        provider_id = extract_data(provider_response)["id"]

        # Create model under provider
        model_response = await client.post(f"/api/v1/llm/providers/{provider_id}/models", json=MODEL_DATA, headers=admin_headers)
        print("DEBUG model_response:", model_response.status_code, model_response.text)
        assert model_response.status_code == 200, model_response.text

        # Create model configuration with admin auth
        model_config_data = {
            **MODEL_CONFIG_DATA,
            "llm_provider_id": provider_id,
            "model_name": MODEL_DATA["model_name"]
        }
        config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=admin_headers)
        print("DEBUG config_response:", config_response.status_code, config_response.text)
        assert config_response.status_code == 201, config_response.text
        model_config_id = extract_data(config_response)["id"]

        # Create conversation with model config as the regular user
        resp = await client.post(
            "/api/v1/chat/conversations",
            json={"title": "Regen Test", "model_configuration_id": model_config_id},
            headers=user_headers
        )
        print("DEBUG create_conversation:", resp.status_code, resp.text)
        assert resp.status_code == 200, resp.text
        conv = extract_data(resp)
        return conv["id"]

    async def _send_user_message(self, client, conv_id, headers, text="Hello world"):
        resp = await client.post(
            f"/api/v1/chat/conversations/{conv_id}/send",
            json={"message": text},
            headers=headers,
        )
        assert resp.status_code == 200
        msg = await process_streaming_result(resp)
        # Server returns the assistant message created in response
        assert msg["role"] == "assistant"
        return msg

    async def _list_messages(self, client, conv_id, headers):
        resp = await client.get(f"/api/v1/chat/conversations/{conv_id}/messages", headers=headers)
        assert resp.status_code == 200, resp.text
        return extract_data(resp)

    async def test_regenerate_creates_variant_and_links_lineage(self, client, db, auth_headers):
        # Use admin headers from test runner to create provider/model-config
        admin_headers = auth_headers
        user_headers = await self._create_user_and_auth(client, admin_headers)
        conv_id = await self._create_conversation(client, admin_headers, user_headers)

        assistant_msg = await self._send_user_message(client, conv_id, user_headers, text="Say hi")

        # Regenerate the assistant message (non-stream)
        regen = await client.post(
            f"/api/v1/chat/messages/{assistant_msg['id']}/regenerate",
            json={},
            headers=user_headers,
        )
        assert regen.status_code == 200, regen.text
        regen_msg = await process_streaming_result(regen)

        # Validate lineage
        root = assistant_msg.get("parent_message_id") or assistant_msg["id"]
        assert regen_msg["parent_message_id"] == root
        assert isinstance(regen_msg.get("variant_index"), int)
        assert regen_msg["variant_index"] >= 1
        assert regen_msg["role"] == "assistant"
        assert regen_msg["conversation_id"] == conv_id

        # Messages should include both assistant variants
        messages = await self._list_messages(client, conv_id, user_headers)
        assistant_variants = [m for m in messages if m["role"] == "assistant"]
        assert len(assistant_variants) >= 2

if __name__ == "__main__":
    ChatRegenerateIntegrationTest().run()
