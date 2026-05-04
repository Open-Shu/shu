"""
Unit tests for chat API schemas and route-level subscription gating.

Tests cover:
- SendMessageRequest accepts knowledge_base_ids as valid list, empty list, or null
- SendMessageRequest rejects the old singular knowledge_base_id field (extra="forbid")
- Subscription gate (SHU-703) on /chat/conversations/{id}/send and /chat/messages/{id}/regenerate
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from shu.api.chat import SendMessageRequest, router as chat_router
from shu.api.dependencies import get_db
from shu.auth.rbac import get_current_user
from shu.billing.cp_client import BillingState
from shu.core.config import get_config_manager_dependency
from shu.core.exceptions import ShuException


class TestSendMessageRequestKBIds:
    """Validate knowledge_base_ids field on SendMessageRequest."""

    def test_valid_list(self):
        """A list of KB IDs is accepted."""
        req = SendMessageRequest(message="hi", knowledge_base_ids=["kb-1", "kb-2"])
        assert req.knowledge_base_ids == ["kb-1", "kb-2"]

    def test_empty_list(self):
        """An empty list is accepted and stored as-is."""
        req = SendMessageRequest(message="hi", knowledge_base_ids=[])
        assert req.knowledge_base_ids == []

    def test_null(self):
        """Omitting the field defaults to None."""
        req = SendMessageRequest(message="hi")
        assert req.knowledge_base_ids is None

    def test_explicit_none(self):
        """Explicitly passing None is accepted."""
        req = SendMessageRequest(message="hi", knowledge_base_ids=None)
        assert req.knowledge_base_ids is None

    def test_singular_field_rejected(self):
        """The old knowledge_base_id (singular) is rejected by extra='forbid'."""
        with pytest.raises(ValidationError, match="knowledge_base_id"):
            SendMessageRequest(message="hi", knowledge_base_id="kb-1")


# Subscription-gating tests below need FastAPI's dependency-resolution machinery
# to fire. Calling the route function directly bypasses Depends() defaults, so
# the dep never raises and we cannot observe the 402 short-circuit. TestClient
# is the minimum surface that exercises the actual gate behavior — the gate is
# a framework concern, not application logic. Flagged as a deviation from the
# "API unit tests should call endpoint functions directly" pattern.


@pytest.fixture
def mock_chat_service():
    """Mock the ChatService class so we can assert it was never instantiated/called.

    Patched at the chat module's import site (not shu.services.chat_service) so
    the patch reaches the symbol the route actually references.
    """
    with patch("shu.api.chat.ChatService") as cls:
        instance = MagicMock()
        instance.get_conversation_by_id = AsyncMock()
        instance.send_message = AsyncMock()
        instance.regenerate_message = AsyncMock()
        cls.return_value = instance
        yield instance


def _build_app() -> FastAPI:
    """Minimal FastAPI app with just the chat router + the production ShuException handler.

    Spinning up the full main.py app pulls in DB init, settings validation, etc.
    The 402 gate behavior depends only on Depends-resolution + the global
    ShuException handler, both of which we can wire up here directly.
    """
    app = FastAPI()
    app.include_router(chat_router, prefix="/api/v1")

    # WHY — re-implement the same handler shape as main.py:658 so the wire
    # format under test matches production. Importing setup_exception_handlers
    # would drag in get_settings_instance and full app config.
    @app.exception_handler(ShuException)
    async def _shu_exception_handler(request, exc: ShuException):  # noqa: ARG001
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.error_code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    return app


@pytest.fixture
def client_with_overrides(mock_chat_service):
    """TestClient with auth/db/config deps stubbed so the chat gate is the only thing
    that can fail the request.
    """
    app = _build_app()

    fake_user = MagicMock(id="user-123")
    fake_db = AsyncMock()
    fake_config_manager = MagicMock()

    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_config_manager_dependency] = lambda: fake_config_manager

    with TestClient(app) as client:
        yield client, fake_user


class TestSendMessageSubscriptionGate:
    """The subscription gate must fire before the route body or service call."""

    def test_inactive_subscription_returns_402_json(self, install_stub_cache, client_with_overrides, mock_chat_service):
        """Disabled key → 402 JSON envelope, NOT a half-streamed SSE."""
        failed_at = datetime(2026, 1, 1, tzinfo=UTC)
        install_stub_cache(
            BillingState(
                openrouter_key_disabled=True,
                payment_failed_at=failed_at,
                payment_grace_days=7,
            )
        )
        client, _ = client_with_overrides

        response = client.post(
            "/api/v1/chat/conversations/conv-1/send",
            json={"message": "hello"},
        )

        assert response.status_code == 402
        assert response.headers["content-type"].startswith("application/json")
        assert not response.headers["content-type"].startswith("text/event-stream")

        body = response.json()
        assert body["error"]["code"] == "subscription_inactive"
        assert body["error"]["details"]["payment_failed_at"] == failed_at.isoformat()
        assert body["error"]["details"]["grace_deadline"] is not None

        # The dep must fire BEFORE service instantiation — if ChatService were
        # constructed, send_message would have at least been reachable.
        mock_chat_service.send_message.assert_not_called()
        mock_chat_service.get_conversation_by_id.assert_not_called()


class TestRegenerateMessageSubscriptionGate:
    """Same gate guarantee on the regenerate endpoint."""

    def test_inactive_subscription_returns_402_json(self, install_stub_cache, client_with_overrides, mock_chat_service):
        """Disabled key → 402 JSON envelope, NOT a half-streamed SSE."""
        failed_at = datetime(2026, 1, 1, tzinfo=UTC)
        install_stub_cache(
            BillingState(
                openrouter_key_disabled=True,
                payment_failed_at=failed_at,
                payment_grace_days=7,
            )
        )
        client, _ = client_with_overrides

        response = client.post(
            "/api/v1/chat/messages/msg-1/regenerate",
            json={},
        )

        assert response.status_code == 402
        assert response.headers["content-type"].startswith("application/json")
        assert not response.headers["content-type"].startswith("text/event-stream")

        body = response.json()
        assert body["error"]["code"] == "subscription_inactive"
        assert body["error"]["details"]["payment_failed_at"] == failed_at.isoformat()

        mock_chat_service.regenerate_message.assert_not_called()


class TestSseInFlightInvariant:
    """Pin the design's "dep runs once at request start" guarantee.

    A future refactor that adds a mid-stream re-check would break the contract
    that an in-flight SSE response runs to natural completion. This test
    locks that behavior in by counting helper invocations across a full
    request/response cycle on the active path.
    """

    def test_active_subscription_streams_sse_and_dep_called_once(self, mock_chat_service):
        """Active subscription → SSE Content-Type, helper invoked exactly once."""
        call_count = {"n": 0}

        async def counting_assert_active() -> None:
            call_count["n"] += 1
            # Healthy — no raise.

        # Patch the symbol the route actually references. Depends(...) captures
        # the function object at route-definition time, so we have to reach
        # into the running APIRouter and override via dependency_overrides.
        from shu.api.chat import assert_subscription_active as real_helper

        app = _build_app()
        fake_user = MagicMock(id="user-123")
        fake_db = AsyncMock()
        fake_config_manager = MagicMock()

        app.dependency_overrides[get_current_user] = lambda: fake_user
        app.dependency_overrides[get_db] = lambda: fake_db
        app.dependency_overrides[get_config_manager_dependency] = lambda: fake_config_manager
        app.dependency_overrides[real_helper] = counting_assert_active

        # Make the conversation lookup pass and the stream yield a single event
        # so we can observe the response Content-Type without running an LLM.
        fake_conversation = MagicMock(user_id="user-123")
        mock_chat_service.get_conversation_by_id.return_value = fake_conversation

        async def _fake_event_gen():
            yield {"type": "done"}

        async def _send_message_stub(**_kwargs):
            return _fake_event_gen()

        mock_chat_service.send_message.side_effect = _send_message_stub

        with TestClient(app) as client:
            response = client.post(
                "/api/v1/chat/conversations/conv-1/send",
                json={"message": "hello"},
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        # Locks the design invariant: the dep is a request-start gate, not a
        # mid-stream re-check. If a future change adds re-validation inside
        # the generator this count flips and the test fails loudly.
        assert call_count["n"] == 1
