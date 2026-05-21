"""Unit tests for PromptService — currently scoped to the CP provisioning path.

Older PromptService coverage lives in `tests/unit/api/test_prompts.py` (the
generalized prompt API tests). This file adds tests for the CP-driven
upsert added under SHU-785.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from shu.models.prompt import EntityType
from shu.schemas.cp_provisioning import PromptInput, SetPromptRequest
from shu.services.prompt_service import PromptService


def _payload(
    *,
    name: str = "tenant-default",
    content: str = "You are a careful assistant.",
) -> SetPromptRequest:
    return SetPromptRequest(
        prompt=PromptInput(name=name, content=content),
        reason="cp set prompt",
    )


def _make_cp_service(
    *,
    existing_prompt: object | None = None,
) -> tuple[PromptService, MagicMock, AsyncMock]:
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    existing_result = MagicMock()
    existing_result.scalar_one_or_none = MagicMock(return_value=existing_prompt)
    session.execute = AsyncMock(return_value=existing_result)

    @asynccontextmanager
    async def _impersonate(tenant_id, actor, reason):
        yield session

    tenant_admin_svc = MagicMock()
    tenant_admin_svc.impersonate_tenant = _impersonate

    audit = AsyncMock()

    svc = PromptService(
        db=MagicMock(),
        tenant_admin_svc=tenant_admin_svc,
        audit_logger=audit,
    )
    return svc, session, audit


class TestCpUpsertByName:
    @pytest.mark.asyncio
    async def test_inserts_new_prompt_with_is_system_default_true(self) -> None:
        svc, session, audit = _make_cp_service(existing_prompt=None)

        # Capture the inserted row + assign an id on flush.
        added: list = []
        session.add.side_effect = lambda obj: added.append(obj)

        async def _flush() -> None:
            if added and added[-1].id is None:
                added[-1].id = "prompt-new-1"
        session.flush.side_effect = _flush

        resp = await svc.cp_upsert_by_name("tenant-1", _payload(), reason="r")

        assert resp.prompt_id == "prompt-new-1"
        assert len(added) == 1
        inserted = added[0]
        assert inserted.is_system_default is True
        assert inserted.is_active is True
        assert inserted.entity_type == EntityType.LLM_MODEL  # default
        session.commit.assert_awaited_once()

        events = [c.kwargs.get("event") for c in audit.log.await_args_list]
        assert events == ["cp_prompt_inserted"]

    @pytest.mark.asyncio
    async def test_upsert_existing_preserves_id_and_updates_content(self) -> None:
        existing = MagicMock(id="prompt-stable-1", content="old")
        svc, session, audit = _make_cp_service(existing_prompt=existing)

        resp = await svc.cp_upsert_by_name(
            "tenant-1",
            _payload(content="new content"),
            reason="r",
        )

        assert resp.prompt_id == "prompt-stable-1"  # id preserved
        assert existing.content == "new content"
        session.add.assert_not_called()  # no new INSERT
        events = [c.kwargs.get("event") for c in audit.log.await_args_list]
        assert events == ["cp_prompt_updated"]

    @pytest.mark.asyncio
    async def test_existence_lookup_filters_by_llm_model_entity_type(self) -> None:
        """The upsert lookup MUST filter on entity_type=llm_model so that a
        prompt sharing the same name in a different entity_type can't be
        accidentally targeted. Without this filter, the MC resolver's
        prompt_name lookup would have the same ambiguity from the read side.
        """
        svc, session, _ = _make_cp_service(existing_prompt=None)
        added: list = []
        session.add.side_effect = lambda obj: added.append(obj)

        async def _flush() -> None:
            if added and added[-1].id is None:
                added[-1].id = "prompt-1"

        session.flush.side_effect = _flush

        await svc.cp_upsert_by_name("tenant-1", _payload(), reason="r")

        # Newly inserted prompt is pinned to LLM_MODEL.
        assert added[0].entity_type == EntityType.LLM_MODEL

    @pytest.mark.asyncio
    async def test_missing_deps_raises_runtime_error(self) -> None:
        svc = PromptService(db=MagicMock())
        with pytest.raises(RuntimeError, match="tenant_admin_svc and audit_logger"):
            await svc.cp_upsert_by_name("tenant-1", _payload(), reason="r")
