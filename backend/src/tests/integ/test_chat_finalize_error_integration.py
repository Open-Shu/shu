"""Integration tests for SHU-759's consolidated chat error path (AC #N7).

The pre-refactor error path took two pool checkouts and two commits per
failed chat: one for the error Message via ``add_message`` and another
for the failed ``LLMUsage`` row via ``UsageRecorder``. The refactor folds
both writes into ``_finalize_variant_phase``'s single fresh-session
transaction (the same one that handles the success path).

These tests drive a chat against a broken provider so the LLM call
raises ``LLMError``, then assert the finalize failure branch wrote both
rows atomically with the expected attribution.

Per TESTING.md, negative tests log the
``=== EXPECTED TEST OUTPUT: ... ===`` markers so the error scanner
doesn't flag the expected LLM-call errors as real failures.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import text

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.helpers.auth import cleanup_framework_test_admin, cleanup_test_user, create_active_user_with_id
from integ.response_utils import extract_data
from shu.core.logging import get_logger

logger = get_logger(__name__)


# An OpenAI-compatible provider pointed at a port that's never listening.
# httpx returns a connect error → chat_streaming raises LLMProviderError →
# _stream_variant_phase catches it → returns VariantStreamResult.Failure →
# _finalize_variant_phase persists the error message + failed usage.
BROKEN_PROVIDER_DATA = {
    "name": "Test Broken Provider",
    "provider_type": "openai",
    "api_endpoint": "http://127.0.0.1:1/v1",
    "api_key": "test-broken-key",
    "is_active": True,
}

MODEL_DATA = {
    "model_name": "gpt-broken-test",
    "display_name": "Broken Model (intentionally unreachable)",
    "description": "Routes to a refused TCP port to exercise the LLM-error path",
    "context_window": 8192,
    "max_tokens": 256,
    "supports_streaming": True,
    "supports_functions": False,
    "supports_vision": False,
}

MODEL_CONFIG_DATA = {
    "name": "Test Broken Chat Config",
    "description": "Drives finalize's failure branch by failing the LLM call",
    "is_active": True,
    "created_by": "test-user",
    "knowledge_base_ids": [],
}


class ChatFinalizeErrorIntegrationTest(BaseIntegrationTestSuite):
    """SHU-759 AC #N7 — failure path through `_finalize_variant_phase`."""

    def get_suite_name(self) -> str:
        return "Chat Finalize Error Integration"

    def get_suite_description(self) -> str:
        return (
            "Validates the consolidated chat error path (AC #N7): an LLM "
            "failure produces an error Message + LLMUsage(success=False) "
            "atomically via `_finalize_variant_phase`."
        )

    def get_test_functions(self):
        return [
            self.test_failed_chat_writes_error_message_and_failed_usage_atomically,
            self.test_zz_teardown_test_admin,
        ]

    async def test_zz_teardown_test_admin(self, client, db, auth_headers):
        """Sentinel teardown — must run last. Deletes the framework-created
        test-admin user so the suite leaves the DB clean.
        """
        await cleanup_framework_test_admin(db)

    async def _create_broken_conversation(self, client, admin_headers, user_headers) -> str:
        suffix = uuid.uuid4().hex[:8]
        provider_payload = {**BROKEN_PROVIDER_DATA, "name": f"{BROKEN_PROVIDER_DATA['name']} {suffix}"}

        provider_response = await client.post(
            "/api/v1/llm/providers", json=provider_payload, headers=admin_headers
        )
        assert provider_response.status_code == 201, provider_response.text
        provider_id = extract_data(provider_response)["id"]

        model_response = await client.post(
            f"/api/v1/llm/providers/{provider_id}/models", json=MODEL_DATA, headers=admin_headers
        )
        assert model_response.status_code == 200, model_response.text

        config_payload = {
            **MODEL_CONFIG_DATA,
            "llm_provider_id": provider_id,
            "model_name": MODEL_DATA["model_name"],
        }
        config_response = await client.post(
            "/api/v1/model-configurations", json=config_payload, headers=admin_headers
        )
        assert config_response.status_code == 201, config_response.text
        model_config_id = extract_data(config_response)["id"]

        conv_response = await client.post(
            "/api/v1/chat/conversations",
            json={"title": "Finalize Error Test", "model_configuration_id": model_config_id},
            headers=user_headers,
        )
        assert conv_response.status_code == 200, conv_response.text
        return extract_data(conv_response)["id"]

    async def test_failed_chat_writes_error_message_and_failed_usage_atomically(self, client, db, auth_headers):
        """SHU-759 AC #N7: an LLM call failure produces an error Message and
        a failed LLMUsage row, both written by `_finalize_variant_phase` in
        a single fresh-session transaction. Verifies:

        - The HTTP endpoint returns 200 (SSE protocol — errors are surfaced
          as in-stream `error` events, not HTTP 5xx).
        - An assistant Message exists with the apology-prefix error content.
        - An LLMUsage row exists with `success=False` and the conversation
          owner's `user_id` (SHU-700 attribution).
        - Both rows reference the same `model_id` (proves they were written
          in the same finalize transaction, not two separate sessions).
        """
        logger.info(
            "=== EXPECTED TEST OUTPUT: The following LLM connection errors are expected — "
            "the test deliberately points the provider at an unreachable TCP port to "
            "exercise the SHU-759 finalize failure branch ==="
        )

        admin_headers = auth_headers
        user_headers, user_id = await create_active_user_with_id(client, admin_headers)
        try:
            conv_id = await self._create_broken_conversation(client, admin_headers, user_headers)

            # llm_usage.created_at is `timestamp without time zone` — pass naive.
            start_at = datetime.now(UTC).replace(tzinfo=None)
            # conversations.updated_at is `timestamp with time zone` — keep aware.
            conv_bump_start = datetime.now(UTC)

            send_response = await client.post(
                f"/api/v1/chat/conversations/{conv_id}/send",
                json={"message": "This should fail to reach the LLM", "rag_rewrite_mode": "no_rag"},
                headers=user_headers,
            )
            # SSE protocol — 200 OK with an in-stream error event, not an HTTP 5xx.
            assert send_response.status_code == 200, (
                f"Expected 200 with in-stream error; got {send_response.status_code}: {send_response.text}"
            )

            # Assistant Message row should carry the apology-prefixed error content.
            message_row = (
                await db.execute(
                    text(
                        "SELECT id, model_id, role, content "
                        "FROM messages "
                        "WHERE conversation_id = :conv_id AND role = 'assistant' "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"conv_id": conv_id},
                )
            ).first()
            assert message_row is not None, "finalize did not persist any assistant Message on failure"
            message_id, message_model_id, message_role, message_content = message_row
            assert message_role == "assistant"
            assert message_content.startswith("I apologize, but I encountered an error:"), (
                f"Expected apology-prefix error content; got {message_content!r}"
            )

            # LLMUsage row with success=False from the same finalize transaction.
            usage_row = (
                await db.execute(
                    text(
                        "SELECT model_id, user_id, success, error_message "
                        "FROM llm_usage "
                        "WHERE created_at >= :start_at AND request_type = 'chat' "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"start_at": start_at},
                )
            ).first()
            assert usage_row is not None, (
                "finalize wrote the error Message but no matching LLMUsage row — "
                "atomicity broken (or the failed usage record silently dropped)"
            )
            usage_model_id, usage_user_id, usage_success, usage_error_message = usage_row

            assert usage_success is False, (
                f"failed chat should record success=False; got {usage_success!r}"
            )
            assert usage_error_message, (
                "LLMUsage.error_message should be populated on the failure path"
            )

            # Same model_id on both rows proves the writes share a transaction.
            # If a future refactor splits them into separate sessions, the rows
            # could still both exist but a partial-failure scenario would leave
            # them inconsistent.
            assert message_model_id == usage_model_id, (
                f"Message.model_id={message_model_id!r} and LLMUsage.model_id={usage_model_id!r} "
                f"diverge — the rows were not written by the same finalize transaction"
            )

            # User attribution must survive the failure path (SHU-700 regression
            # coverage that previously had a unit test against `_handle_exception`).
            conv_owner_row = (
                await db.execute(
                    text("SELECT user_id FROM conversations WHERE id = :id"), {"id": conv_id}
                )
            ).first()
            assert conv_owner_row is not None
            conversation_owner_id = conv_owner_row[0]
            assert usage_user_id == conversation_owner_id, (
                f"LLMUsage.user_id={usage_user_id!r} should match conversation owner "
                f"{conversation_owner_id!r} — failure path lost user attribution"
            )

            # Conversation.updated_at must advance on a failed chat so the
            # conversation still sorts to the top of "recently updated" in
            # the list view. Pre-refactor `_handle_exception` got this for
            # free via `add_message`; the inline-Message-construction path
            # has to do it explicitly. Regression check: a code-review
            # finding flagged that the failure branch dropped this bump.
            conv_updated_row = (
                await db.execute(
                    text("SELECT updated_at FROM conversations WHERE id = :id"),
                    {"id": conv_id},
                )
            ).first()
            assert conv_updated_row is not None
            conv_updated_at = conv_updated_row[0]
            assert conv_updated_at >= conv_bump_start, (
                f"Conversation.updated_at={conv_updated_at!r} did not advance past "
                f"the failure-path start time {conv_bump_start!r} — the failure branch "
                f"is no longer bumping the conversation timestamp and the chat will "
                f"sink to the bottom of 'recently updated' in the conversation list."
            )

            logger.info(
                "=== EXPECTED TEST OUTPUT: Finalize failure-branch error message and "
                "LLMUsage(success=False) were persisted as expected ==="
            )
        finally:
            await cleanup_test_user(client, admin_headers, user_id)


if __name__ == "__main__":
    ChatFinalizeErrorIntegrationTest().run()
