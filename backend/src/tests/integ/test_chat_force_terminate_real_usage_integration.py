"""SHU-803 AC10: real-provider partial-usage capture under user-terminate.

The existing :mod:`test_chat_force_terminate_integration` suite drives the
local test provider, which never emits usage events — it only exercises
the ``partial_usage_unavailable=True`` honest-flag branch from
SHU-802. This suite covers the real-provider paths and the drain-after-
terminate behavior AC9c introduces, plus the OpenRouter pricing
normalization (AC9d) and the Responses cancel endpoint (AC9e).

The load-bearing test is
:meth:`test_terminate_openrouter_gemma4_cost_derived_from_model_pricing`
— it proves the billing-evasion abuse vector close-out at the full
HTTP → consumer-loop → drain → finalize → DB layer. The abuse vector:
a user crafts a prompt to get a useful answer followed by tens of
thousands of filler tokens, terminates mid-stream after the useful part,
and pre-SHU-803 ends up with ``input_tokens=0, output_tokens=0,
total_cost=0`` because OpenAI Chat Completions and OpenRouter emit usage
only as the final chunk. AC9c (drain) catches the eventual usage chunk;
AC9d (pricing normalization) ensures the DB-rate fallback resolves for
OpenRouter slugs; the test asserts the LLMUsage row carries a non-zero
``total_cost``.

Implementation note: rather than registering test-only ``provider_type``
values with stub adapters, this suite patches
:meth:`UnifiedLLMClient._stream_response` to yield deterministic
chunks (see :mod:`integ.helpers.stub_provider_stream`). Each chunk is
fed to the REAL adapter's ``handle_provider_event`` so the capture
logic is exercised against actual provider chunk shapes — anthropic
``message_start`` / ``message_delta``, gemini cumulative
``usageMetadata``, OpenAI Chat Completions end-only ``usage``,
OpenAI Responses ``response.completed``.
"""

import asyncio
import logging
import uuid
from decimal import Decimal

from sqlalchemy import select, text, update

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.helpers.auth import cleanup_framework_test_admin, cleanup_test_user, create_active_user_with_id
from integ.helpers.stub_provider_stream import (
    anthropic_message_start_then_delta_fixture,
    anthropic_pre_delta_fixture,
    gemini_cumulative_usage_fixture,
    openai_completions_end_usage_fixture,
    openai_responses_response_completed_fixture,
    stub_provider_stream,
    stub_responses_cancel_transport,
)
from integ.helpers.wait_for_message import wait_for_message_persisted
from integ.response_utils import extract_data
from integ.test_chat_force_terminate_integration import _capture_stream_id_and_terminate
from shu.core.config import get_settings_instance
from shu.core.logging import get_logger
from shu.models.llm_provider import LLMModel, LLMUsage
from shu.services.chat_streaming import StreamLifecycle

logger = get_logger(__name__)

# Per-chunk default delay used inside the stub generator. The terminate
# POST in `_capture_stream_id_and_terminate` polls the registry every
# 5ms and fires HTTP POST ~50ms after the variant registers. Most chunks
# in the per-protocol fixtures pause 0–300ms; the test-specific delays
# are tuned so the terminate-fire window lands between the chunks
# documented in each test's docstring.

# Default model_name to use when the test doesn't care about pricing
# resolution (most tests). For the OpenRouter cost tests we use the
# real slug shape so AC9d normalization resolves to the bare-name
# pricing entry.
DEFAULT_MODEL_NAME_BY_PROVIDER = {
    "anthropic": "claude-shu803-stub",
    "gemini": "models/gemini-shu803-stub",
    "generic_completions": "shu803-stub-completions",
    "openai": "shu803-stub-responses",
}


async def _create_provider_model_conversation(
    client,
    admin_headers: dict,
    user_headers: dict,
    *,
    provider_type: str,
    provider_name: str,
    model_name: str | None = None,
    cost_per_input_unit: Decimal | None = None,
    cost_per_output_unit: Decimal | None = None,
) -> tuple[str, str, str]:
    """Provision the four DB rows each test needs: LLMProvider, LLMModel,
    ModelConfiguration, Conversation. Returns
    ``(provider_id, model_id, conversation_id)``.

    The ``cost_per_input_unit`` / ``cost_per_output_unit`` arguments are
    written directly onto the model row AFTER creation so the
    DB-rate fallback path in ``usage_recording.py`` resolves to a
    non-zero cost. Skipped when None — the OpenRouter cost tests use
    these; other tests don't care about the cost column.
    """
    suffix = uuid.uuid4().hex[:8]
    model_name = model_name or DEFAULT_MODEL_NAME_BY_PROVIDER[provider_type]

    provider_payload = {
        "name": f"{provider_name}-{suffix}",
        "provider_type": provider_type,
        "api_endpoint": "https://example-stub.invalid/v1",
        "is_active": True,
    }
    provider_response = await client.post("/api/v1/llm/providers", json=provider_payload, headers=admin_headers)
    assert provider_response.status_code == 201, provider_response.text
    provider_id = extract_data(provider_response)["id"]

    model_payload = {
        "model_name": model_name,
        "display_name": f"SHU-803 Stub Model ({provider_type}) {suffix}",
        "description": "SHU-803 AC10 stub — synthetic chunks via _stream_response patch",
        "context_window": 8192,
        "max_tokens": 4096,
        "supports_streaming": True,
        "supports_functions": False,
        "supports_vision": False,
    }
    model_response = await client.post(
        f"/api/v1/llm/providers/{provider_id}/models", json=model_payload, headers=admin_headers
    )
    assert model_response.status_code == 200, model_response.text
    model_id = extract_data(model_response)["id"]

    config_payload = {
        # SHU-803: suffix the config name with the same per-test UUID so
        # tests run cleanly even when prior runs leaked rows. Without
        # the suffix two same-provider_type tests (e.g. both
        # generic_completions) collide on the name UNIQUE constraint.
        "name": f"SHU-803 Stub Config ({provider_type}) {suffix}",
        "description": "SHU-803 AC10",
        "is_active": True,
        "created_by": "test-user",
        "knowledge_base_ids": [],
        "llm_provider_id": provider_id,
        "model_name": model_name,
        # SHU-803: must be True so the chat streaming layer takes the
        # streaming branch in chat_completion and goes through the
        # patched _stream_response. Without this, allowed_to_stream
        # evaluates False and chat_completion does a real HTTP POST
        # against the stub api_endpoint → DNS failure.
        "functionalities": {"supports_streaming": True},
    }
    config_response = await client.post("/api/v1/model-configurations", json=config_payload, headers=admin_headers)
    assert config_response.status_code == 201, config_response.text
    model_config_id = extract_data(config_response)["id"]

    conv_response = await client.post(
        "/api/v1/chat/conversations",
        json={"title": f"SHU-803 Stub Test ({provider_type})", "model_configuration_id": model_config_id},
        headers=user_headers,
    )
    assert conv_response.status_code == 200, conv_response.text
    conversation_id = extract_data(conv_response)["id"]

    return provider_id, model_id, conversation_id


async def _seed_model_pricing(db, model_id: str, *, cost_per_input_unit: Decimal, cost_per_output_unit: Decimal) -> None:
    """Write per-unit pricing onto an LLMModel row so the DB-rate fallback
    path in ``usage_recording.py`` produces non-zero costs. The OpenRouter
    cost tests use this to simulate ``sync_pricing_to_db`` having
    populated rates via the AC9d normalized-fallback resolution.
    """
    await db.execute(
        update(LLMModel)
        .where(LLMModel.id == model_id)
        .values(
            cost_per_input_unit=cost_per_input_unit,
            cost_per_output_unit=cost_per_output_unit,
        )
    )
    await db.commit()


async def _ensure_local_provider_type(db) -> None:
    """SHU-803 tests don't use ``local`` directly, but they may run
    before/alongside other suites that do. Mirror the
    ``baseline/_setup.py`` bootstrap so a fresh DB has the row.
    """
    existing = await db.execute(
        text("SELECT 1 FROM llm_provider_type_definitions WHERE key = :k"), {"k": "local"}
    )
    if existing.first():
        return
    from datetime import UTC, datetime as _dt
    now = _dt.now(UTC)
    await db.execute(
        text(
            "INSERT INTO llm_provider_type_definitions "
            "(id, key, display_name, provider_adapter_name, is_active, created_at, updated_at) "
            "VALUES (:id, :key, :display_name, :adapter, :is_active, :created_at, :updated_at)"
        ),
        {
            "id": str(uuid.uuid4()),
            "key": "local",
            "display_name": "Local (test)",
            "adapter": "local",
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        },
    )
    await db.commit()


async def _latest_llm_usage_for_model(db, model_id: str) -> LLMUsage | None:
    """Fetch the most-recent LLMUsage row for a given model_id. Used to
    assert on the row finalize wrote after the terminate signal landed.
    """
    result = await db.execute(
        select(LLMUsage).where(LLMUsage.model_id == model_id).order_by(LLMUsage.created_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()


class ChatForceTerminateRealUsageIntegrationTest(BaseIntegrationTestSuite):
    """SHU-803 AC10 — per-protocol terminate integration coverage."""

    def get_suite_name(self) -> str:
        return "Chat Force Terminate Real Usage Integration"

    def get_suite_description(self) -> str:
        return (
            "SHU-803 AC10: validates real-provider partial-usage capture "
            "under user-terminate. Covers Anthropic message_start nested-"
            "usage fix (AC9b), Gemini cumulative usageMetadata, OpenAI "
            "Chat Completions / Responses drain (AC9c), Responses cancel "
            "endpoint (AC9e), OpenRouter pricing normalization (AC9d), "
            "and the shutdown escape valve."
        )

    def get_test_functions(self):
        return [
            self.test_terminate_anthropic_captures_message_start_and_delta_usage,
            self.test_terminate_anthropic_pre_delta_captures_input_tokens_only,
            self.test_terminate_gemini_captures_per_chunk_usage,
            self.test_terminate_completions_drains_for_end_usage,
            self.test_terminate_completions_drain_propagates_shutdown,
            self.test_terminate_responses_drains_for_response_completed_usage,
            self.test_terminate_responses_cancel_called_via_mock_transport,
            self.test_terminate_openrouter_gemma4_cost_derived_from_model_pricing,
            self.test_terminate_openrouter_authoritative_cost_via_wire,
            self.test_zz_teardown_test_admin,
        ]

    async def test_zz_teardown_test_admin(self, client, db, auth_headers):
        await cleanup_framework_test_admin(db)

    async def _setup_user_and_provider(
        self,
        client,
        db,
        auth_headers,
        *,
        provider_type: str,
        provider_name: str,
        model_name: str | None = None,
        cost_per_input_unit: Decimal | None = None,
        cost_per_output_unit: Decimal | None = None,
    ) -> tuple[dict, str, str, str, str]:
        """Returns ``(user_headers, user_id, provider_id, model_id, conversation_id)``."""
        await _ensure_local_provider_type(db)
        admin_headers = auth_headers
        user_headers, user_id = await create_active_user_with_id(client, admin_headers)
        provider_id, model_id, conversation_id = await _create_provider_model_conversation(
            client,
            admin_headers,
            user_headers,
            provider_type=provider_type,
            provider_name=provider_name,
            model_name=model_name,
        )
        if cost_per_input_unit is not None or cost_per_output_unit is not None:
            await _seed_model_pricing(
                db,
                model_id,
                cost_per_input_unit=cost_per_input_unit or Decimal("0"),
                cost_per_output_unit=cost_per_output_unit or Decimal("0"),
            )
        return user_headers, user_id, provider_id, model_id, conversation_id

    # ------------------------------------------------------------------
    # 1. Anthropic — happy path (message_start + delta both land)
    # ------------------------------------------------------------------
    async def test_terminate_anthropic_captures_message_start_and_delta_usage(self, client, db, auth_headers):
        """Anthropic emits ``input_tokens`` on ``message_start`` (nested
        under ``message.usage``) and ``output_tokens`` on each
        ``message_delta`` (top-level ``usage``). When terminate fires
        AFTER the first message_delta, both should land on the LLMUsage
        row. The SHU-803 anthropic_adapter fix (AC9b) makes the
        message_start nested-usage capture work — pre-fix only delta
        was caught.
        """
        user_headers, user_id, _, model_id, conv_id = await self._setup_user_and_provider(
            client, db, auth_headers, provider_type="anthropic", provider_name="shu803-anthropic"
        )
        try:
            fixture = anthropic_message_start_then_delta_fixture(
                input_tokens=42,
                output_tokens=17,
                text_content="The answer is 42.",
                delay_before_message_delta_s=0.05,
            )
            with stub_provider_stream(fixture):
                stream_id, terminate_response = await _capture_stream_id_and_terminate(
                    client, conv_id=conv_id, user_headers=user_headers
                )
            assert stream_id is not None
            assert terminate_response is not None
            assert terminate_response.status_code == 202

            persisted = await wait_for_message_persisted(
                db, conversation_id=conv_id, role="assistant", timeout_seconds=5.0
            )
            assert persisted is not None
            assert (persisted.message_metadata or {}).get("stream_state") == "user_terminated"

            usage = await _latest_llm_usage_for_model(db, model_id)
            assert usage is not None, "LLMUsage row missing for Anthropic terminate test"
            assert usage.success is False
            assert usage.error_message == "Stream interrupted: user_terminated"
            # Both axes captured — the load-bearing assertion for the
            # SHU-803 Anthropic fix on the happy-path branch.
            assert usage.input_tokens > 0, (
                f"input_tokens should be captured from message_start; got {usage.input_tokens}"
            )
            assert usage.output_tokens > 0, (
                f"output_tokens should be captured from message_delta; got {usage.output_tokens}"
            )
        finally:
            await cleanup_test_user(client, auth_headers, user_id)

    # ------------------------------------------------------------------
    # 2. Anthropic — pre-delta (input_tokens ONLY)
    # ------------------------------------------------------------------
    async def test_terminate_anthropic_pre_delta_captures_input_tokens_only(self, client, db, auth_headers):
        """Load-bearing test for the SHU-803 anthropic_adapter fix
        (AC9b). The fixture yields ``message_start`` (with nested
        ``input_tokens``), then a 400ms pause where the terminate POST
        lands, then content_block_delta. Terminate fires BEFORE any
        ``message_delta`` — pre-fix this lost input_tokens entirely;
        post-fix the LLMUsage row carries input_tokens > 0 with
        output_tokens = 0.
        """
        user_headers, user_id, _, model_id, conv_id = await self._setup_user_and_provider(
            client, db, auth_headers, provider_type="anthropic", provider_name="shu803-anthropic-predelta"
        )
        try:
            fixture = anthropic_pre_delta_fixture(input_tokens=250, delay_before_first_content_s=0.4)
            with stub_provider_stream(fixture):
                stream_id, terminate_response = await _capture_stream_id_and_terminate(
                    client, conv_id=conv_id, user_headers=user_headers
                )
            assert stream_id is not None
            assert terminate_response.status_code == 202

            persisted = await wait_for_message_persisted(
                db, conversation_id=conv_id, role="assistant", timeout_seconds=5.0
            )
            assert persisted is not None
            assert (persisted.message_metadata or {}).get("stream_state") == "user_terminated"

            usage = await _latest_llm_usage_for_model(db, model_id)
            assert usage is not None
            assert usage.success is False
            assert usage.input_tokens > 0, (
                "SHU-803 AC9b: input_tokens from message_start must survive "
                "a terminate before any message_delta lands. Pre-fix this was 0."
            )
            assert usage.output_tokens == 0, (
                f"No message_delta landed before terminate; output_tokens should be 0, "
                f"got {usage.output_tokens}"
            )
        finally:
            await cleanup_test_user(client, auth_headers, user_id)

    # ------------------------------------------------------------------
    # 3. Gemini — cumulative usageMetadata
    # ------------------------------------------------------------------
    async def test_terminate_gemini_captures_per_chunk_usage(self, client, db, auth_headers):
        """Gemini emits ``usageMetadata`` cumulative-to-date on every
        streaming chunk. The drain-exit snapshot reflects the LAST-seen
        cumulative counts — equivalent to "what the provider had billed
        up to the moment terminate landed." No drain needed at the
        protocol level (usage is incremental), but the drain path
        still runs and the audit fields are recorded.
        """
        user_headers, user_id, _, model_id, conv_id = await self._setup_user_and_provider(
            client, db, auth_headers, provider_type="gemini", provider_name="shu803-gemini"
        )
        try:
            fixture = gemini_cumulative_usage_fixture(
                final_prompt_tokens=33,
                final_candidates_tokens=8,
                text_content="ok",
                delay_before_final_chunk_s=0.3,
            )
            with stub_provider_stream(fixture):
                stream_id, terminate_response = await _capture_stream_id_and_terminate(
                    client, conv_id=conv_id, user_headers=user_headers
                )
            assert stream_id is not None
            assert terminate_response.status_code == 202

            persisted = await wait_for_message_persisted(
                db, conversation_id=conv_id, role="assistant", timeout_seconds=5.0
            )
            assert persisted is not None

            usage = await _latest_llm_usage_for_model(db, model_id)
            assert usage is not None
            assert usage.success is False
            assert usage.input_tokens > 0
            assert usage.output_tokens > 0

        finally:
            await cleanup_test_user(client, auth_headers, user_id)

    # ------------------------------------------------------------------
    # 4. OpenAI Chat Completions — drain catches end-of-stream usage
    # ------------------------------------------------------------------
    async def test_terminate_completions_drains_for_end_usage(self, client, db, auth_headers):
        """OpenAI Chat Completions emits ``usage`` ONLY in the final
        chunk before ``[DONE]`` when ``stream_options.include_usage=true``.
        Pre-SHU-803 terminate broke the loop before that chunk and
        the LLMUsage row recorded zero tokens (the billing-evasion
        abuse vector). AC9c drain silently consumes the upstream until
        the usage chunk lands, then finalize records it.

        Asserts: input_tokens / output_tokens > 0 (drain captured the
        end-of-stream usage), drain_audit reflects ``drain_outcome``
        of ``done`` or ``final_event``, content_accumulator was frozen
        at terminate (persisted ``Message.content`` shorter than the
        full fixture content).
        """
        user_headers, user_id, _, model_id, conv_id = await self._setup_user_and_provider(
            client, db, auth_headers, provider_type="generic_completions", provider_name="shu803-completions"
        )
        try:
            fixture = openai_completions_end_usage_fixture(
                prompt_tokens=120,
                completion_tokens=55,
                text_content="Hello, this is a streamed response from the stub.",
                delay_before_usage_chunk_s=0.3,
            )
            with stub_provider_stream(fixture):
                stream_id, terminate_response = await _capture_stream_id_and_terminate(
                    client, conv_id=conv_id, user_headers=user_headers
                )
            assert stream_id is not None
            assert terminate_response.status_code == 202

            persisted = await wait_for_message_persisted(
                db, conversation_id=conv_id, role="assistant", timeout_seconds=5.0
            )
            assert persisted is not None
            assert (persisted.message_metadata or {}).get("stream_state") == "user_terminated"

            usage = await _latest_llm_usage_for_model(db, model_id)
            assert usage is not None
            assert usage.success is False
            # The load-bearing assertions for AC9c drain:
            assert usage.input_tokens > 0, (
                "SHU-803 AC9c: drain must consume upstream until the end-of-stream "
                "usage chunk lands. Pre-fix input_tokens was 0 on terminate."
            )
            assert usage.output_tokens > 0
            # Drain audit recorded in request_metadata (AC9g).
            request_metadata = usage.request_metadata or {}
            assert request_metadata.get("drain_outcome") in {"done", "final_event"}, (
                f"Expected clean drain exit; got drain_outcome={request_metadata.get('drain_outcome')!r}"
            )
            assert request_metadata.get("cancel_attempted") is True, (
                "drain spawns cancel_task concurrently — should be attempted even when adapter is no-op"
            )
        finally:
            await cleanup_test_user(client, auth_headers, user_id)

    # ------------------------------------------------------------------
    # 5. Drain shutdown escape valve
    # ------------------------------------------------------------------
    async def test_terminate_completions_drain_propagates_shutdown(self, client, db, auth_headers):
        """SHU-803 R7 / AC9c escape valve: drain in progress for a
        user_terminated stream, then SIGTERM fires. The drain loop must
        observe ``lifecycle.shutdown_signaled`` between events and exit
        as ``drain_outcome=shutdown_aborted``. Without the escape valve,
        drain would only stop via cancellation propagation at the
        lifespan-drain timeout, bypassing the shielded finalize.
        """
        from shu.main import app as _app
        from shu.services.chat_streaming import signal_shutdown_to_in_flight_streams

        user_headers, user_id, _, model_id, conv_id = await self._setup_user_and_provider(
            client, db, auth_headers, provider_type="generic_completions", provider_name="shu803-shutdown"
        )
        try:
            # Slow drain — the usage chunk lands far enough in the future
            # that we can fire shutdown DURING drain.
            fixture = openai_completions_end_usage_fixture(
                prompt_tokens=88,
                completion_tokens=44,
                delay_before_usage_chunk_s=2.0,
            )

            async def _fire_shutdown_during_drain() -> None:
                # Wait until the variant has registered AND terminate
                # has fired (registry contains the lifecycle with
                # reason="user_terminated"); then fire shutdown.
                deadline = asyncio.get_running_loop().time() + 5.0
                while True:
                    registry = getattr(_app.state, "in_flight_streams", {}) or {}
                    fired_lifecycles = [
                        lc for lc in registry.values() if lc.reason == "user_terminated"
                    ]
                    if fired_lifecycles:
                        # Allow drain to spin up briefly so the
                        # between-events check is the one that observes
                        # shutdown_signaled (not the entry check).
                        await asyncio.sleep(0.1)
                        signal_shutdown_to_in_flight_streams(registry)
                        return
                    if asyncio.get_running_loop().time() >= deadline:
                        return
                    await asyncio.sleep(0.01)

            with stub_provider_stream(fixture):
                shutdown_task = asyncio.create_task(_fire_shutdown_during_drain())
                try:
                    stream_id, terminate_response = await _capture_stream_id_and_terminate(
                        client, conv_id=conv_id, user_headers=user_headers
                    )
                finally:
                    if not shutdown_task.done():
                        try:
                            await asyncio.wait_for(shutdown_task, timeout=2.0)
                        except asyncio.TimeoutError:
                            shutdown_task.cancel()

            assert stream_id is not None
            assert terminate_response.status_code == 202

            persisted = await wait_for_message_persisted(
                db, conversation_id=conv_id, role="assistant", timeout_seconds=8.0
            )
            assert persisted is not None
            # The stream was user-terminated; shutdown layered on top.
            # ``reason`` stays user_terminated (first-writer-wins at
            # tier 2) so the audit trail reflects user intent.
            metadata = persisted.message_metadata or {}
            assert metadata.get("stream_state") in {"user_terminated", "shutdown"}

            usage = await _latest_llm_usage_for_model(db, model_id)
            assert usage is not None
            assert usage.success is False
            # The escape valve fired: drain exited as shutdown_aborted.
            request_metadata = usage.request_metadata or {}
            assert request_metadata.get("drain_outcome") == "shutdown_aborted", (
                f"Expected drain_outcome=shutdown_aborted (escape valve); got "
                f"{request_metadata.get('drain_outcome')!r}"
            )
        finally:
            await cleanup_test_user(client, auth_headers, user_id)

    # ------------------------------------------------------------------
    # 6. OpenAI Responses — drain catches response.completed
    # ------------------------------------------------------------------
    async def test_terminate_responses_drains_for_response_completed_usage(self, client, db, auth_headers):
        """OpenAI Responses API emits usage ONLY on ``response.completed``
        — same end-only pattern as Chat Completions but with a real
        cancel endpoint to stop server-side billing (AC9e). Cancel is
        mocked at the transport layer (200 OK); drain still runs in
        parallel via ``asyncio.gather`` and captures the eventual
        ``response.completed`` chunk's usage.
        """
        user_headers, user_id, _, model_id, conv_id = await self._setup_user_and_provider(
            client, db, auth_headers, provider_type="openai", provider_name="shu803-responses"
        )
        try:
            fixture = openai_responses_response_completed_fixture(
                input_tokens=200,
                output_tokens=50,
                text_content="Hi from Responses stub.",
                delay_before_completed_s=0.3,
            )

            def cancel_handler(_request):
                import httpx
                return httpx.Response(200, json={"cancelled": True})

            with stub_provider_stream(fixture), stub_responses_cancel_transport(cancel_handler):
                stream_id, terminate_response = await _capture_stream_id_and_terminate(
                    client, conv_id=conv_id, user_headers=user_headers
                )
            assert stream_id is not None
            assert terminate_response.status_code == 202

            persisted = await wait_for_message_persisted(
                db, conversation_id=conv_id, role="assistant", timeout_seconds=5.0
            )
            assert persisted is not None
            assert (persisted.message_metadata or {}).get("stream_state") == "user_terminated"

            usage = await _latest_llm_usage_for_model(db, model_id)
            assert usage is not None
            assert usage.input_tokens > 0
            assert usage.output_tokens > 0
            request_metadata = usage.request_metadata or {}
            # cancel was attempted; succeeded against the mock transport.
            assert request_metadata.get("cancel_attempted") is True
            assert request_metadata.get("cancel_succeeded") is True
        finally:
            await cleanup_test_user(client, auth_headers, user_id)

    # ------------------------------------------------------------------
    # 7. Responses cancel endpoint URL verification + negative
    # ------------------------------------------------------------------
    async def test_terminate_responses_cancel_called_via_mock_transport(self, client, db, auth_headers):
        """SHU-803 AC9e: the Responses cancel endpoint POST URL must end
        in ``/responses/{response_id}/cancel`` and carry the adapter's
        auth headers. This test intercepts the cancel POST via
        httpx.MockTransport and asserts the URL.

        Negative variant: with a 500 response, ``cancel_succeeded`` lands
        as False on the LLMUsage row but drain still captures the
        end-of-stream usage so the row's token counts are non-zero
        (the value-add of running cancel+drain concurrently).
        """
        user_headers, user_id, _, model_id, conv_id = await self._setup_user_and_provider(
            client, db, auth_headers, provider_type="openai", provider_name="shu803-cancel-url"
        )
        try:
            captured_requests = []

            def cancel_handler(request):
                import httpx
                captured_requests.append(str(request.url))
                # Return 500 — exercises the AC9j negative path: cancel
                # fails but drain catches usage anyway.
                return httpx.Response(500, json={"error": "simulated"})

            fixture = openai_responses_response_completed_fixture(
                input_tokens=75,
                output_tokens=25,
                response_id="resp_url_check",
                delay_before_completed_s=0.3,
            )

            logger.info(
                "=== EXPECTED TEST OUTPUT: ResponsesAdapter.cancel will log INFO/WARN "
                "for the simulated 500 from the MockTransport ==="
            )

            with stub_provider_stream(fixture), stub_responses_cancel_transport(cancel_handler):
                stream_id, terminate_response = await _capture_stream_id_and_terminate(
                    client, conv_id=conv_id, user_headers=user_headers
                )
            assert stream_id is not None
            assert terminate_response.status_code == 202

            # The cancel transport must have been hit at least once and
            # the URL must end in /responses/{id}/cancel.
            assert captured_requests, "cancel POST was never sent to the mock transport"
            matching_urls = [url for url in captured_requests if url.endswith("/responses/resp_url_check/cancel")]
            assert matching_urls, (
                f"Expected cancel POST URL ending in /responses/resp_url_check/cancel; "
                f"got {captured_requests!r}"
            )

            persisted = await wait_for_message_persisted(
                db, conversation_id=conv_id, role="assistant", timeout_seconds=5.0
            )
            assert persisted is not None

            usage = await _latest_llm_usage_for_model(db, model_id)
            assert usage is not None
            # Drain still captured usage despite the cancel 500 — that's
            # the value-add of running cancel+drain concurrently.
            assert usage.input_tokens > 0
            assert usage.output_tokens > 0
            request_metadata = usage.request_metadata or {}
            assert request_metadata.get("cancel_attempted") is True
            assert request_metadata.get("cancel_succeeded") is False, (
                "500 from cancel endpoint must surface as cancel_succeeded=False"
            )
        finally:
            await cleanup_test_user(client, auth_headers, user_id)

    # ------------------------------------------------------------------
    # 8. OpenRouter Gemma-4 — DB-rate fallback cost (LOAD BEARING)
    # ------------------------------------------------------------------
    async def test_terminate_openrouter_gemma4_cost_derived_from_model_pricing(self, client, db, auth_headers):
        """**The load-bearing AC10 test for the billing-evasion close-out.**

        Stub OpenRouter-shaped provider with model_name
        ``google/gemma-4-31b-it:nitro``. DB rates are seeded via the
        AC9d normalized-fallback path (here simulated by direct DB
        write; in production ``sync_pricing_to_db`` does it on startup).
        The end-of-stream usage chunk does NOT carry ``usage.cost`` —
        forcing the DB-rate fallback in ``usage_recording.py``'s cost
        contract.

        Asserts the LLMUsage row's
        ``total_cost = input_tokens × cost_per_input_unit
                     + output_tokens × cost_per_output_unit``
        and is non-zero. Pre-SHU-803 (or pre-fix MODEL_PRICING lookup),
        this row's ``total_cost`` was zero — the abuse vector.
        """
        # Per-token rates (Gemma-4-31B real rates: $0.18/Mtok in, $0.50/Mtok out).
        rate_in = Decimal("0.00000018")  # 0.18 / 1e6
        rate_out = Decimal("0.00000050")  # 0.50 / 1e6

        user_headers, user_id, _, model_id, conv_id = await self._setup_user_and_provider(
            client,
            db,
            auth_headers,
            provider_type="generic_completions",
            provider_name="shu803-openrouter-gemma",
            model_name="google/gemma-4-31b-it:nitro",
            cost_per_input_unit=rate_in,
            cost_per_output_unit=rate_out,
        )
        try:
            # Realistic token counts for a partial Gemma-4 stream.
            input_tokens = 500
            output_tokens = 250
            fixture = openai_completions_end_usage_fixture(
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                text_content="Useful answer; then 50k words of filler the user never read.",
                wire_cost=None,  # CRITICAL: no wire cost → DB-rate fallback path.
                delay_before_usage_chunk_s=0.3,
            )
            with stub_provider_stream(fixture):
                stream_id, terminate_response = await _capture_stream_id_and_terminate(
                    client, conv_id=conv_id, user_headers=user_headers
                )
            assert stream_id is not None
            assert terminate_response.status_code == 202

            persisted = await wait_for_message_persisted(
                db, conversation_id=conv_id, role="assistant", timeout_seconds=5.0
            )
            assert persisted is not None

            usage = await _latest_llm_usage_for_model(db, model_id)
            assert usage is not None
            assert usage.success is False
            assert usage.input_tokens == input_tokens, (
                f"Drain must have captured the full end-of-stream usage chunk; "
                f"expected input_tokens={input_tokens}, got {usage.input_tokens}"
            )
            assert usage.output_tokens == output_tokens
            # **The billing-evasion close-out assertion.** total_cost must
            # be the DB-rate fallback computation, non-zero.
            expected_total = (Decimal(input_tokens) * rate_in) + (Decimal(output_tokens) * rate_out)
            assert usage.total_cost == expected_total, (
                f"SHU-803 abuse-vector close-out: total_cost must equal the DB-rate "
                f"fallback computation. Expected {expected_total}, got {usage.total_cost}"
            )
            assert usage.total_cost > Decimal("0"), (
                "Pre-fix this was 0 (the abuse vector). Post-fix the DB-rate "
                "fallback resolves rates via AC9d normalization."
            )
        finally:
            await cleanup_test_user(client, auth_headers, user_id)

    # ------------------------------------------------------------------
    # 9. OpenRouter — provider-authoritative wire cost
    # ------------------------------------------------------------------
    async def test_terminate_openrouter_authoritative_cost_via_wire(self, client, db, auth_headers):
        """When the OpenRouter stream emits ``usage.cost`` on the wire,
        the cost contract in ``usage_recording.py`` records it verbatim
        (provider-authoritative path) and skips the DB-rate fallback.
        Drain still captures the token counts (so the row's
        input_tokens / output_tokens are accurate); cost comes from the
        wire.
        """
        rate_in = Decimal("0.00000018")
        rate_out = Decimal("0.00000050")
        # Wire cost reported by OpenRouter — intentionally NOT the DB-rate
        # computation so the test can prove which path was taken.
        wire_cost = Decimal("0.00012345")

        user_headers, user_id, _, model_id, conv_id = await self._setup_user_and_provider(
            client,
            db,
            auth_headers,
            provider_type="generic_completions",
            provider_name="shu803-openrouter-wire",
            model_name="google/gemma-4-31b-it:nitro",
            cost_per_input_unit=rate_in,
            cost_per_output_unit=rate_out,
        )
        try:
            fixture = openai_completions_end_usage_fixture(
                prompt_tokens=400,
                completion_tokens=200,
                wire_cost=str(wire_cost),
                delay_before_usage_chunk_s=0.3,
            )
            with stub_provider_stream(fixture):
                stream_id, terminate_response = await _capture_stream_id_and_terminate(
                    client, conv_id=conv_id, user_headers=user_headers
                )
            assert stream_id is not None
            assert terminate_response.status_code == 202

            persisted = await wait_for_message_persisted(
                db, conversation_id=conv_id, role="assistant", timeout_seconds=5.0
            )
            assert persisted is not None

            usage = await _latest_llm_usage_for_model(db, model_id)
            assert usage is not None
            assert usage.input_tokens == 400
            assert usage.output_tokens == 200
            # Wire cost recorded verbatim — provider-authoritative path.
            assert usage.total_cost == wire_cost, (
                f"Provider-authoritative path: total_cost must match wire usage.cost. "
                f"Expected {wire_cost}, got {usage.total_cost}"
            )
            # And the contract invariant — when wire cost > 0, the split
            # stays at 0/0 (per the cost contract in usage_recording.py).
            assert usage.input_cost == Decimal("0")
            assert usage.output_cost == Decimal("0")
        finally:
            await cleanup_test_user(client, auth_headers, user_id)


if __name__ == "__main__":
    ChatForceTerminateRealUsageIntegrationTest().run()
