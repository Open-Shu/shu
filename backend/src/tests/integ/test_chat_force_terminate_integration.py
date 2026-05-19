"""Integration tests for the SHU-802 force-terminate endpoint (AC8/AC9/AC10).

The behavioral stub for ``POST /api/v1/chat/streams/{stream_id}/terminate``:
the client captures ``stream_id`` from the first SSE event, decides to stop
the stream, and POSTs to the terminate endpoint. The endpoint:

- **202** when the lifecycle is in-flight and owned by the caller. The
  variant's consumer loop observes ``lifecycle.event.is_set()`` at its next
  iteration, breaks the LLM loop with whatever content it has accumulated,
  and the shielded finalize commits a partial Message + ``LLMUsage(success=False,
  error_message="Stream interrupted: user_terminated")``. The persisted
  ``message_metadata["stream_state"]`` is locked to ``"user_terminated"``.
- **403** if the caller is not the lifecycle owner. The check is
  ``lifecycle.user_id == current_user.id``; UUIDs are not secrets here so
  ownership is the only gate.
- **410 Gone (STREAM_NOT_ACTIVE)** if the stream_id is unknown OR the stream
  has already finalized and the supervisor cleaned the registry entry.

Why force-terminate gets the deterministic ``stream_state`` test and the
disconnect suite does not: the terminate signal is sent via a SEPARATE
HTTP POST that runs synchronously from the test, locking
``lifecycle.reason="user_terminated"`` BEFORE the variant's stream phase
reaches finalize. The signal-then-finalize order is causal, not racy.
This is the deterministic counterpart to the L3 race documented in the
disconnect suite.

The local provider with ``SHU_LOCAL_STREAM_TEST_CHUNK_DELAY_MS`` set
ensures the stream phase is long enough for the terminate POST to land
mid-stream — without the delay, the stream finishes in ~10ms and the
test would race itself (terminate POST arrives after finalize).
"""

import asyncio
import json
import uuid

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.helpers.auth import cleanup_framework_test_admin, cleanup_test_user, create_active_user_with_id
from integ.helpers.wait_for_message import wait_for_message_persisted
from integ.response_utils import extract_data
from shu.core.config import get_settings_instance
from shu.core.logging import get_logger

logger = get_logger(__name__)

CHUNK_DELAY_MS_FOR_TERMINATE_TEST = 200

PROVIDER_DATA = {
    "name": "Test Local Provider",
    "provider_type": "local",
    "api_endpoint": "endpoint",
    "is_active": True,
}

MODEL_DATA = {
    "model_name": "local-echo",
    "display_name": "Local Echo Test Model",
    "description": "Local echo model for SHU-802 terminate tests",
    "context_window": 8192,
    "max_tokens": 4096,
    "supports_streaming": True,
    "supports_functions": False,
    "supports_vision": False,
}

MODEL_CONFIG_DATA = {
    "name": "Test Chat Assistant (Terminate)",
    "description": "Test model configuration for SHU-802 terminate endpoint",
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


async def _capture_stream_id_and_terminate(
    client,
    *,
    conv_id: str,
    user_headers: dict,
    terminate_headers: dict | None = None,
) -> tuple[str | None, dict | None]:
    """Drive a chat stream and POST terminate concurrently mid-stream.

    Returns ``(stream_id, terminate_response)``. ``terminate_headers``
    defaults to the user's own headers (the happy path); pass a different
    auth set (e.g., admin) to exercise the 403 ownership path. ``stream_id``
    is None if no entry appeared in the registry within the budget
    (test bug or SHU-802 regression).

    **Why this peeks ``app.state.in_flight_streams`` instead of reading
    the SSE ``stream_start`` event:** the test framework uses
    ``httpx.ASGITransport``, which buffers the entire response body
    before yielding the iterator. By the time the test could see the
    first SSE event, the variant has already finalized and the supervisor
    has popped the registry entry (terminate returns 410). Since we're
    in the same process as the app, we can peek the registry directly to
    catch the stream while it's still running — this gives the terminate
    POST a chance to land before the variant's stream phase ends. With
    the chunk-delay knob extending the stream phase to ~600ms, the poll
    + POST comfortably fit inside that window.
    """
    headers = terminate_headers if terminate_headers is not None else user_headers
    stream_id_future: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    async def _consume_stream() -> None:
        try:
            async with client.stream(
                "POST",
                f"/api/v1/chat/conversations/{conv_id}/send",
                json={"message": "long stream to terminate", "rag_rewrite_mode": "no_rag"},
                headers=user_headers,
            ) as response:
                if response.status_code != 200:
                    if not stream_id_future.done():
                        stream_id_future.set_exception(
                            AssertionError(f"send failed: {response.status_code}")
                        )
                    return
                # Iterate to consume the buffered body — required so
                # ASGITransport's flush cycle runs cleanly, even though
                # we get the stream_id via the registry peek instead.
                async for _line in response.aiter_lines():
                    pass
        except Exception as exc:
            if not stream_id_future.done():
                stream_id_future.set_exception(exc)

    async def _poll_registry_for_new_stream(known_ids: set[str]) -> None:
        """Watch app.state.in_flight_streams for the first new lifecycle
        registered after this poller starts. Resolves stream_id_future
        with the new stream_id; allows the parent coroutine to fire
        terminate while the variant is still on the event loop."""
        from shu.main import app as _app

        # Tight polling cadence — variant runs in tens to hundreds of ms
        # with the chunk delay; we want to catch it within the first
        # ~10ms so the terminate POST lands well before finalize.
        deadline = asyncio.get_running_loop().time() + 5.0
        while True:
            registry = getattr(_app.state, "in_flight_streams", {}) or {}
            new_ids = set(registry.keys()) - known_ids
            if new_ids:
                # Take the lexicographically-first new id (deterministic
                # if multiple appeared; in practice there's always exactly
                # one for this test).
                stream_id_future.set_result(sorted(new_ids)[0])
                return
            if asyncio.get_running_loop().time() >= deadline:
                if not stream_id_future.done():
                    stream_id_future.set_exception(
                        AssertionError("no new stream_id appeared in app.state within 5s")
                    )
                return
            await asyncio.sleep(0.005)

    from shu.main import app as _app

    pre_existing_ids = set((getattr(_app.state, "in_flight_streams", {}) or {}).keys())
    consumer_task = asyncio.create_task(_consume_stream())
    poller_task = asyncio.create_task(_poll_registry_for_new_stream(pre_existing_ids))

    try:
        stream_id = await asyncio.wait_for(stream_id_future, timeout=5.0)
        # Fire terminate while the variant is mid-stream. The chunk-delay
        # knob extends the stream phase enough that the terminate POST
        # (sub-50ms typical) lands before finalize.
        terminate_response = await client.post(
            f"/api/v1/chat/streams/{stream_id}/terminate",
            headers=headers,
        )
        return stream_id, terminate_response
    finally:
        # Drain background tasks so they don't leak into subsequent tests.
        for task in (poller_task, consumer_task):
            if not task.done():
                try:
                    await asyncio.wait_for(task, timeout=10.0)
                except asyncio.TimeoutError:
                    task.cancel()
                except Exception:
                    pass


class ChatForceTerminateIntegrationTest(BaseIntegrationTestSuite):
    """SHU-802 — terminate endpoint contract + partial-persist semantics."""

    def get_suite_name(self) -> str:
        return "Chat Force Terminate Integration"

    def get_suite_description(self) -> str:
        return (
            "Validates SHU-802 AC8 (POST /streams/{stream_id}/terminate "
            "endpoint, 202/403/410 responses), AC9 (consumer-loop "
            "lifecycle.event.is_set() check), and AC10 (partial-persist "
            "with stream_state='user_terminated' + LLMUsage(success=False))."
        )

    def get_test_functions(self):
        return [
            self.test_terminate_mid_stream_persists_with_user_terminated_state,
            self.test_terminate_with_different_user_returns_403,
            self.test_terminate_with_unknown_stream_id_returns_410,
            self.test_terminate_already_finalized_stream_returns_410,
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
            json={"title": "SHU-802 Terminate Test", "model_configuration_id": model_config_id},
            headers=user_headers,
        )
        assert conv_response.status_code == 200, conv_response.text
        return extract_data(conv_response)["id"]

    async def test_terminate_mid_stream_persists_with_user_terminated_state(
        self, client, db, auth_headers
    ):
        """SHU-802 AC8 + AC9 + AC10: terminate mid-stream, stream short-circuits,
        finalize commits with stream_state='user_terminated' + LLMUsage(success=False).

        This is the deterministic counterpart to the disconnect suite's
        L3 race. Because the terminate POST is a separate HTTP request
        the test makes synchronously, the lifecycle.signal('user_terminated')
        runs to completion BEFORE the variant's stream phase ends —
        finalize reads `lifecycle.resolved_reason() == 'user_terminated'`
        and stamps it on Message.message_metadata['stream_state']. No
        race; this assertion is tight.
        """
        admin_headers = auth_headers
        user_headers, user_id = await create_active_user_with_id(client, admin_headers)
        settings = get_settings_instance()
        original_delay = getattr(settings, "local_stream_test_chunk_delay_ms", 0)
        settings.local_stream_test_chunk_delay_ms = CHUNK_DELAY_MS_FOR_TERMINATE_TEST
        try:
            conv_id = await self._create_conversation(client, admin_headers, user_headers)

            stream_id, terminate_response = await _capture_stream_id_and_terminate(
                client, conv_id=conv_id, user_headers=user_headers
            )

            assert stream_id is not None, "SSE stream produced no stream_start event"
            assert terminate_response is not None, "terminate POST did not run"
            assert terminate_response.status_code == 202, (
                f"terminate should return 202 Accepted, got {terminate_response.status_code}: "
                f"{terminate_response.text}"
            )
            payload = extract_data(terminate_response)
            assert payload.get("stream_id") == stream_id, (
                f"terminate response should echo stream_id; got {payload!r}"
            )
            assert payload.get("reason") == "user_terminated", (
                f"terminate response reason should be 'user_terminated'; got {payload.get('reason')!r}"
            )

            # Wait for finalize to commit. Per AC9, the consumer loop
            # observes the lifecycle event at its next provider event
            # and short-circuits; finalize then stamps the
            # user_terminated state.
            persisted = await wait_for_message_persisted(
                db,
                conversation_id=conv_id,
                role="assistant",
                timeout_seconds=5.0,
            )
            assert persisted is not None, "Terminated stream did not persist its Message"
            metadata = persisted.message_metadata or {}
            # AC10 — stream_state must be user_terminated (deterministic on this path).
            assert metadata.get("stream_state") == "user_terminated", (
                f"Expected stream_state='user_terminated', got {metadata.get('stream_state')!r}. "
                f"Full metadata: {metadata!r}"
            )
            # AC10 — partial_usage_unavailable flag tells the audit consumer
            # that token counts are zero by absence, not by reality.
            assert metadata.get("partial_usage_unavailable") is True, (
                f"Terminated stream should set partial_usage_unavailable=True; got {metadata!r}"
            )
        finally:
            settings.local_stream_test_chunk_delay_ms = original_delay
            await cleanup_test_user(client, admin_headers, user_id)

    async def test_terminate_with_different_user_returns_403(self, client, db, auth_headers):
        """SHU-802 AC8 + Scenario S1: ownership check enforces
        lifecycle.user_id == current_user.id. A different user trying to
        terminate user A's stream gets 403 even though they know the
        stream_id.

        The "different user" is the framework admin rather than a second
        regular user — that way the test doesn't trip the dev-tier
        seat limit (5 users) when multiple suites run in sequence and
        each creates its own test users. Admin's user_id is distinct
        from user_a's, which is what the ownership check actually checks.
        """
        admin_headers = auth_headers
        user_a_headers, user_a_id = await create_active_user_with_id(client, admin_headers)

        settings = get_settings_instance()
        original_delay = getattr(settings, "local_stream_test_chunk_delay_ms", 0)
        settings.local_stream_test_chunk_delay_ms = CHUNK_DELAY_MS_FOR_TERMINATE_TEST
        try:
            conv_id = await self._create_conversation(client, admin_headers, user_a_headers)

            logger.info(
                "=== EXPECTED TEST OUTPUT: The following 403 from terminate is expected — "
                "the test deliberately attempts terminate from a different user's session "
                "to exercise SHU-802 AC8 ownership check ==="
            )

            stream_id, terminate_response = await _capture_stream_id_and_terminate(
                client,
                conv_id=conv_id,
                user_headers=user_a_headers,
                terminate_headers=admin_headers,  # admin is a different user from user_a
            )

            assert stream_id is not None
            assert terminate_response is not None
            assert terminate_response.status_code == 403, (
                f"terminate from a non-owning user should return 403, "
                f"got {terminate_response.status_code}: {terminate_response.text}"
            )

            logger.info(
                "=== EXPECTED TEST OUTPUT: 403 ownership rejection occurred as expected ==="
            )
        finally:
            settings.local_stream_test_chunk_delay_ms = original_delay
            await cleanup_test_user(client, admin_headers, user_a_id)

    async def test_terminate_with_unknown_stream_id_returns_410(self, client, db, auth_headers):
        """SHU-802 AC8 + Scenario M1: unknown stream_id (never existed,
        or expired and already cleaned from the registry) returns
        410 Gone with code STREAM_NOT_ACTIVE.
        """
        admin_headers = auth_headers
        user_headers, user_id = await create_active_user_with_id(client, admin_headers)
        try:
            logger.info(
                "=== EXPECTED TEST OUTPUT: The following 410 STREAM_NOT_ACTIVE is expected — "
                "the test exercises the unknown-stream_id branch of SHU-802 AC8 ==="
            )

            fake_stream_id = str(uuid.uuid4())
            response = await client.post(
                f"/api/v1/chat/streams/{fake_stream_id}/terminate",
                headers=user_headers,
            )
            assert response.status_code == 410, (
                f"unknown stream_id should return 410 Gone, got {response.status_code}: {response.text}"
            )
            body = response.json()
            error = body.get("error") or {}
            assert error.get("code") == "STREAM_NOT_ACTIVE", (
                f"error code should be STREAM_NOT_ACTIVE, got {error!r}"
            )

            logger.info(
                "=== EXPECTED TEST OUTPUT: 410 STREAM_NOT_ACTIVE occurred as expected ==="
            )
        finally:
            await cleanup_test_user(client, admin_headers, user_id)

    async def test_terminate_already_finalized_stream_returns_410(self, client, db, auth_headers):
        """SHU-802 AC8 + Scenario M1: a stream that completed and was
        cleaned out of app.state.in_flight_streams by its supervisor
        returns 410 Gone on a late terminate call. The supervisor's
        on_complete callback pops the entry; from the terminate endpoint's
        perspective the stream simply isn't in the registry anymore —
        same code as unknown.
        """
        admin_headers = auth_headers
        user_headers, user_id = await create_active_user_with_id(client, admin_headers)
        try:
            conv_id = await self._create_conversation(client, admin_headers, user_headers)

            # Capture stream_id from a normal chat (no chunk delay → fast
            # finalize). Iterate to the natural end of the stream so the
            # supervisor's on_complete callback has fired and the registry
            # entry has been popped before we attempt the late-terminate
            # call.
            stream_id: str | None = None
            async with client.stream(
                "POST",
                f"/api/v1/chat/conversations/{conv_id}/send",
                json={"message": "complete then terminate", "rag_rewrite_mode": "no_rag"},
                headers=user_headers,
            ) as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    event = _parse_sse_event(line)
                    if event is None:
                        continue
                    if event.get("event") == "stream_start":
                        content = event.get("content") or {}
                        stream_id = content.get("stream_id")
                # Loop exits when the SSE stream ends ([DONE] + body close).
                # By exit-time the consumer has read everything the server
                # had to send; supervisor cleanup is moments away.
            assert stream_id is not None

            # Belt-and-suspenders: also poll the DB so we're sure finalize
            # committed before we test the late-terminate path.
            persisted = await wait_for_message_persisted(
                db, conversation_id=conv_id, role="assistant", timeout_seconds=2.0
            )
            assert persisted is not None

            logger.info(
                "=== EXPECTED TEST OUTPUT: The following 410 STREAM_NOT_ACTIVE is expected — "
                "the test calls terminate AFTER the stream has already finalized ==="
            )

            response = await client.post(
                f"/api/v1/chat/streams/{stream_id}/terminate",
                headers=user_headers,
            )
            assert response.status_code == 410, (
                f"terminate on already-finalized stream should return 410 Gone, "
                f"got {response.status_code}: {response.text}"
            )
            body = response.json()
            error = body.get("error") or {}
            assert error.get("code") == "STREAM_NOT_ACTIVE"

            logger.info(
                "=== EXPECTED TEST OUTPUT: 410 STREAM_NOT_ACTIVE for already-finalized stream "
                "occurred as expected ==="
            )
        finally:
            await cleanup_test_user(client, admin_headers, user_id)


if __name__ == "__main__":
    ChatForceTerminateIntegrationTest().run()
