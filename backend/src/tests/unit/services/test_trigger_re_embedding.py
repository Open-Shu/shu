"""Unit tests for KnowledgeBaseService.trigger_re_embedding."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.core.exceptions import ConflictError
from shu.services.knowledge_base_service import KnowledgeBaseService


def _scalar_one_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _scalar_result(value):
    result = MagicMock()
    result.scalar.return_value = value
    return result


@pytest.mark.asyncio
async def test_trigger_re_embedding_rejects_fresh_in_progress_kb():
    """Active/fresh re-embedding should remain a conflict."""
    mock_db = AsyncMock()
    service = KnowledgeBaseService(mock_db)

    kb = MagicMock()
    kb.embedding_model = "old-model"
    kb.embedding_status = "re_embedding"
    kb.updated_at = datetime.now(UTC) - timedelta(seconds=30)

    mock_db.execute = AsyncMock(return_value=_scalar_one_result(kb))

    embedding_service = SimpleNamespace(model_name="new-model")
    queue_backend = AsyncMock()

    with pytest.raises(ConflictError, match="already in progress"):
        await service.trigger_re_embedding("kb-1", embedding_service=embedding_service, queue_backend=queue_backend)


@pytest.mark.asyncio
async def test_trigger_re_embedding_allows_stale_in_progress_kb():
    """Stale in-progress KB should be retriggerable."""
    mock_db = AsyncMock()
    service = KnowledgeBaseService(mock_db)

    kb = MagicMock()
    kb.embedding_model = "old-model"
    kb.embedding_status = "re_embedding"
    kb.re_embedding_progress = {"phase": "chunks"}
    kb.updated_at = datetime.now(UTC) - timedelta(minutes=10)

    mock_db.execute = AsyncMock(
        side_effect=[
            _scalar_one_result(kb),  # SELECT ... FOR UPDATE KB
            _scalar_result(5),  # chunk count
        ]
    )
    mock_db.commit = AsyncMock()

    embedding_service = SimpleNamespace(model_name="new-model")
    queue_backend = AsyncMock()

    with (
        patch("shu.core.config.get_settings_instance", return_value=SimpleNamespace(worker_concurrency=4)),
        patch("shu.core.workload_routing.enqueue_job", new_callable=AsyncMock) as mock_enqueue,
    ):
        result = await service.trigger_re_embedding(
            "kb-1",
            embedding_service=embedding_service,
            queue_backend=queue_backend,
        )

    kb.mark_re_embedding_started.assert_called_once()
    assert mock_enqueue.await_count >= 1
    assert result["status"] == "queued"
    assert result["knowledge_base_id"] == "kb-1"
    assert result["total_chunks"] == 5
