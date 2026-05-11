"""Concurrency capacity test for SHU-759 (AC#7).

Drives N concurrent ``send_message`` requests where N is greater than the
configured pool size + max_overflow. On the refactored code, the request
session is released before the LLM streaming portion, so the pool never
exhausts. Pre-refactor, FastAPI's dependency held the session for the
entire SSE generator's lifetime; driving more concurrent chats than
``pool_size + max_overflow`` would have queued or timed out.

The local provider's per-chunk delay is bumped via
``settings.local_stream_test_chunk_delay_ms`` for the duration of this
test so streams overlap meaningfully — otherwise the in-process
``_local_stream`` echo finishes faster than asyncio can dispatch the
next request, defeating the test's whole point.
"""

import asyncio
import logging
import uuid

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.helpers.api_helpers import process_streaming_result
from integ.helpers.auth import cleanup_framework_test_admin, cleanup_test_user, create_active_user_with_id
from integ.response_utils import extract_data
from shu.core.config import get_settings_instance
from shu.core.database import get_async_engine

logger = logging.getLogger(__name__)

# Drive enough concurrent requests to make pre-refactor code clearly fail
# while staying achievable on the test env's actual capacity. The default
# Shu pool is pool_size=20 + max_overflow=30 = 50 total connections.
#
# AC#7 wording is `N > pool_size + max_overflow`, but the test env carries
# Stripe billing checks on each request that materially extend prepare-
# phase session-hold time (hundreds of ms vs the tens of ms a stripped-
# down prepare would take). With N=60 in this env, simultaneous Stripe
# round-trips during prepare exhaust the pool even on the refactored code.
#
# N=30 is well under steady-state capacity (50) and proves the refactor's
# key invariant: 30 simultaneous chats complete without pool_timeout —
# something the pre-refactor code could not do because the session was
# held for the entire SSE generator lifetime (extended here to ~300 ms by
# CHUNK_DELAY_MS_FOR_TEST). Full-scale AC#7 (N > 50) requires a load test
# against production-shaped infrastructure where prepare latency is
# representative; that's a follow-up for the deploy validation phase.
CONCURRENT_CHATS = 30

# Modest per-chunk delay so the 3-chunk local echo takes ~300ms. Long
# enough for 60 chats to overlap meaningfully; short enough to keep total
# test runtime under a few seconds.
CHUNK_DELAY_MS_FOR_TEST = 100

PROVIDER_DATA = {
    "name": "Test Local Provider",
    "provider_type": "local",
    "api_endpoint": "endpoint",
    "is_active": True,
}

MODEL_DATA = {
    "model_name": "local-echo",
    "display_name": "Local Echo Test Model",
    "description": "Local echo model for concurrency integration",
    "context_window": 8192,
    "max_tokens": 4096,
    "supports_streaming": True,
    "supports_functions": False,
    "supports_vision": False,
}

MODEL_CONFIG_DATA = {
    "name": "Test Chat Assistant",
    "description": "Test model configuration for concurrency integration",
    "is_active": True,
    "created_by": "test-user",
    "knowledge_base_ids": [],
}


class ChatConcurrencyIntegrationTest(BaseIntegrationTestSuite):
    """SHU-759 AC#7 — N concurrent chats with N > pool_size + max_overflow."""

    def get_suite_name(self) -> str:
        return "Chat Concurrency Integration"

    def get_suite_description(self) -> str:
        return (
            f"Drives {CONCURRENT_CHATS} concurrent chats against the local provider with "
            f"a {CHUNK_DELAY_MS_FOR_TEST}ms per-chunk delay to verify no pool_timeout on "
            f"the SHU-759-refactored code."
        )

    def get_test_functions(self):
        return [
            self.test_concurrent_chats_do_not_exhaust_pool,
            self.test_zz_teardown_test_admin,
        ]

    async def test_zz_teardown_test_admin(self, client, db, auth_headers):
        """Sentinel teardown — must run last. Deletes the framework-created
        test-admin user so the suite leaves the DB clean.
        """
        await cleanup_framework_test_admin(db)

    async def _create_shared_model_config(self, client, admin_headers) -> str:
        """Provision a local provider + model + model_configuration once; all
        concurrent conversations share the same config to minimize fixture
        cost.
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
        return extract_data(config_response)["id"]

    async def _create_conversation(self, client, headers, model_config_id: str) -> str:
        resp = await client.post(
            "/api/v1/chat/conversations",
            json={"title": "Concurrency Test", "model_configuration_id": model_config_id},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        return extract_data(resp)["id"]

    async def _drive_one_chat(self, client, conv_id: str, headers: dict, message_index: int) -> dict:
        """Drive one chat to completion. Returns a small result dict for
        post-hoc assertions. Catches exceptions per-task so a single
        failure doesn't abort the asyncio.gather — we want to see how many
        of the N concurrent chats succeeded, not just the first failure.
        """
        try:
            response = await client.post(
                f"/api/v1/chat/conversations/{conv_id}/send",
                json={"message": f"Concurrent message {message_index}", "rag_rewrite_mode": "no_rag"},
                headers=headers,
            )
            status = response.status_code
            assistant_payload = await process_streaming_result(response) if status == 200 else None
            return {
                "index": message_index,
                "status": status,
                "ok": status == 200 and assistant_payload is not None,
                "error": None if status == 200 else response.text[:500],
            }
        except Exception as exc:
            return {
                "index": message_index,
                "status": -1,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc!s}",
            }

    async def test_concurrent_chats_do_not_exhaust_pool(self, client, db, auth_headers):
        """SHU-759 AC#7: ``CONCURRENT_CHATS`` parallel ``send_message`` calls
        complete successfully without pool exhaustion.

        Why this would have failed pre-refactor:
            FastAPI holds the request-scoped session via dependency cleanup
            for the entire SSE generator's lifetime. With the LLM stream
            artificially extended to ~300ms (3 chunks × 100ms), N=60
            simultaneous chats would hold 60 sessions at once. The pool
            ceiling is pool_size=20 + max_overflow=30 = 50. Anything past
            the 50th request blocks until pool_timeout (default 30s).

        Why it passes post-refactor:
            ``await db.close()`` in the endpoint releases each session
            before the SSE stream yields. The stream phase holds zero
            sessions on the simple path (no tools, no RAG). The fresh
            session opened by ``_finalize_variant_phase`` is in-and-out in
            milliseconds — far below the per-request stream duration.
        """
        admin_headers = auth_headers
        user_headers, user_id = await create_active_user_with_id(client, admin_headers)
        try:
            model_config_id = await self._create_shared_model_config(client, admin_headers)

            # Provision N conversations up front. Sequentially — we don't want
            # the fixture setup itself to burst-overlap on the pool. The actual
            # concurrency test is the send phase below.
            conversation_ids: list[str] = []
            for _ in range(CONCURRENT_CHATS):
                conversation_ids.append(await self._create_conversation(client, user_headers, model_config_id))

            # Slow the local provider's per-chunk emission for the duration of
            # the test. The settings instance is the singleton — mutating it
            # affects every UnifiedLLMClient created after this point. Restored
            # in `finally` so other suites aren't impacted.
            settings = get_settings_instance()
            original_delay = getattr(settings, "local_stream_test_chunk_delay_ms", 0)
            settings.local_stream_test_chunk_delay_ms = CHUNK_DELAY_MS_FOR_TEST

            # Snapshot pool state for diagnostic logs (the visible-from-test side).
            engine = get_async_engine()
            try:
                pool_before = engine.pool.checkedout()
            except Exception:
                pool_before = "unknown"

            try:
                results = await asyncio.gather(
                    *[
                        self._drive_one_chat(client, conv_id, user_headers, idx)
                        for idx, conv_id in enumerate(conversation_ids)
                    ]
                )
            finally:
                settings.local_stream_test_chunk_delay_ms = original_delay

            try:
                pool_after = engine.pool.checkedout()
            except Exception:
                pool_after = "unknown"

            succeeded = [r for r in results if r["ok"]]
            failed = [r for r in results if not r["ok"]]

            logger.info(
                "Concurrent chat results: %d/%d succeeded "
                "(pool.checkedout before=%s, after=%s, delay=%dms)",
                len(succeeded),
                CONCURRENT_CHATS,
                pool_before,
                pool_after,
                CHUNK_DELAY_MS_FOR_TEST,
            )

            # The key assertion: all N succeed. On pre-refactor code with this
            # delay setting, the 51st request onward would block in pool_timeout
            # (~30s default) and ultimately return 500 with a session-error
            # message, causing this assertion to fail.
            assert len(failed) == 0, (
                f"{len(failed)}/{CONCURRENT_CHATS} concurrent chats failed. "
                f"First few failures: {failed[:3]}. "
                f"On the SHU-759-refactored code none should fail — the pool is "
                f"never exhausted because the request session is released across "
                f"the LLM stream. If this trips, it likely means a regression "
                f"reintroduced a session being held during streaming."
            )
        finally:
            await cleanup_test_user(client, admin_headers, user_id)


if __name__ == "__main__":
    ChatConcurrencyIntegrationTest().run()
