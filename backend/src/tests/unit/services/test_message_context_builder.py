"""
Unit tests for MessageContextBuilder._get_rag_sections knowledge_base_ids handling.

Tests cover:
- Explicit knowledge_base_ids are passed directly to execute_rag_queries
- None knowledge_base_ids falls back to model config KBs
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.schemas.query import RagRewriteMode
from shu.services.message_context_builder import MessageContextBuilder


def _make_builder() -> MessageContextBuilder:
    """Create a MessageContextBuilder with mocked dependencies."""
    db_session = AsyncMock()
    config_manager = MagicMock()
    config_manager.get_rag_search_type.return_value = "hybrid"
    config_manager.get_rag_max_chunks.return_value = 5
    config_manager.get_rag_search_threshold.return_value = 0.5
    llm_service = MagicMock()
    prompt_service = MagicMock()
    query_service = MagicMock()
    return MessageContextBuilder(
        db_session=db_session,
        config_manager=config_manager,
        llm_service=llm_service,
        prompt_service=prompt_service,
        query_service=query_service,
        context_window_manager=MagicMock(),
        context_preferences_resolver=MagicMock(),
        diagnostics_target=MagicMock(),
    )


def _mock_conversation(model_config=None):
    """Build a mock Conversation with optional model_configuration."""
    conv = MagicMock()
    conv.id = "conv-1"
    if model_config is not None:
        conv.model_configuration = model_config
    else:
        conv.model_configuration = None
    return conv


def _mock_user(user_id: str = "user-1"):
    user = MagicMock()
    user.id = user_id
    return user


class TestGetRagSectionsKBIds:
    """Tests for _get_rag_sections knowledge_base_ids routing."""

    @pytest.mark.asyncio
    async def test_explicit_kb_ids_passed_to_execute_rag_queries(self):
        """When knowledge_base_ids is provided, those IDs are passed directly."""
        builder = _make_builder()
        conversation = _mock_conversation()
        current_user = _mock_user()
        model = MagicMock()

        with patch(
            "shu.services.message_context_builder.execute_rag_queries",
            new_callable=AsyncMock,
            return_value=("rewritten", None, []),
        ) as mock_exec:
            await builder._get_rag_sections(
                conversation=conversation,
                user_message="test query",
                current_user=current_user,
                knowledge_base_ids=["kb-1", "kb-2"],
                rag_rewrite_mode=RagRewriteMode.RAW_QUERY,
                model=model,
                conversation_messages=[],
            )

            mock_exec.assert_awaited_once()
            call_kwargs = mock_exec.call_args.kwargs
            assert call_kwargs["knowledge_base_ids"] == ["kb-1", "kb-2"]

    @pytest.mark.asyncio
    async def test_none_kb_ids_falls_back_to_model_config_kbs(self):
        """When knowledge_base_ids is None, KBs come from the model config."""
        builder = _make_builder()

        mock_kb = MagicMock()
        mock_kb.id = "config-kb-1"
        mock_kb.is_active = True

        model_config = MagicMock()
        model_config.knowledge_bases = [mock_kb]

        conversation = _mock_conversation(model_config=model_config)
        current_user = _mock_user()
        model = MagicMock()

        with patch(
            "shu.services.message_context_builder.KnowledgeBaseService"
        ) as mock_kb_svc_class, patch(
            "shu.services.message_context_builder.execute_rag_queries",
            new_callable=AsyncMock,
            return_value=("rewritten", None, []),
        ) as mock_exec:
            mock_kb_svc = MagicMock()
            mock_kb_svc.filter_accessible_kb_ids = AsyncMock(return_value=["config-kb-1"])
            mock_kb_svc_class.return_value = mock_kb_svc

            await builder._get_rag_sections(
                conversation=conversation,
                user_message="test query",
                current_user=current_user,
                knowledge_base_ids=None,
                rag_rewrite_mode=RagRewriteMode.RAW_QUERY,
                model=model,
                conversation_messages=[],
            )

            mock_exec.assert_awaited_once()
            call_kwargs = mock_exec.call_args.kwargs
            assert call_kwargs["knowledge_base_ids"] == ["config-kb-1"]

    @pytest.mark.asyncio
    async def test_none_kb_ids_no_model_config_returns_empty(self):
        """When knowledge_base_ids is None and no model config KBs, returns empty."""
        builder = _make_builder()
        conversation = _mock_conversation()
        current_user = _mock_user()
        model = MagicMock()

        with patch(
            "shu.services.message_context_builder.execute_rag_queries",
            new_callable=AsyncMock,
        ) as mock_exec:
            sections, metadata = await builder._get_rag_sections(
                conversation=conversation,
                user_message="test query",
                current_user=current_user,
                knowledge_base_ids=None,
                rag_rewrite_mode=RagRewriteMode.RAW_QUERY,
                model=model,
                conversation_messages=[],
            )

            mock_exec.assert_not_awaited()
            assert sections == []
            assert metadata == []
