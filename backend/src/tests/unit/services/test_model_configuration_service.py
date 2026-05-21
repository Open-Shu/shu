"""Unit tests for ModelConfigurationService."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.core.exceptions import NotFoundError
from shu.schemas.cp_provisioning import ModelConfigInput, SetModelConfigsRequest
from shu.services.model_configuration_service import ModelConfigurationService
from shu.services.side_call_settings import (
    PROFILING_MODEL_SETTING_KEY,
    SIDE_CALL_MODEL_SETTING_KEY,
)


class TestGetModelConfigurationLogging:
    """Denied-config logging should avoid PII."""

    @pytest.mark.asyncio
    async def test_denied_config_warning_logs_user_id_not_email(self) -> None:
        db = AsyncMock()
        result = MagicMock()

        config = MagicMock()
        config.id = "cfg-1"
        config.knowledge_bases = [MagicMock(id="kb-1"), MagicMock(id="kb-2")]
        result.scalar_one_or_none.return_value = config
        db.execute.return_value = result

        current_user = MagicMock()
        current_user.id = "user-1"
        current_user.email = "pii@example.com"

        service = ModelConfigurationService(db)

        with patch("shu.services.model_configuration_service.KnowledgeBaseService") as mock_kb_service_class, \
             patch("shu.services.model_configuration_service.logger.warning") as warning_mock:
            mock_kb_service = MagicMock()
            mock_kb_service.filter_accessible_kb_ids = AsyncMock(return_value=["kb-1"])
            mock_kb_service_class.return_value = mock_kb_service

            result = await service.get_model_configuration("cfg-1", current_user=current_user)

        assert result is None
        warning_mock.assert_called_once()
        message = warning_mock.call_args.args[0]
        assert "user-1" in message
        assert "pii@example.com" not in message


# ---------------------------------------------------------------------------
# cp_upsert_by_name (SHU-785) — provider resolution, idempotent upsert,
# side-call / profiling pointer writes, batch rollback on resolution failure.
# ---------------------------------------------------------------------------


def _make_cfg(
    *,
    name: str = "default",
    provider_name: str = "openai-prod",
    model_name: str = "gpt-4",
    prompt_name: str | None = None,
    parameter_overrides: dict | None = None,
) -> ModelConfigInput:
    return ModelConfigInput(
        name=name,
        provider_name=provider_name,
        model_name=model_name,
        prompt_name=prompt_name,
        parameter_overrides=parameter_overrides,
    )


def _make_payload(
    *,
    configs: list[ModelConfigInput] | None = None,
    side_call_model_config_name: str | None = None,
    profiling_model_config_name: str | None = None,
) -> SetModelConfigsRequest:
    return SetModelConfigsRequest(
        configs=configs if configs is not None else [_make_cfg()],
        side_call_model_config_name=side_call_model_config_name,
        profiling_model_config_name=profiling_model_config_name,
        reason="cp set MCs",
    )


def _scalar_result(value: object) -> MagicMock:
    """Build a result object whose scalar_one_or_none() returns `value`."""
    res = MagicMock()
    res.scalar_one_or_none = MagicMock(return_value=value)
    return res


def _make_cp_service() -> tuple[
    ModelConfigurationService,
    MagicMock,
    AsyncMock,
    AsyncMock,
    AsyncMock,
]:
    """Build a CP-wired ModelConfigurationService.

    Returns (service, session, tenant_admin_svc, audit, session_execute).
    Tests configure `session_execute.side_effect` to a list of result mocks
    matching the service's SELECT order (first_user → provider → model →
    [prompt?] → existing_mc → [pointer lookups]).
    """
    session = MagicMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    @asynccontextmanager
    async def _impersonate(tenant_id, actor, reason):
        yield session

    tenant_admin_svc = MagicMock()
    tenant_admin_svc.impersonate_tenant = _impersonate

    audit = AsyncMock()

    svc = ModelConfigurationService(
        db=MagicMock(),  # unused by cp method
        tenant_admin_svc=tenant_admin_svc,
        audit_logger=audit,
    )
    return svc, session, tenant_admin_svc, audit, session.execute


class TestCpUpsertByName:
    """CP-driven model_configurations upsert + system_settings pointer write."""

    @pytest.mark.asyncio
    async def test_provider_not_found_raises_and_rolls_back_batch(self) -> None:
        svc, session, _, _, execute = _make_cp_service()
        # Order: first_user query → provider query (returns None) → boom.
        execute.side_effect = [
            _scalar_result("user-1"),
            _scalar_result(None),
        ]
        with pytest.raises(NotFoundError, match="llm_provider"):
            await svc.cp_upsert_by_name(
                "tenant-1",
                _make_payload(),
                reason="r",
            )
        session.add.assert_not_called()
        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_prompt_name_unresolved_raises_and_rolls_back_batch(self) -> None:
        svc, session, _, _, execute = _make_cp_service()
        provider = MagicMock(id="prov-1")
        execute.side_effect = [
            _scalar_result("user-1"),
            _scalar_result(provider),
            _scalar_result("model-row-1"),
            _scalar_result(None),  # prompt lookup
        ]
        with pytest.raises(NotFoundError, match="prompt"):
            await svc.cp_upsert_by_name(
                "tenant-1",
                _make_payload(configs=[_make_cfg(prompt_name="missing")]),
                reason="r",
            )
        session.add.assert_not_called()
        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_inserts_new_mc_and_writes_side_call_pointer(self) -> None:
        svc, session, _, audit, execute = _make_cp_service()
        provider = MagicMock(id="prov-1")

        # The session.add will receive both the ModelConfiguration and
        # SystemSetting rows. Capture them to inspect.
        added: list = []
        session.add.side_effect = lambda obj: added.append(obj)

        # After session.add(mc), session.flush sets mc.id. Simulate by
        # patching flush to set the id on the just-added MC.
        async def _flush_side_effect() -> None:
            if added and added[-1].__class__.__name__ == "ModelConfiguration":
                added[-1].id = "mc-new-1"
        session.flush.side_effect = _flush_side_effect

        execute.side_effect = [
            _scalar_result("user-1"),       # first user
            _scalar_result(provider),       # provider lookup
            _scalar_result("model-row-1"),  # model validation
            _scalar_result(None),           # existing MC lookup → none → insert
            _scalar_result(None),           # side-call SystemSetting lookup → none → insert
        ]

        resp = await svc.cp_upsert_by_name(
            "tenant-1",
            _make_payload(side_call_model_config_name="default"),
            reason="r",
        )

        assert resp.config_ids_by_name == {"default": "mc-new-1"}
        assert resp.side_call_model_config_id == "mc-new-1"
        assert resp.profiling_model_config_id is None

        # Pointer-write went to the correct system_settings key.
        ss_rows = [o for o in added if o.__class__.__name__ == "SystemSetting"]
        assert len(ss_rows) == 1
        assert ss_rows[0].key == SIDE_CALL_MODEL_SETTING_KEY
        assert ss_rows[0].value["model_config_id"] == "mc-new-1"

        # Audit: per-MC insert + pointer upsert.
        event_names = [c.kwargs.get("event") for c in audit.log.await_args_list]
        assert "cp_model_config_inserted" in event_names
        assert "cp_system_setting_upserted" in event_names

        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upsert_existing_mc_preserves_id(self) -> None:
        svc, session, _, _, execute = _make_cp_service()
        provider = MagicMock(id="prov-1")
        existing_mc = MagicMock(
            id="mc-stable-1",
            llm_provider_id="prov-old",
            model_name="old-model",
        )

        execute.side_effect = [
            _scalar_result("user-1"),
            _scalar_result(provider),
            _scalar_result("model-row-1"),
            _scalar_result(existing_mc),  # MC already exists
        ]

        resp = await svc.cp_upsert_by_name(
            "tenant-1",
            _make_payload(),
            reason="r",
        )

        # ID is preserved on re-upsert (no new row created); mc fields are
        # written through the model attributes, not a fresh INSERT.
        assert resp.config_ids_by_name == {"default": "mc-stable-1"}
        session.add.assert_not_called()  # no new MC INSERT
        assert existing_mc.llm_provider_id == "prov-1"
        assert existing_mc.model_name == "gpt-4"

    @pytest.mark.asyncio
    async def test_profiling_pointer_can_reference_pre_existing_mc(self) -> None:
        """profiling_model_config_name need not be in the current `configs` batch."""
        svc, session, _, _, execute = _make_cp_service()
        provider = MagicMock(id="prov-1")

        # Set up flush to assign an id when an MC is added.
        added: list = []
        session.add.side_effect = lambda obj: added.append(obj)

        async def _flush_side_effect() -> None:
            if added and added[-1].__class__.__name__ == "ModelConfiguration":
                added[-1].id = "mc-new-1"
        session.flush.side_effect = _flush_side_effect

        execute.side_effect = [
            _scalar_result("user-1"),       # first user
            _scalar_result(provider),       # provider lookup
            _scalar_result("model-row-1"),  # model validation
            _scalar_result(None),           # existing MC lookup → none → insert
            # profiling_model_config_name="legacy-mc" not in batch — lookup tenant
            _scalar_result("mc-legacy-1"),
            _scalar_result(None),           # profiling SystemSetting upsert (insert)
        ]

        resp = await svc.cp_upsert_by_name(
            "tenant-1",
            _make_payload(profiling_model_config_name="legacy-mc"),
            reason="r",
        )

        assert resp.profiling_model_config_id == "mc-legacy-1"
        # Pointer goes into the profiling key, not side-call.
        ss_rows = [o for o in added if o.__class__.__name__ == "SystemSetting"]
        assert any(s.key == PROFILING_MODEL_SETTING_KEY for s in ss_rows)

    @pytest.mark.asyncio
    async def test_pointer_to_unknown_mc_name_raises(self) -> None:
        svc, _, _, _, execute = _make_cp_service()
        provider = MagicMock(id="prov-1")

        execute.side_effect = [
            _scalar_result("user-1"),
            _scalar_result(provider),
            _scalar_result("model-row-1"),
            _scalar_result(None),           # MC insert path
            _scalar_result(None),           # side-call name lookup → not found
        ]

        with pytest.raises(NotFoundError, match="model_configuration"):
            await svc.cp_upsert_by_name(
                "tenant-1",
                _make_payload(side_call_model_config_name="nope"),
                reason="r",
            )

    @pytest.mark.asyncio
    async def test_missing_deps_raises_runtime_error(self) -> None:
        """Calling without injected tenant_admin_svc / audit is a wire-up bug."""
        svc = ModelConfigurationService(db=MagicMock())
        with pytest.raises(RuntimeError, match="tenant_admin_svc and audit_logger"):
            await svc.cp_upsert_by_name("tenant-1", _make_payload(), reason="r")
