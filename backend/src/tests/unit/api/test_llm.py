"""Unit tests for LLM provider API endpoints.

Tests cover system-managed provider provenance and lockdown semantics:
- POST /providers silently drops client-supplied `is_system_managed` (Pydantic schema
  excludes the field, so the service receives FALSE provenance regardless of request body)
- DELETE /providers/{id} on a system-managed row returns 403 with the canonical detail
- PUT /providers/{id} on a system-managed row returns 403 with the canonical detail
  regardless of which mutable field the caller attempts to change
- DELETE /providers/{pid}/models/{mid} on a system-managed parent returns 403
- POST /providers/{pid}/models on a system-managed parent succeeds (child linkage preserved)
- POST /providers/{pid}/sync-models on a system-managed parent succeeds (design decision #4)
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from shu.api.llm import (
    LLMModelCreate,
    LLMProviderCreate,
    LLMProviderUpdate,
    create_model,
    create_provider,
    delete_provider,
    disable_provider_model,
    get_provider,
    list_models,
    sync_provider_models,
    update_provider,
)
from shu.core.exceptions import (
    ModelLockedError,
    ProviderCreationDisabledError,
    ProviderLockedError,
)
from shu.llm.service import LLMService
from shu.models.llm_provider import LLMModel, LLMProvider, ModelType


LOCKED_DETAIL = "Provider is managed by Shu and cannot be modified."
MODEL_LOCKED_DETAIL = "Model is managed by Shu and cannot be modified."
CREATION_DISABLED_DETAIL = "Provider creation is disabled on this deployment."


def _mock_admin_user(user_id: str = "admin-1"):
    user = MagicMock()
    user.id = user_id
    user.can_manage_users.return_value = True
    return user


def _mock_provider(*, provider_id: str = "prov-1", is_system_managed: bool = False):
    """Return a MagicMock shaped like an LLMProvider row for response serialization."""
    provider = MagicMock()
    provider.id = provider_id
    provider.is_system_managed = is_system_managed
    return provider


class TestCreateProviderSilentDrop:
    """POST /providers must ignore client-supplied `is_system_managed`."""

    def test_schema_silently_drops_is_system_managed_field(self):
        """LLMProviderCreate does not declare `is_system_managed`; Pydantic must drop it."""
        payload = {
            "name": "Acme",
            "provider_type": "openai",
            "api_endpoint": "https://api.example.com",
            "is_system_managed": True,
        }

        model = LLMProviderCreate(**payload)

        dumped = model.model_dump()
        assert "is_system_managed" not in dumped
        assert not hasattr(model, "is_system_managed")

    @pytest.mark.asyncio
    async def test_post_provider_with_is_system_managed_true_persists_as_false(self):
        """Handler forwards only schema-declared fields; created row has is_system_managed=False."""
        db = AsyncMock()
        current_user = _mock_admin_user()

        provider_data = LLMProviderCreate(
            name="Acme",
            provider_type="openai",
            api_endpoint="https://api.example.com",
            **{"is_system_managed": True},  # dropped by Pydantic before it reaches the handler
        )

        created_provider = _mock_provider(provider_id="prov-new", is_system_managed=False)

        with (
            patch("shu.api.llm.LLMService") as mock_svc_class,
            patch("shu.api.llm._provider_to_response") as mock_to_response,
        ):
            mock_svc = MagicMock()
            mock_svc.create_provider = AsyncMock(return_value=created_provider)
            mock_svc_class.return_value = mock_svc

            mock_to_response.return_value = MagicMock(
                model_dump=lambda: {"id": "prov-new", "is_system_managed": False},
            )

            response = await create_provider(
                provider_data=provider_data,
                current_user=current_user,
                db=db,
            )

        assert response.status_code == 201
        body = json.loads(response.body.decode())
        assert body["data"]["is_system_managed"] is False

        mock_svc.create_provider.assert_awaited_once()
        _, kwargs = mock_svc.create_provider.call_args
        assert "is_system_managed" not in kwargs, (
            "is_system_managed must never reach the service layer from the API"
        )


class TestDeleteProviderLocked:
    """DELETE /providers/{id} on a system-managed row must return 403 with canonical detail."""

    @pytest.mark.asyncio
    async def test_delete_system_managed_provider_returns_403_with_canonical_detail(self):
        db = AsyncMock()
        current_user = _mock_admin_user()

        with patch("shu.api.llm.LLMService") as mock_svc_class:
            mock_svc = MagicMock()
            mock_svc.delete_provider = AsyncMock(side_effect=ProviderLockedError(LOCKED_DETAIL))
            mock_svc_class.return_value = mock_svc

            with pytest.raises(HTTPException) as exc_info:
                await delete_provider(
                    provider_id="prov-sys",
                    current_user=current_user,
                    db=db,
                )

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == LOCKED_DETAIL


class TestUpdateProviderLocked:
    """PUT /providers/{id} on a system-managed row must return 403 for every mutable field.

    The service's `update_provider` raises `ProviderLockedError` as soon as it loads a
    system-managed row, before inspecting which fields were submitted. These cases prove
    the router maps that error to 403 with the canonical detail no matter which field
    the caller tries to mutate.

    Note: `LLMProviderUpdate` currently declares `provider_type` and `api_endpoint` as
    required (non-optional) fields, so both must be supplied to instantiate the schema.
    When the parametrized target field IS `api_endpoint`, the target value overrides
    the baseline.
    """

    @pytest.mark.parametrize(
        "field,value",
        [
            ("name", "new-name"),
            ("api_key", "sk-new"),
            ("api_endpoint", "https://new.example.com"),
            ("is_active", False),
            ("rate_limit_rpm", 999),
        ],
    )
    @pytest.mark.asyncio
    async def test_put_system_managed_provider_returns_403_with_canonical_detail(self, field, value):
        db = AsyncMock()
        current_user = _mock_admin_user()

        # provider_type and api_endpoint are required by the schema; the parametrized
        # field overrides the baseline when it targets api_endpoint.
        update_kwargs = {"provider_type": "openai", "api_endpoint": "https://baseline.example.com"}
        update_kwargs[field] = value
        provider_data = LLMProviderUpdate(**update_kwargs)

        with patch("shu.api.llm.LLMService") as mock_svc_class:
            mock_svc = MagicMock()
            mock_svc.update_provider = AsyncMock(side_effect=ProviderLockedError(LOCKED_DETAIL))
            mock_svc_class.return_value = mock_svc

            with pytest.raises(HTTPException) as exc_info:
                await update_provider(
                    provider_id="prov-sys",
                    provider_data=provider_data,
                    current_user=current_user,
                    db=db,
                )

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == LOCKED_DETAIL


class TestDeleteModelLocked:
    """DELETE /providers/{pid}/models/{mid} must return 403 when parent is system-managed."""

    @pytest.mark.asyncio
    async def test_delete_model_on_system_managed_parent_returns_403_with_canonical_detail(self):
        db = AsyncMock()
        current_user = _mock_admin_user()

        with patch("shu.api.llm.LLMService") as mock_svc_class:
            mock_svc = MagicMock()
            mock_svc.delete_provider_model = AsyncMock(side_effect=ModelLockedError(MODEL_LOCKED_DETAIL))
            mock_svc_class.return_value = mock_svc

            with pytest.raises(HTTPException) as exc_info:
                await disable_provider_model(
                    provider_id="prov-sys",
                    model_id="model-sys",
                    current_user=current_user,
                    db=db,
                )

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == MODEL_LOCKED_DETAIL

        mock_svc.delete_provider_model.assert_awaited_once_with("prov-sys", "model-sys")


class TestCreateModelOnSystemManagedParent:
    """POST /providers/{pid}/models must succeed even when the parent is system-managed.

    Creating a new child model on a system-managed provider is explicitly allowed by
    the lockdown contract: only mutations to the provider row itself are blocked;
    children (models) remain addable. The handler should return 200 with the new
    model carrying the correct parent `provider_id`.
    """

    @pytest.mark.asyncio
    async def test_post_model_on_system_managed_parent_returns_200_with_linked_child(self):
        db = AsyncMock()
        current_user = _mock_admin_user()

        model_data = LLMModelCreate(
            model_name="gpt-5",
            display_name="GPT-5",
            model_type="chat",
        )

        # Shape the returned object so ShuResponse.success can serialize it cleanly:
        # to_serializable prefers `model_dump` over `dict`, so a MagicMock whose
        # `model_dump` returns a plain dict captures the child-to-system-managed-parent
        # linkage without tripping jsonable_encoder on MagicMock internals.
        created_model = MagicMock()
        created_model.model_dump = MagicMock(
            return_value={
                "id": "model-new",
                "provider_id": "prov-sys",
                "model_name": "gpt-5",
                "display_name": "GPT-5",
                "model_type": "chat",
                "is_active": True,
            },
        )

        with patch("shu.api.llm.LLMService") as mock_svc_class:
            mock_svc = MagicMock()
            mock_svc.create_model = AsyncMock(return_value=created_model)
            mock_svc_class.return_value = mock_svc

            response = await create_model(
                provider_id="prov-sys",
                model_data=model_data,
                current_user=current_user,
                db=db,
            )

        assert response.status_code == 200
        body = json.loads(response.body.decode())
        assert body["data"]["provider_id"] == "prov-sys"
        assert body["data"]["model_name"] == "gpt-5"

        mock_svc.create_model.assert_awaited_once()
        args, _ = mock_svc.create_model.call_args
        assert args[0] == "prov-sys", "handler must forward provider_id positionally to create_model"


class TestSyncModelsOnSystemManagedParent:
    """POST /providers/{pid}/sync-models must succeed on a system-managed parent.

    Per design decision #4, sync-models is ALLOWED on system-managed providers: the
    contract is add-only (existing rows are not modified or deactivated). That contract
    is enforced and proven at the service layer (task 24). At the API layer the only
    meaningful assertions are:
      1. the handler does not raise when the parent is system-managed
      2. the handler forwards provider_id unchanged to the service
      3. the response envelope carries through the list of newly-added models

    We seed the mock with a freshly-created model (representative of add-only output)
    and verify the handler surfaces it. The "no existing row modified/deactivated"
    invariant is intentionally NOT asserted here because the handler does not observe
    pre-existing rows — that belongs in task 24.
    """

    @pytest.mark.asyncio
    async def test_sync_models_on_system_managed_parent_returns_200_and_forwards_provider_id(self):
        db = AsyncMock()
        current_user = _mock_admin_user()

        newly_added_model = MagicMock()
        newly_added_model.id = "model-new"
        newly_added_model.model_name = "gpt-5"
        newly_added_model.display_name = "GPT-5"
        newly_added_model.is_active = True

        with patch("shu.api.llm.LLMService") as mock_svc_class:
            mock_svc = MagicMock()
            mock_svc.sync_provider_models = AsyncMock(return_value=[newly_added_model])
            mock_svc_class.return_value = mock_svc

            response = await sync_provider_models(
                provider_id="prov-sys",
                selected_models=None,
                current_user=current_user,
                db=db,
            )

        assert response.status_code == 200
        body = json.loads(response.body.decode())
        assert body["data"]["provider_id"] == "prov-sys"
        assert body["data"]["count"] == 1
        assert body["data"]["synced_models"][0]["id"] == "model-new"
        assert body["data"]["synced_models"][0]["model_name"] == "gpt-5"

        mock_svc.sync_provider_models.assert_awaited_once_with("prov-sys")


class TestLockProviderCreations:
    """`settings.lock_provider_creations` gates ONLY POST /providers.

    The guard lives inside `LLMService.create_provider` (it inspects
    `self.settings.lock_provider_creations` and raises `ProviderCreationDisabledError`).
    The API layer's sole responsibility is mapping that exception to
    `HTTPException(403, "Provider creation is disabled on this deployment.")`.

    The canonical detail string deliberately differs from `LOCKED_DETAIL` so clients
    can distinguish "provider creation disabled deployment-wide" from "this specific
    row is system-managed".

    Every other handler (PUT / DELETE / GET / sync-models / POST-models) does NOT
    read the flag. The "does not bleed through" tests below are therefore just
    happy-path exercises proving those handlers still execute normally — the flag
    is invisible to them by design (Requirement 10.4). Note that POST-models and
    sync-models already have happy-path coverage via
    `TestCreateModelOnSystemManagedParent` and `TestSyncModelsOnSystemManagedParent`,
    so they are not duplicated here.
    """

    @pytest.mark.asyncio
    async def test_post_provider_when_creations_locked_returns_403_with_canonical_detail(self):
        db = AsyncMock()
        current_user = _mock_admin_user()

        provider_data = LLMProviderCreate(
            name="Acme",
            provider_type="openai",
            api_endpoint="https://api.example.com",
        )

        with patch("shu.api.llm.LLMService") as mock_svc_class:
            mock_svc = MagicMock()
            mock_svc.create_provider = AsyncMock(
                side_effect=ProviderCreationDisabledError(CREATION_DISABLED_DETAIL)
            )
            mock_svc_class.return_value = mock_svc

            with pytest.raises(HTTPException) as exc_info:
                await create_provider(
                    provider_data=provider_data,
                    current_user=current_user,
                    db=db,
                )

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == CREATION_DISABLED_DETAIL
        # Sanity: the creation-lock detail must NOT collide with the system-managed
        # row-lock detail — they communicate different failure modes to clients.
        assert exc_info.value.detail != LOCKED_DETAIL

    @pytest.mark.asyncio
    async def test_put_provider_on_non_system_managed_row_succeeds(self):
        """PUT handler does not branch on `lock_provider_creations` — proven by happy path."""
        db = AsyncMock()
        current_user = _mock_admin_user()

        provider_data = LLMProviderUpdate(
            provider_type="openai",
            api_endpoint="https://baseline.example.com",
            name="updated-name",
        )

        updated_provider = _mock_provider(provider_id="prov-1", is_system_managed=False)

        with (
            patch("shu.api.llm.LLMService") as mock_svc_class,
            patch("shu.api.llm._provider_to_response") as mock_to_response,
        ):
            mock_svc = MagicMock()
            mock_svc.update_provider = AsyncMock(return_value=updated_provider)
            mock_svc_class.return_value = mock_svc

            mock_to_response.return_value = MagicMock(
                model_dump=lambda: {"id": "prov-1", "is_system_managed": False, "name": "updated-name"},
            )

            response = await update_provider(
                provider_id="prov-1",
                provider_data=provider_data,
                current_user=current_user,
                db=db,
            )

        assert response.status_code == 200
        body = json.loads(response.body.decode())
        assert body["data"]["id"] == "prov-1"
        mock_svc.update_provider.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_provider_on_non_system_managed_row_succeeds(self):
        """DELETE handler does not branch on `lock_provider_creations` — proven by happy path."""
        db = AsyncMock()
        current_user = _mock_admin_user()

        with patch("shu.api.llm.LLMService") as mock_svc_class:
            mock_svc = MagicMock()
            mock_svc.delete_provider = AsyncMock(return_value=True)
            mock_svc_class.return_value = mock_svc

            response = await delete_provider(
                provider_id="prov-1",
                current_user=current_user,
                db=db,
            )

        assert response.status_code == 204
        mock_svc.delete_provider.assert_awaited_once_with("prov-1")

    @pytest.mark.asyncio
    async def test_get_provider_succeeds(self):
        """GET handler does not branch on `lock_provider_creations` — proven by happy path."""
        db = AsyncMock()
        current_user = _mock_admin_user()

        fetched_provider = _mock_provider(provider_id="prov-1", is_system_managed=False)

        with (
            patch("shu.api.llm.LLMService") as mock_svc_class,
            patch("shu.api.llm._provider_to_response") as mock_to_response,
        ):
            mock_svc = MagicMock()
            mock_svc.get_provider_by_id = AsyncMock(return_value=fetched_provider)
            mock_svc_class.return_value = mock_svc

            mock_to_response.return_value = MagicMock(
                model_dump=lambda: {"id": "prov-1", "is_system_managed": False},
            )

            response = await get_provider(
                provider_id="prov-1",
                current_user=current_user,
                db=db,
            )

        assert response.status_code == 200
        body = json.loads(response.body.decode())
        assert body["data"]["id"] == "prov-1"
        mock_svc.get_provider_by_id.assert_awaited_once_with("prov-1")


class TestListModelsForwardsActiveProvidersOnly:
    """The only end-user-facing listing endpoint is `GET /llm/models` (handler `list_models`).

    `list_models` must forward `active_providers_only=True` so the service excludes
    models whose parent provider is deactivated. Admin-management endpoints
    (providers CRUD, discover/sync-models) deliberately do NOT pass this flag — operators
    must still see inactive rows for management. The mock-based check here proves the
    forwarding contract at the handler level; the semantic double-filter (model AND
    provider must both be active) is exercised end-to-end against a real SQLite session
    in TestGetAvailableModelsDoubleActiveFilter below.
    """

    @pytest.mark.asyncio
    async def test_list_models_passes_active_providers_only_true_to_service(self):
        db = AsyncMock()
        current_user = MagicMock()

        with patch("shu.api.llm.LLMService") as mock_svc_class:
            mock_svc = MagicMock()
            mock_svc.get_available_models = AsyncMock(return_value=[])
            mock_svc_class.return_value = mock_svc

            response = await list_models(
                provider_id=None,
                model_type=None,
                current_user=current_user,
                db=db,
            )

        assert response.status_code == 200
        mock_svc.get_available_models.assert_awaited_once()
        _, kwargs = mock_svc.get_available_models.call_args
        assert kwargs.get("active_providers_only") is True, (
            "list_models must forward active_providers_only=True to LLMService — "
            "without it, end-user pickers would surface models under deactivated providers."
        )


class TestGetAvailableModelsDoubleActiveFilter:
    """Assert that `active_providers_only=True` adds the provider-active JOIN/WHERE.

    `list_models` forwards this flag to `LLMService.get_available_models`; the service
    then adds `JOIN LLMProvider ON ... WHERE LLMProvider.is_active`. We can't run the
    query here (no Postgres in unit tests, no SQLite fallback either) so we capture
    the constructed `Select` statement and inspect its compiled SQL — enough to prove
    the filter is wired without introducing a DB driver dependency.
    """

    @staticmethod
    def _capture_stmt(mock_session: AsyncMock):
        """Return the stmt arg passed to session.execute(...) during the call."""
        assert mock_session.execute.await_count == 1, "expected exactly one execute() call"
        return mock_session.execute.await_args.args[0]

    @staticmethod
    def _compiled_sql(stmt) -> str:
        return str(stmt.compile(compile_kwargs={"literal_binds": True}))

    @pytest.mark.asyncio
    async def test_active_providers_only_adds_provider_join_and_filter(self):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        service = LLMService(mock_session)
        await service.get_available_models(
            model_types=[ModelType.CHAT], active_providers_only=True
        )

        sql = self._compiled_sql(self._capture_stmt(mock_session)).lower()
        assert "join llm_providers" in sql, f"expected JOIN on llm_providers; got: {sql}"
        # Both is_active predicates must be present — model's AND provider's.
        assert sql.count("is_active") >= 2, (
            f"expected both llm_models.is_active and llm_providers.is_active; got: {sql}"
        )
        assert "llm_providers.is_active" in sql, (
            f"expected llm_providers.is_active predicate; got: {sql}"
        )

    @pytest.mark.asyncio
    async def test_admin_flow_without_flag_omits_provider_filter(self):
        """Regression guard: omitting the flag must NOT JOIN/filter on LLMProvider."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        service = LLMService(mock_session)
        await service.get_available_models(model_types=[ModelType.CHAT])

        sql = self._compiled_sql(self._capture_stmt(mock_session)).lower()
        assert "join llm_providers" not in sql, (
            f"admin listing must not filter by provider active state; got: {sql}"
        )
        assert "llm_providers.is_active" not in sql, (
            f"admin listing must not reference llm_providers.is_active; got: {sql}"
        )
