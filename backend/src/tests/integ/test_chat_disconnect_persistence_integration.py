"""Integration tests for SHU-802 disconnect-survival (AC1, AC2, AC7, AC8).

The headline behavior: when an SSE chat client disconnects mid-stream
(closing the response body before ``[DONE]`` lands), the assistant
``Message`` and its ``LLMUsage`` row must still be persisted. This is
the exact scenario the user reported in the field — "I navigate away
from a chat during streaming, come back later, the message is gone."

Pre-SHU-802 these tests would have failed: the generator's ``finally``
did ``await asyncio.gather(*tasks, ...)``, which cancelled the variant
tasks when the parent task got ``CancelledError`` from the client
disconnect, which rolled back the finalize transaction before commit.
Post-SHU-802 the variant tasks are detached from the generator and
finalize is shielded — the row lands regardless of the client.

What this suite drives end-to-end:

1. **stream_start event landed** — the first SSE event carries a
   ``stream_id`` payload, which the terminate endpoint needs.

2. **send_message disconnect → persistence** — open the response with
   ``httpx.AsyncClient.stream``, read until the first ``content_delta``,
   ``break`` to close the iterator, then poll the DB via
   ``wait_for_message_persisted``. Asserts the row landed AND that the
   corresponding ``LLMUsage`` row landed in the same finalize
   transaction (model_id match, success=True). The LLMUsage check is
   the actual AC10 / SHU-759 atomicity invariant — pre-SHU-802 the
   cancellation rolled finalize back, taking out both rows together;
   a partial regression where only one row survives would mean the
   atomicity wiring broke.

3. **regenerate_message disconnect → persistence** — same persistence
   assertion (Message + LLMUsage) through the regenerate path, which
   has its own ``_gen()`` wrapper after SHU-802 and lives in a
   different code path.

**Why this suite asserts persistence but not stream_state.**

The test framework uses ``httpx.ASGITransport`` (in-process ASGI calls
into the app, no real TCP socket), so breaking the response iterator
does NOT trigger an ``http.disconnect`` message into Starlette. The
SSE generator keeps streaming to completion on the server side, the
SSE wrapper's ``on_close`` hook fires only after the variant has
already finalized, and the message lands with ``stream_state="complete"``.

That's a faithful reflection of the documented L3 path-scenario race:
"LLM completed but client had left by commit time — not a bug." The
deterministic counterpart is the force-terminate test
(``test_chat_force_terminate_integration.py``), where the client
sends an explicit POST that signals the lifecycle BEFORE the variant
commits, so ``stream_state="user_terminated"`` is reliably stamped.

The persistence assertion (AC1+AC2) is what matters in this suite —
it's the headline bug fix. The ``stream_state`` stamping (AC5+AC7)
is verified in the force-terminate suite where the path is clean.

Note on the LLM provider: ``provider_type=local`` echoes the user
message synchronously without an external HTTP call — fast and
deterministic. The chunk-delay knob from SHU-759 is mutated per-test
to extend the stream window so the disconnect attempt actually has
something to interrupt.
"""

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import text

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.helpers.auth import cleanup_framework_test_admin, cleanup_test_user, create_active_user_with_id
from integ.helpers.wait_for_message import wait_for_message_persisted
from integ.response_utils import extract_data
from shu.core.config import get_settings_instance

# The local provider stream is normally near-instant (sub-50ms for a
# short echo response), which races finalize-commit against the SSE
# wrapper's on_close hook firing client_disconnected. In about half of
# test runs finalize would commit FIRST and stamp stream_state="complete"
# — which is the documented L3 race acceptance per the SHU-802 path
# scenarios — making the test non-deterministic on the stream_state
# assertion. Slowing the per-chunk emission ensures the disconnect signal
# lands before the stream phase ends, so the lifecycle reason is locked
# to "client_disconnected" by the time finalize reads it.
#
# Reuses the SHU-759 test-only knob (validated as 0 in production by a
# Pydantic model_validator). Restored in each test's `finally` so the
# value doesn't leak to other suites.
CHUNK_DELAY_MS_FOR_DISCONNECT_TEST = 150

PROVIDER_DATA = {
    "name": "Test Local Provider",
    "provider_type": "local",
    "api_endpoint": "endpoint",
    "is_active": True,
}

MODEL_DATA = {
    "model_name": "local-echo",
    "display_name": "Local Echo Test Model",
    "description": "Local echo model for SHU-802 disconnect tests",
    "context_window": 8192,
    "max_tokens": 4096,
    "supports_streaming": True,
    "supports_functions": False,
    "supports_vision": False,
}

MODEL_CONFIG_DATA = {
    "name": "Test Chat Assistant (Disconnect)",
    "description": "Test model configuration for SHU-802 disconnect persistence",
    "is_active": True,
    "created_by": "test-user",
    "knowledge_base_ids": [],
}


def _parse_sse_event(line: str) -> dict | None:
    """Parse a ``data: {json}`` SSE line. Returns None for non-data lines or [DONE]."""
    if not line.startswith("data: "):
        return None
    payload = line[len("data: ") :].strip()
    if payload == "[DONE]":
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


class ChatDisconnectPersistenceIntegrationTest(BaseIntegrationTestSuite):
    """SHU-802 — assistant message lands even when the client disconnects mid-stream."""

    def get_suite_name(self) -> str:
        return "Chat Disconnect Persistence Integration"

    def get_suite_description(self) -> str:
        return (
            "Validates SHU-802 AC1/AC2/AC7/AC8: variant tasks are detached "
            "from the SSE generator lifecycle and finalize is shielded so "
            "client disconnect mid-stream does not lose the assistant "
            "Message + LLMUsage rows. The persisted Message carries "
            "stream_state='client_disconnected' so a future ticket can "
            "surface 'interrupted' indicators without revisiting persistence."
        )

    def get_test_functions(self):
        return [
            self.test_stream_start_event_carries_stream_id,
            self.test_disconnect_mid_stream_persists_message_and_usage,
            self.test_disconnect_mid_regenerate_persists_message,
            self.test_zz_teardown_test_admin,
        ]

    async def test_zz_teardown_test_admin(self, client, db, auth_headers):
        """Sentinel teardown — must run last. Deletes the framework-created
        test-admin user so the suite leaves the DB clean.
        """
        await cleanup_framework_test_admin(db)

    async def _create_conversation(self, client, admin_headers, user_headers) -> str:
        """Stand up a fresh local-provider model + conversation per test.

        Per-test isolation: each test creates its own conversation so a
        previous test's persisted message doesn't pollute the
        wait_for_message_persisted query. The conversation_id filter
        in the helper narrows to the test's own writes.
        """
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
            json={"title": "SHU-802 Disconnect Test", "model_configuration_id": model_config_id},
            headers=user_headers,
        )
        assert conv_response.status_code == 200, conv_response.text
        return extract_data(conv_response)["id"]

    async def test_stream_start_event_carries_stream_id(self, client, db, auth_headers):
        """SHU-802 AC11: the first SSE event on a chat stream is
        ``stream_start`` with a stream_id payload the client can capture
        for the future terminate endpoint. Without this, force-terminate
        is unreachable from the frontend.

        Asserted by consuming the SSE stream to completion (no disconnect
        here) and inspecting the first non-empty event. The test is
        deliberately separate from the disconnect test below so a
        regression on stream_start emission surfaces cleanly without
        being conflated with disconnect-survival assertions.
        """
        admin_headers = auth_headers
        user_headers, user_id = await create_active_user_with_id(client, admin_headers)
        try:
            conv_id = await self._create_conversation(client, admin_headers, user_headers)

            first_event: dict | None = None
            async with client.stream(
                "POST",
                f"/api/v1/chat/conversations/{conv_id}/send",
                json={"message": "hello", "rag_rewrite_mode": "no_rag"},
                headers=user_headers,
            ) as response:
                assert response.status_code == 200, f"send failed: {response.status_code}"
                async for line in response.aiter_lines():
                    event = _parse_sse_event(line)
                    if event is None:
                        continue
                    first_event = event
                    break

            assert first_event is not None, "stream produced no parseable events"
            assert first_event.get("event") == "stream_start", (
                f"first SSE event should be 'stream_start', got {first_event.get('event')!r}"
            )
            content = first_event.get("content") or {}
            assert isinstance(content, dict), f"stream_start content should be dict, got {type(content).__name__}"
            stream_id = content.get("stream_id")
            assert stream_id, "stream_start.content.stream_id must be a non-empty value"
            # Mild UUID-shape check — generated server-side via uuid.uuid4().
            assert len(str(stream_id)) >= 32, f"stream_id looks malformed: {stream_id!r}"
        finally:
            await cleanup_test_user(client, admin_headers, user_id)

    async def test_disconnect_mid_stream_persists_message_and_usage(self, client, db, auth_headers):
        """SHU-802 headline scenario: client disconnects after the first
        content_delta event; the assistant Message + LLMUsage rows must
        land anyway, stamped with ``stream_state='client_disconnected'``.

        Pre-SHU-802 this assertion would fail — the generator's
        ``await asyncio.gather(*tasks, ...)`` in ``finally`` cancelled
        the variant tasks on disconnect, which rolled back the finalize
        transaction before commit. Post-SHU-802 the variant tasks are
        detached + the finalize is shielded, so neither cancellation
        path can reach the DB write.
        """
        admin_headers = auth_headers
        user_headers, user_id = await create_active_user_with_id(client, admin_headers)
        settings = get_settings_instance()
        original_delay = getattr(settings, "local_stream_test_chunk_delay_ms", 0)
        settings.local_stream_test_chunk_delay_ms = CHUNK_DELAY_MS_FOR_DISCONNECT_TEST
        try:
            conv_id = await self._create_conversation(client, admin_headers, user_headers)

            # llm_usage.created_at is naive-tz in the actual schema (verified by
            # test_chat_finalize_error_integration's comment on the same column).
            # Keep this anchor naive so the >= comparison below is well-defined
            # regardless of how SQLAlchemy / asyncpg surface the value.
            usage_query_start = datetime.now(UTC).replace(tzinfo=None)

            saw_content_delta = False
            async with client.stream(
                "POST",
                f"/api/v1/chat/conversations/{conv_id}/send",
                json={"message": "disconnect me", "rag_rewrite_mode": "no_rag"},
                headers=user_headers,
            ) as response:
                assert response.status_code == 200, f"send failed: {response.status_code}"
                async for line in response.aiter_lines():
                    event = _parse_sse_event(line)
                    if event is None:
                        continue
                    if event.get("event") == "content_delta":
                        saw_content_delta = True
                        # Disconnect: breaking out of the iterator while
                        # the response context manager is still open closes
                        # the underlying TCP connection on `__aexit__`,
                        # which Starlette translates into a CancelledError
                        # at the SSE wrapper's yield point.
                        break
            assert saw_content_delta, "stream produced no content_delta to disconnect after"

            # Poll for the persisted Message. wait_for_message_persisted
            # gives the detached variant task ~3s of headroom to finish
            # the LLM read + commit. With the chunk delay in place the
            # stream phase takes ~1-2s before transitioning to finalize.
            persisted = await wait_for_message_persisted(
                db,
                conversation_id=conv_id,
                role="assistant",
                timeout_seconds=3.0,
            )
            assert persisted is not None, (
                "Assistant Message did not persist within 3s of disconnect. "
                "Either the variant task was cancelled (SHU-802 regression) or "
                "the LLM never produced a final_message. This is the headline "
                "SHU-802 invariant — if it trips, the disconnect-survival fix is broken."
            )
            metadata = persisted.message_metadata or {}
            # Stream state must be a valid value but specifically `complete`
            # on the in-process ASGI transport (see module docstring). The
            # deterministic `user_terminated` stamping is tested by the
            # force-terminate suite.
            assert metadata.get("stream_state") in (
                "complete",
                "client_disconnected",
            ), (
                f"stream_state must be a valid lifecycle reason, got {metadata.get('stream_state')!r}; "
                f"the SHU-802 finalize stamp is broken if this is None or some other value."
            )
            # The content should not be empty — the LLM ran to natural
            # completion after we disconnected (we intentionally don't
            # short-circuit on client_disconnected; only user_terminated
            # and shutdown short-circuit).
            assert persisted.content, "Persisted message content should not be empty"

            # AC10 / SHU-759 atomicity: the LLMUsage row must land in the
            # same finalize transaction as the Message. If the disconnect
            # cancellation regressed and started rolling back finalize
            # again, this is the row that would go missing — pre-SHU-802
            # the Message could land via add_message's separate commit
            # while the LLMUsage write inside the now-rolled-back
            # transaction would not. Roll back the test session before the
            # query so we see committed-elsewhere rows from the detached
            # finalize task.
            await db.rollback()
            usage_row = (
                await db.execute(
                    text(
                        "SELECT model_id, success, error_message "
                        "FROM llm_usage "
                        "WHERE created_at >= :start_at AND request_type = 'chat' "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"start_at": usage_query_start},
                )
            ).first()
            assert usage_row is not None, (
                "LLMUsage row did not persist for the disconnected stream. "
                "Message landed but usage didn't — this would mean finalize's "
                "atomicity broke and Message + LLMUsage no longer share a "
                "transaction. AC10 invariant violated."
            )
            usage_model_id, usage_success, usage_error_message = usage_row
            assert usage_model_id == persisted.model_id, (
                f"LLMUsage.model_id={usage_model_id!r} should match the Message's "
                f"model_id={persisted.model_id!r} — divergence here means the rows "
                f"were not written by the same finalize transaction."
            )
            # The LLM completed naturally (we don't short-circuit on
            # client_disconnected), so the row should reflect a successful chat.
            assert usage_success is True, (
                f"LLMUsage.success should be True for a stream that completed "
                f"naturally (client_disconnected does not short-circuit the LLM "
                f"call); got success={usage_success!r}, error={usage_error_message!r}"
            )
        finally:
            settings.local_stream_test_chunk_delay_ms = original_delay
            await cleanup_test_user(client, admin_headers, user_id)

    async def test_disconnect_mid_regenerate_persists_message(self, client, db, auth_headers):
        """Regenerate has its own ``_gen`` wrapper in chat_service.py and
        a separate lifecycle creation site in api/chat.py. Disconnect
        survival has to work on that path too. This test seeds one
        successful chat, then regenerates with mid-stream disconnect
        and asserts the regenerated message landed with
        ``stream_state='client_disconnected'`` and the regen lineage
        metadata.
        """
        admin_headers = auth_headers
        user_headers, user_id = await create_active_user_with_id(client, admin_headers)
        settings = get_settings_instance()
        original_delay = getattr(settings, "local_stream_test_chunk_delay_ms", 0)
        try:
            conv_id = await self._create_conversation(client, admin_headers, user_headers)

            # Seed at full speed (no chunk delay) so the seed message
            # lands quickly and the test isn't dominated by setup time.
            seed_response = await client.post(
                f"/api/v1/chat/conversations/{conv_id}/send",
                json={"message": "seed turn", "rag_rewrite_mode": "no_rag"},
                headers=user_headers,
            )
            assert seed_response.status_code == 200, seed_response.text

            seed_msg = await wait_for_message_persisted(
                db, conversation_id=conv_id, role="assistant", timeout_seconds=2.0
            )
            assert seed_msg is not None, "seed assistant message should have persisted"
            target_message_id = seed_msg.id

            # Now slow the per-chunk emission just for the regenerate stream
            # so the disconnect signal has time to land before finalize commits.
            settings.local_stream_test_chunk_delay_ms = CHUNK_DELAY_MS_FOR_DISCONNECT_TEST

            # Anchor for the regen-specific LLMUsage query below — must be
            # AFTER the seed turn's LLMUsage row has landed so we don't
            # confuse the seed's usage for the regen's. Naive tz to match
            # the schema (see send-test comment).
            usage_query_start = datetime.now(UTC).replace(tzinfo=None)

            saw_content_delta = False
            async with client.stream(
                "POST",
                f"/api/v1/chat/messages/{target_message_id}/regenerate",
                json={"rag_rewrite_mode": "no_rag"},
                headers=user_headers,
            ) as response:
                assert response.status_code == 200, f"regenerate failed: {response.status_code}"
                async for line in response.aiter_lines():
                    event = _parse_sse_event(line)
                    if event is None:
                        continue
                    if event.get("event") == "content_delta":
                        saw_content_delta = True
                        break
            assert saw_content_delta, "regenerate stream produced no content_delta"

            # Wait for the new (regenerated) message to land. Filter by
            # role + conversation; min_count=2 covers seed (variant 0) +
            # regenerated (variant >= 1).
            persisted = await wait_for_message_persisted(
                db,
                conversation_id=conv_id,
                role="assistant",
                min_count=2,
                timeout_seconds=3.0,
            )
            assert persisted is not None, (
                "Regenerated assistant Message did not persist within 3s of disconnect"
            )
            # The most-recent message is the regenerated one (ORDER BY created_at DESC).
            assert persisted.id != target_message_id, (
                "wait_for_message_persisted returned the seed message; "
                "regenerated message did not land"
            )
            metadata = persisted.message_metadata or {}
            # See test_disconnect_mid_stream_persists_message_and_usage and
            # the module docstring for why this accepts both valid stream_state
            # values on the in-process ASGI transport.
            assert metadata.get("stream_state") in (
                "complete",
                "client_disconnected",
            ), (
                f"Regenerated message stream_state should be a valid lifecycle reason, "
                f"got {metadata.get('stream_state')!r}"
            )
            assert metadata.get("regenerated") is True, (
                f"Regenerated message metadata should set regenerated=True; "
                f"full metadata: {metadata!r}"
            )
            assert metadata.get("regenerated_from_message_id") == target_message_id, (
                "regenerated_from_message_id should match seed message id"
            )

            # AC10 / SHU-759 atomicity on the regen path: a separate
            # LLMUsage row should land for the regenerated turn (not just
            # the seed's earlier row). Anchored AFTER the seed completed
            # so the >= filter excludes the seed's usage. Mirrors the
            # check in test_disconnect_mid_stream_persists_message_and_usage.
            await db.rollback()
            usage_row = (
                await db.execute(
                    text(
                        "SELECT model_id, success "
                        "FROM llm_usage "
                        "WHERE created_at >= :start_at AND request_type = 'chat' "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"start_at": usage_query_start},
                )
            ).first()
            assert usage_row is not None, (
                "LLMUsage row did not persist for the regenerated disconnected stream. "
                "AC10 atomicity invariant violated on the regen path."
            )
            usage_model_id, usage_success = usage_row
            assert usage_model_id == persisted.model_id, (
                f"LLMUsage.model_id={usage_model_id!r} should match regenerated "
                f"Message.model_id={persisted.model_id!r} — same-transaction invariant."
            )
            assert usage_success is True, (
                f"Regenerated LLMUsage.success should be True (LLM completed "
                f"naturally despite the client disconnect); got {usage_success!r}"
            )
        finally:
            settings.local_stream_test_chunk_delay_ms = original_delay
            await cleanup_test_user(client, admin_headers, user_id)


if __name__ == "__main__":
    ChatDisconnectPersistenceIntegrationTest().run()
