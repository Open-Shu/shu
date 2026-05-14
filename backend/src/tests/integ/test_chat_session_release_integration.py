"""Integration tests for SHU-759 chat session-release guarantees.

Two acceptance criteria are exercised here:

AC#1 (pool-checkout bound during stream window):
    ``send_message`` and ``regenerate_message`` hold zero DB sessions
    during the LLM provider call window on the simple path (no tools, no
    RAG). Asserted by hooking SQLAlchemy pool events via PoolObserver and
    bracketing the window on the first ``content_delta`` SSE event through
    the ``final_message`` SSE event.

AC#3 (finalize atomicity):
    The assistant ``Message`` and its corresponding ``LLMUsage`` row are
    written by `_finalize_variant_phase` in a single fresh-session
    transaction. Asserted by querying both tables after a chat completes
    and confirming both rows exist with consistent provider/model
    references and a matching user attribution.

Forced-failure atomicity (the "neither row exists on rollback" half of
AC#3) is intentionally deferred to the unit-test layer
(``test_finalize_retry.py``) where mocking a commit failure is clean.
Reproducing it in an integration test would require brittle internal
monkeypatching; the unit test plus the single-session-single-commit
structure provides equivalent coverage.
"""

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import text

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.helpers.auth import cleanup_framework_test_admin, cleanup_test_user, create_active_user_with_id
from integ.helpers.pool_observer import PoolObserver
from integ.response_utils import extract_data
from shu.core.database import get_async_engine

PROVIDER_DATA = {
    "name": "Test Local Provider",
    "provider_type": "local",
    "api_endpoint": "endpoint",
    "is_active": True,
}

MODEL_DATA = {
    "model_name": "local-echo",
    "display_name": "Local Echo Test Model",
    "description": "Local echo model for integration testing",
    "context_window": 8192,
    "max_tokens": 4096,
    "supports_streaming": True,
    "supports_functions": False,
    "supports_vision": False,
}

MODEL_CONFIG_DATA = {
    "name": "Test Chat Assistant",
    "description": "Test model configuration for session-release integration",
    "is_active": True,
    "created_by": "test-user",
    "knowledge_base_ids": [],
}


def _parse_sse_event(line: str) -> dict | None:
    """Parse a `data: {json}` SSE line. Returns None for non-data lines or [DONE]."""
    if not line.startswith("data: "):
        return None
    payload = line[len("data: "):].strip()
    if payload == "[DONE]":
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


class ChatSessionReleaseIntegrationTest(BaseIntegrationTestSuite):
    """SHU-759 — session-release behavior + finalize atomicity."""

    def get_suite_name(self) -> str:
        return "Chat Session Release Integration"

    def get_suite_description(self) -> str:
        return (
            "Validates SHU-759 AC#1 (zero pool checkouts during the LLM stream window) "
            "and AC#3 (Message + LLMUsage atomicity in finalize)."
        )

    def get_test_functions(self):
        return [
            self.test_no_pool_checkouts_during_stream_window_simple_chat,
            self.test_finalize_writes_message_and_usage_atomically,
            self.test_zz_teardown_test_admin,
        ]

    async def test_zz_teardown_test_admin(self, client, db, auth_headers):
        """Sentinel teardown — must run last. Deletes the framework-created
        test-admin user so the suite leaves the DB clean.
        """
        await cleanup_framework_test_admin(db)

    async def _create_conversation(self, client, admin_headers, user_headers) -> str:
        suffix = uuid.uuid4().hex[:8]
        provider_payload = {**PROVIDER_DATA, "name": f"{PROVIDER_DATA['name']} {suffix}"}

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
            json={"title": "Session Release Test", "model_configuration_id": model_config_id},
            headers=user_headers,
        )
        assert conv_response.status_code == 200, conv_response.text
        return extract_data(conv_response)["id"]

    async def test_no_pool_checkouts_during_stream_window_simple_chat(self, client, db, auth_headers):
        """SHU-759 AC#1: simple chat (no tools, no RAG) holds zero pool checkouts
        between the first content_delta SSE event and the final_message event.

        The PoolObserver hooks ``checkout`` and ``checkin`` pool events on the
        engine. We open the observer window precisely on the SSE event boundary
        that defines "during the LLM streaming portion" — this matches the AC's
        wording.

        Pre-refactor this assertion would have failed: the FastAPI-injected
        request session is held by dependency cleanup for the entire SSE
        generator's lifetime, so any sample inside the window observed ≥1
        active checkout. Post-refactor the endpoint releases the session before
        yielding the StreamingResponse, _build_tool_context only opens a
        session when tools are enabled (off here), and _post_process_references
        reads the kb_include_references_map snapshot (no DB queries when no
        sources came back from RAG).
        """
        admin_headers = auth_headers
        user_headers, user_id = await create_active_user_with_id(client, admin_headers)
        try:
            conv_id = await self._create_conversation(client, admin_headers, user_headers)

            engine = get_async_engine()
            send_url = f"/api/v1/chat/conversations/{conv_id}/send"
            message_payload = {"message": "Say hi", "rag_rewrite_mode": "no_rag"}

            with PoolObserver(engine) as observer:
                window_opened = False
                saw_final = False

                async with client.stream("POST", send_url, json=message_payload, headers=user_headers) as response:
                    assert response.status_code == 200, f"chat send failed: {response.status_code}"
                    async for line in response.aiter_lines():
                        event = _parse_sse_event(line)
                        if event is None:
                            continue
                        event_type = event.get("event")

                        # Open the observer window when the LLM begins emitting content.
                        if event_type == "content_delta" and not window_opened:
                            observer.open_window()
                            window_opened = True
                        # Close on final_message but keep iterating so the server fully
                        # flushes [DONE] and FastAPI's dependency cleanup runs before
                        # the test moves on.
                        elif event_type == "final_message" and window_opened and not saw_final:
                            stats = observer.close_window()
                            saw_final = True

                assert window_opened, "stream produced no content_delta SSE events; cannot bracket window"
                assert saw_final, "stream did not produce a final_message SSE event"

            # AC#1 — zero pool checkouts during the LLM-streaming portion of a
            # simple (no tools, no RAG) chat. If this trips with max_in_window=1,
            # the most likely cause is a regression that re-introduces a
            # mid-stream DB query (e.g. provider re-fetch, KB lookup, or the
            # request session being held by the endpoint).
            assert stats.max_in_window == 0, (
                f"Expected zero concurrent pool checkouts during the SSE-bracketed stream window "
                f"(simple chat, no tools, no RAG). Got max_in_window={stats.max_in_window}, "
                f"cumulative_hold_seconds={stats.cumulative_hold_seconds:.4f}, "
                f"window_duration_seconds={stats.window_duration_seconds:.4f}. "
                f"A non-zero value points at a regression that re-introduces a mid-stream "
                f"request-session dependency."
            )
        finally:
            await cleanup_test_user(client, admin_headers, user_id)

    async def test_finalize_writes_message_and_usage_atomically(self, client, db, auth_headers):
        """SHU-759 AC#3: the assistant Message and its LLMUsage row are
        written by _finalize_variant_phase in a single fresh-session
        transaction. After a successful chat, both rows must exist with
        consistent provider/model attribution and the same user_id.
        """
        admin_headers = auth_headers
        user_headers, user_id = await create_active_user_with_id(client, admin_headers)
        try:
            conv_id = await self._create_conversation(client, admin_headers, user_headers)

            # Snapshot LLMUsage state for this user *before* the chat so we can
            # isolate the row written by the chat we are about to drive.
            # The framework's test fixture creates a fresh test user per-suite,
            # so usage rows for this user are exclusively from this test.
            # llm_usage.created_at is `timestamp without time zone`, so the
            # comparison value must be naive too — asyncpg won't coerce.
            send_url = f"/api/v1/chat/conversations/{conv_id}/send"
            start_at = datetime.now(UTC).replace(tzinfo=None)

            send_response = await client.post(
                send_url, json={"message": "Atomic write test", "rag_rewrite_mode": "no_rag"}, headers=user_headers
            )
            assert send_response.status_code == 200, send_response.text

            # Find the most-recently-written assistant Message in this conversation.
            message_row = (
                await db.execute(
                    text(
                        "SELECT id, model_id, conversation_id, role, created_at "
                        "FROM messages "
                        "WHERE conversation_id = :conv_id AND role = 'assistant' "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"conv_id": conv_id},
                )
            ).first()
            assert message_row is not None, "finalize did not persist an assistant Message"
            message_id, message_model_id, message_conv_id, message_role, message_created_at = message_row
            assert message_role == "assistant"
            assert message_conv_id == conv_id

            # The matching LLMUsage row was written in the same transaction.
            # It must reference the same model AND attribute to the same user
            # (the conversation owner from the prepare snapshot).
            # We narrow by created_at >= start_at to ignore any pre-existing rows.
            usage_row = (
                await db.execute(
                    text(
                        "SELECT model_id, provider_id, user_id, request_type, success, total_tokens "
                        "FROM llm_usage "
                        "WHERE created_at >= :start_at AND request_type = 'chat' "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"start_at": start_at},
                )
            ).first()
            assert usage_row is not None, (
                "finalize wrote the assistant Message but no matching LLMUsage row "
                "— atomicity broken (or LLMUsage write silently failed)"
            )
            (
                usage_model_id,
                _usage_provider_id,
                usage_user_id,
                usage_request_type,
                usage_success,
                _usage_tokens,
            ) = usage_row

            assert usage_request_type == "chat"
            assert usage_success is True, "successful chat should record success=True"
            assert usage_model_id == message_model_id, (
                f"Message.model_id={message_model_id!r} and LLMUsage.model_id={usage_model_id!r} "
                f"diverge — the rows are not from the same finalize transaction"
            )

            # Confirm the user attribution is the conversation owner — this is the
            # SHU-700 concern that previously had its own unit test against the
            # removed _handle_exception. Moved to integration coverage as planned
            # in AC#6 refinement.
            conv_owner_row = (
                await db.execute(
                    text("SELECT user_id FROM conversations WHERE id = :id"), {"id": conv_id}
                )
            ).first()
            assert conv_owner_row is not None
            conversation_owner_id = conv_owner_row[0]
            assert usage_user_id == conversation_owner_id, (
                f"LLMUsage.user_id={usage_user_id!r} should match conversation owner "
                f"{conversation_owner_id!r} — user attribution lost between prepare and finalize"
            )
        finally:
            await cleanup_test_user(client, admin_headers, user_id)


if __name__ == "__main__":
    ChatSessionReleaseIntegrationTest().run()
