"""
Unit tests for knowledge_bases API endpoint.

Tests cover:
- list_knowledge_bases is accessible to regular (non-power) users
- User ID is passed to the service for PBAC filtering
- Service results are correctly formatted in the response
- create_knowledge_base accepts any authenticated user and stamps owner_id
- Subscription gate (SHU-703) on POST /{kb_id}/documents/upload
"""

import io
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from shu.api.dependencies import get_db
from shu.api.knowledge_bases import (
    create_knowledge_base,
    list_knowledge_bases,
    router as kb_router,
)
from shu.auth.rbac import require_kb_write_access
from shu.billing.cp_client import BillingState
from shu.schemas.knowledge_base import KnowledgeBaseCreate
from tests.unit.api.conftest import make_app_with_router


def _mock_user(user_id: str = "user-1"):
    """Build a mock User."""
    user = MagicMock()
    user.id = user_id
    return user


def _mock_kb(kb_id: str = "kb-1", name: str = "Test KB"):
    """Build a mock KnowledgeBase with all fields the endpoint reads."""
    kb = MagicMock()
    kb.id = kb_id
    kb.slug = f"slug-{kb_id}"
    kb.name = name
    kb.description = "desc"
    kb.sync_enabled = True
    kb.embedding_model = "all-MiniLM-L6-v2"
    kb.chunk_size = 512
    kb.chunk_overlap = 50
    kb.status = "active"
    kb.embedding_status = "current"
    kb.re_embedding_progress = None
    kb.document_count = 10
    kb.total_chunks = 100
    kb.last_sync_at = None
    kb.created_at = datetime(2025, 1, 1, tzinfo=UTC)
    return kb


class TestListKnowledgeBases:
    """Tests for the list_knowledge_bases endpoint."""

    @pytest.mark.asyncio
    async def test_regular_user_can_list_kbs(self):
        """Endpoint is accessible to regular users (not just power users)."""
        current_user = _mock_user("regular-user-1")
        db = AsyncMock()
        mock_kb = _mock_kb("kb-1", "My KB")

        with patch("shu.api.knowledge_bases.KnowledgeBaseService") as mock_svc_class:
            mock_svc = MagicMock()
            mock_svc.list_knowledge_bases = AsyncMock(return_value=([mock_kb], 1))
            mock_svc_class.return_value = mock_svc

            response = await list_knowledge_bases(
                limit=50, offset=0, search=None, current_user=current_user, db=db,
            )

            mock_svc.list_knowledge_bases.assert_awaited_once_with(
                user_id="regular-user-1",
                limit=50,
                offset=0,
                search=None,
            )

            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_user_id_passed_for_pbac_filtering(self):
        """The current user's ID is forwarded to the service for PBAC enforcement."""
        current_user = _mock_user("specific-user-42")
        db = AsyncMock()

        with patch("shu.api.knowledge_bases.KnowledgeBaseService") as mock_svc_class:
            mock_svc = MagicMock()
            mock_svc.list_knowledge_bases = AsyncMock(return_value=([], 0))
            mock_svc_class.return_value = mock_svc

            await list_knowledge_bases(
                limit=10, offset=5, search="test", current_user=current_user, db=db,
            )

            mock_svc.list_knowledge_bases.assert_awaited_once_with(
                user_id="specific-user-42",
                limit=10,
                offset=5,
                search="test",
            )

    @pytest.mark.asyncio
    async def test_empty_kb_list_returns_empty_response(self):
        """When user has no accessible KBs, response contains empty list."""
        current_user = _mock_user()
        db = AsyncMock()

        with patch("shu.api.knowledge_bases.KnowledgeBaseService") as mock_svc_class:
            mock_svc = MagicMock()
            mock_svc.list_knowledge_bases = AsyncMock(return_value=([], 0))
            mock_svc_class.return_value = mock_svc

            response = await list_knowledge_bases(
                limit=50, offset=0, search=None, current_user=current_user, db=db,
            )

            assert response.status_code == 200


class TestCreateKnowledgeBase:
    """Tests for the create_knowledge_base endpoint."""

    @pytest.mark.asyncio
    async def test_regular_user_can_create_kb_and_owner_id_is_set(self):
        """Regular users can create KBs; the new KB is stamped with owner_id = current_user.id."""
        current_user = _mock_user("regular-user-99")
        kb_data = KnowledgeBaseCreate(name="My Personal Knowledge")
        db = AsyncMock()

        with patch("shu.api.knowledge_bases.KnowledgeBaseService") as mock_svc_class:
            mock_svc = MagicMock()
            created_kb = _mock_kb("new-kb-1", "My Personal Knowledge")
            created_kb.updated_at = datetime(2026, 5, 1, tzinfo=UTC)
            mock_svc.create_knowledge_base = AsyncMock(return_value=created_kb)
            mock_svc_class.return_value = mock_svc

            response = await create_knowledge_base(kb_data, current_user, db)

            mock_svc.create_knowledge_base.assert_awaited_once()
            call_kwargs = mock_svc.create_knowledge_base.call_args.kwargs
            assert call_kwargs["owner_id"] == "regular-user-99"
            assert response.status_code == 201

    @pytest.mark.asyncio
    async def test_owner_id_uses_current_user_id_verbatim(self):
        """No transformation on the user id — it's passed straight through to the service."""
        current_user = _mock_user("00000000-1111-2222-3333-444444444444")
        kb_data = KnowledgeBaseCreate(name="Project KB")
        db = AsyncMock()

        with patch("shu.api.knowledge_bases.KnowledgeBaseService") as mock_svc_class:
            mock_svc = MagicMock()
            created_kb = _mock_kb("new-kb-2", "Project KB")
            created_kb.updated_at = datetime(2026, 5, 1, tzinfo=UTC)
            mock_svc.create_knowledge_base = AsyncMock(return_value=created_kb)
            mock_svc_class.return_value = mock_svc

            await create_knowledge_base(kb_data, current_user, db)

            call_kwargs = mock_svc.create_knowledge_base.call_args.kwargs
            assert call_kwargs["owner_id"] == "00000000-1111-2222-3333-444444444444"


# Subscription-gating tests below need FastAPI's dependency-resolution machinery
# to fire. Calling the route function directly bypasses Depends() defaults, so
# the dep never raises and we cannot observe the 402 short-circuit. TestClient
# is the minimum surface that exercises the actual gate behavior — the gate is
# a framework concern, not application logic. Mirrors the deviation called out
# in test_chat.py for the same reason.


class TestUploadDocumentsSubscriptionGate:
    """The subscription gate must fire before staging or job enqueue."""

    def test_inactive_subscription_returns_402_and_blocks_ingestion(self, install_stub_cache):
        """Disabled key → 402 JSON envelope; no staging write, no OCR enqueue."""
        failed_at = datetime(2026, 1, 1, tzinfo=UTC)
        install_stub_cache(
            BillingState(
                openrouter_key_disabled=True,
                payment_failed_at=failed_at,
                payment_grace_days=7,
            )
        )

        app = make_app_with_router(kb_router)
        fake_user = _mock_user("user-123")
        fake_db = AsyncMock()

        app.dependency_overrides[require_kb_write_access] = lambda: fake_user
        app.dependency_overrides[get_db] = lambda: fake_db

        # Patch the route's local entry point to ingestion plus the lazy-imported
        # staging class and enqueue_job at their definition sites. The gate must
        # short-circuit before any of these are reachable.
        with (
            patch("shu.api.knowledge_bases.ingest_document_service") as mock_ingest,
            patch("shu.services.file_staging_service.FileStagingService") as mock_staging_cls,
            patch("shu.core.workload_routing.enqueue_job") as mock_enqueue,
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/knowledge-bases/kb-1/documents/upload",
                    files={"files": ("test.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
                )

            assert response.status_code == 402
            assert response.headers["content-type"].startswith("application/json")

            body = response.json()
            assert body["error"]["code"] == "subscription_inactive"
            assert body["error"]["details"]["payment_failed_at"] == failed_at.isoformat()
            assert body["error"]["details"]["grace_deadline"] is not None

            # The gate must fire BEFORE the route reads bytes or stages anything.
            mock_ingest.assert_not_called()
            mock_staging_cls.assert_not_called()
            mock_enqueue.assert_not_called()
