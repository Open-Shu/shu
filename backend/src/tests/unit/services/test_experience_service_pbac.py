"""PBAC integration tests for ExperienceService.

Uses a **real** PolicyCache (no mocking of check/is_admin) to verify that
every public method enforces the correct action and resource slug.

Setup:
- Two experiences: Morning Briefing (allowed) and Project Pulse (denied).
- Two users: admin-1 (admin bypass) and user-1 (policy grants experience.*
  on experience:morning-briefing only).
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.core.exceptions import AuthorizationError, NotFoundError
from shu.schemas.experience import (
    ExperienceCreate,
    ExperienceResponse,
    ExperienceScope,
    ExperienceStepCreate,
    ExperienceUpdate,
    ExperienceVisibility,
    RunStatus,
    StepType,
    TriggerType,
)
from shu.services.experience_service import ExperienceService
from shu.services.policy_engine import CachedPolicy, PolicyCache, _split_patterns, CachedStatement

ADMIN_USER_ID = "admin-1"
REGULAR_USER_ID = "user-1"

ALLOWED_EXP_ID = "exp-allowed"
ALLOWED_EXP_NAME = "Morning Briefing"
ALLOWED_EXP_SLUG = "morning-briefing"

DENIED_EXP_ID = "exp-denied"
DENIED_EXP_NAME = "Project Pulse"
DENIED_EXP_SLUG = "project-pulse"

ALLOWED_RUN_ID = "run-allowed"
DENIED_RUN_ID = "run-denied"

POLICY_ID = "policy-exp-access"


def _make_statement(actions: list[str], resources: list[str]) -> CachedStatement:
    exact_a, wc_a = _split_patterns(actions)
    exact_r, wc_r = _split_patterns(resources)
    return CachedStatement(
        exact_actions=exact_a,
        wildcard_actions=wc_a,
        exact_resources=exact_r,
        wildcard_resources=wc_r,
    )


def _make_pbac_cache() -> PolicyCache:
    """Build a PolicyCache granting user-1 access to morning-briefing only."""
    settings = MagicMock()
    settings.policy_cache_ttl = 9999
    cache = PolicyCache(settings=settings)
    cache._stale = False
    cache._last_refresh = 1e12

    cache._admin_user_ids = {ADMIN_USER_ID}
    cache._policies = {
        POLICY_ID: CachedPolicy(
            id=POLICY_ID,
            effect="allow",
            statements=[
                _make_statement(["experience.*"], [f"experience:{ALLOWED_EXP_SLUG}"]),
            ],
        ),
    }
    cache._user_policies = {REGULAR_USER_ID: {POLICY_ID}}
    cache._group_policies = {}
    cache._user_groups = {}
    return cache


def _make_mock_experience(*, exp_id: str, name: str, slug: str) -> MagicMock:
    """Build a mock ORM Experience object."""
    now = datetime.now()
    exp = MagicMock()
    exp.id = exp_id
    exp.name = name
    exp.slug = slug
    exp.description = f"Description for {name}"
    exp.created_by = ADMIN_USER_ID
    exp.visibility = ExperienceVisibility.PUBLISHED.value
    exp.scope = ExperienceScope.USER.value
    exp.trigger_type = TriggerType.MANUAL.value
    exp.trigger_config = None
    exp.include_previous_run = False
    exp.model_configuration_id = None
    exp.model_configuration = None
    exp.prompt_id = None
    exp.prompt = None
    exp.inline_prompt_template = None
    exp.max_run_seconds = 120
    exp.token_budget = None
    exp.version = 1
    exp.is_active_version = True
    exp.parent_version_id = None
    exp.steps = []
    exp.runs = []
    exp.last_run_at = None
    exp.created_at = now
    exp.updated_at = now
    exp.creator = MagicMock(is_active=True)
    return exp


def _make_experience_response(*, exp_id: str, name: str, slug: str) -> ExperienceResponse:
    """Build a real ExperienceResponse schema object."""
    now = datetime.now()
    return ExperienceResponse(
        id=exp_id,
        name=name,
        slug=slug,
        description=f"Description for {name}",
        created_by=ADMIN_USER_ID,
        visibility=ExperienceVisibility.PUBLISHED,
        scope=ExperienceScope.USER,
        trigger_type=TriggerType.MANUAL,
        trigger_config=None,
        include_previous_run=False,
        model_configuration_id=None,
        prompt_id=None,
        inline_prompt_template=None,
        max_run_seconds=120,
        token_budget=None,
        version=1,
        is_active_version=True,
        parent_version_id=None,
        steps=[],
        model_configuration=None,
        prompt=None,
        step_count=0,
        last_run_at=None,
        created_at=now,
        updated_at=now,
    )


def _make_mock_run(*, run_id: str, experience: MagicMock, user_id: str | None) -> MagicMock:
    """Build a mock ORM ExperienceRun."""
    now = datetime.now()
    run = MagicMock()
    run.id = run_id
    run.experience_id = experience.id
    run.experience = experience
    run.user_id = user_id
    run.previous_run_id = None
    run.model_configuration_id = None
    run.status = RunStatus.SUCCEEDED.value
    run.started_at = now
    run.finished_at = now
    run.step_states = None
    run.step_outputs = None
    run.input_params = None
    run.result_content = "output"
    run.result_metadata = None
    run.error_message = None
    run.error_details = None
    run.created_at = now
    run.updated_at = now
    return run


SHARED_EXP_ID = "exp-shared"
SHARED_EXP_NAME = "Shared Briefing"
SHARED_EXP_SLUG = "shared-briefing"

MOCK_EXP_ALLOWED = _make_mock_experience(exp_id=ALLOWED_EXP_ID, name=ALLOWED_EXP_NAME, slug=ALLOWED_EXP_SLUG)
MOCK_EXP_DENIED = _make_mock_experience(exp_id=DENIED_EXP_ID, name=DENIED_EXP_NAME, slug=DENIED_EXP_SLUG)

_shared_creator = MagicMock(is_active=True)
_shared_creator.id = ADMIN_USER_ID
MOCK_EXP_SHARED = _make_mock_experience(exp_id=SHARED_EXP_ID, name=SHARED_EXP_NAME, slug=SHARED_EXP_SLUG)
MOCK_EXP_SHARED.scope = ExperienceScope.SHARED.value
MOCK_EXP_SHARED.creator = _shared_creator

SHARED_DENIED_EXP_ID = "exp-shared-denied"
_shared_denied_creator = MagicMock(is_active=True)
_shared_denied_creator.id = REGULAR_USER_ID
MOCK_EXP_SHARED_DENIED = _make_mock_experience(
    exp_id=SHARED_DENIED_EXP_ID, name=DENIED_EXP_NAME, slug=DENIED_EXP_SLUG
)
MOCK_EXP_SHARED_DENIED.scope = ExperienceScope.SHARED.value
MOCK_EXP_SHARED_DENIED.creator = _shared_denied_creator

SHARED_INACTIVE_EXP_ID = "exp-shared-inactive"
_shared_inactive_creator = MagicMock(is_active=False)
_shared_inactive_creator.id = REGULAR_USER_ID
MOCK_EXP_SHARED_INACTIVE = _make_mock_experience(
    exp_id=SHARED_INACTIVE_EXP_ID, name=DENIED_EXP_NAME, slug=DENIED_EXP_SLUG
)
MOCK_EXP_SHARED_INACTIVE.scope = ExperienceScope.SHARED.value
MOCK_EXP_SHARED_INACTIVE.creator = _shared_inactive_creator

RESP_ALLOWED = _make_experience_response(exp_id=ALLOWED_EXP_ID, name=ALLOWED_EXP_NAME, slug=ALLOWED_EXP_SLUG)
RESP_DENIED = _make_experience_response(exp_id=DENIED_EXP_ID, name=DENIED_EXP_NAME, slug=DENIED_EXP_SLUG)
MOCK_RUN_ALLOWED = _make_mock_run(run_id=ALLOWED_RUN_ID, experience=MOCK_EXP_ALLOWED, user_id=REGULAR_USER_ID)
MOCK_RUN_DENIED = _make_mock_run(run_id=DENIED_RUN_ID, experience=MOCK_EXP_DENIED, user_id=REGULAR_USER_ID)


@pytest.fixture
def pbac_cache():
    return _make_pbac_cache()


@pytest.fixture
def db():
    session = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    session.delete = AsyncMock()
    session.execute = AsyncMock()
    return session


@pytest.fixture
def service(db):
    return ExperienceService(db)


class TestPbacAdminOps:
    """Admin-only operations: create, update, delete."""

    @pytest.mark.asyncio
    async def test_create_experience_admin_passes(self, service, pbac_cache):
        """Admin users bypass enforce_admin and can create experiences."""
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             patch.object(service, "_get_experience_by_slug", return_value=None), \
             patch.object(service, "compute_required_scopes_for_step", return_value=[]), \
             patch.object(service, "_experience_to_response", return_value=RESP_ALLOWED):
            result = await service.create_experience(
                ExperienceCreate(
                    name=ALLOWED_EXP_NAME,
                    steps=[ExperienceStepCreate(step_key="s", step_type=StepType.PLUGIN, order=0, plugin_name="gmail", plugin_op="digest")],
                ),
                created_by=ADMIN_USER_ID,
            )
        assert isinstance(result, ExperienceResponse)

    @pytest.mark.asyncio
    async def test_create_experience_non_admin_denied(self, service, pbac_cache):
        """Non-admin users are rejected by enforce_admin with AuthorizationError."""
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             pytest.raises(AuthorizationError):
            await service.create_experience(
                ExperienceCreate(
                    name=ALLOWED_EXP_NAME,
                    steps=[ExperienceStepCreate(step_key="s", step_type=StepType.PLUGIN, order=0, plugin_name="gmail", plugin_op="digest")],
                ),
                created_by=REGULAR_USER_ID,
            )

    @pytest.mark.asyncio
    async def test_update_experience_admin_passes(self, service, pbac_cache):
        """Admin users bypass enforce_admin and can update experiences."""
        admin_user = MagicMock()
        admin_user.id = ADMIN_USER_ID
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             patch.object(service, "_get_experience_by_id", return_value=MOCK_EXP_ALLOWED), \
             patch.object(service, "_experience_to_response", return_value=RESP_ALLOWED):
            result = await service.update_experience(
                ALLOWED_EXP_ID,
                ExperienceUpdate(description="Updated"),
                current_user=admin_user,
            )
        assert isinstance(result, ExperienceResponse)

    @pytest.mark.asyncio
    async def test_update_experience_non_admin_denied(self, service, pbac_cache):
        """Non-admin users are rejected by enforce_admin with AuthorizationError."""
        regular_user = MagicMock()
        regular_user.id = REGULAR_USER_ID
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             pytest.raises(AuthorizationError):
            await service.update_experience(
                ALLOWED_EXP_ID,
                ExperienceUpdate(description="Updated"),
                current_user=regular_user,
            )

    @pytest.mark.asyncio
    async def test_delete_experience_admin_passes(self, service, pbac_cache):
        """Admin users bypass enforce_admin and can delete experiences."""
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             patch.object(service, "_get_experience_by_id", return_value=MOCK_EXP_ALLOWED):
            result = await service.delete_experience(ALLOWED_EXP_ID, user_id=ADMIN_USER_ID)
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_experience_non_admin_denied(self, service, pbac_cache):
        """Non-admin users are rejected by enforce_admin with AuthorizationError."""
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             pytest.raises(AuthorizationError):
            await service.delete_experience(ALLOWED_EXP_ID, user_id=REGULAR_USER_ID)


class TestPbacGetExperience:
    """get_experience: PBAC on experience.read with slug."""

    @pytest.mark.asyncio
    async def test_admin_gets_allowed_experience(self, service, db, pbac_cache):
        """Admin bypasses enforce_pbac and can access the allowed experience."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MOCK_EXP_ALLOWED
        db.execute.return_value = mock_result

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             patch.object(service, "_experience_to_response", return_value=RESP_ALLOWED):
            result = await service.get_experience(ALLOWED_EXP_ID, user_id=ADMIN_USER_ID)
        assert result is not None
        assert result.slug == ALLOWED_EXP_SLUG

    @pytest.mark.asyncio
    async def test_admin_gets_denied_experience(self, service, db, pbac_cache):
        """Admin bypasses enforce_pbac and can access experiences that regular users cannot."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MOCK_EXP_DENIED
        db.execute.return_value = mock_result

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             patch.object(service, "_experience_to_response", return_value=RESP_DENIED):
            result = await service.get_experience(DENIED_EXP_ID, user_id=ADMIN_USER_ID)
        assert result is not None
        assert result.slug == DENIED_EXP_SLUG

    @pytest.mark.asyncio
    async def test_user_gets_allowed_experience(self, service, db, pbac_cache):
        """Regular user passes enforce_pbac when the experience slug matches their policy."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MOCK_EXP_ALLOWED
        db.execute.return_value = mock_result

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             patch.object(service, "_experience_to_response", return_value=RESP_ALLOWED):
            result = await service.get_experience(ALLOWED_EXP_ID, user_id=REGULAR_USER_ID)
        assert result is not None
        assert result.slug == ALLOWED_EXP_SLUG

    @pytest.mark.asyncio
    async def test_user_denied_on_other_experience(self, service, db, pbac_cache):
        """Regular user is denied with NotFoundError when the experience slug is not in their policy."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MOCK_EXP_DENIED
        db.execute.return_value = mock_result

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             pytest.raises(NotFoundError):
            await service.get_experience(DENIED_EXP_ID, user_id=REGULAR_USER_ID)


def _make_orm_result(exp_mock: MagicMock) -> MagicMock:
    """Build a mock DB execute result returning a single ORM Experience."""
    result = MagicMock()
    result.scalars.return_value.first.return_value = exp_mock
    return result


class TestPbacRun:
    """run(): PBAC on experience.read + experience.run (no get_experience call)."""

    @pytest.mark.asyncio
    async def test_admin_runs_any_experience(self, service, db, pbac_cache):
        """Admin bypasses both experience.read and experience.run PBAC checks."""
        admin_user = MagicMock()
        admin_user.id = ADMIN_USER_ID

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.get_config_manager"), \
             patch("shu.services.experience_service.ExperienceExecutor") as mock_executor:
            db.execute.return_value = _make_orm_result(MOCK_EXP_DENIED)
            mock_executor.return_value.execute_streaming.return_value = AsyncMock()
            result = await service.run(DENIED_EXP_ID, current_user=admin_user)
        assert result is not None

    @pytest.mark.asyncio
    async def test_user_runs_allowed_experience(self, service, db, pbac_cache):
        """Regular user passes experience.read + experience.run PBAC when the slug matches their policy."""
        user = MagicMock()
        user.id = REGULAR_USER_ID

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.get_config_manager"), \
             patch("shu.services.experience_service.ExperienceExecutor") as mock_executor:
            db.execute.return_value = _make_orm_result(MOCK_EXP_ALLOWED)
            mock_executor.return_value.execute_streaming.return_value = AsyncMock()
            result = await service.run(ALLOWED_EXP_ID, current_user=user)
        assert result is not None

    @pytest.mark.asyncio
    async def test_user_denied_run_on_other_experience(self, service, db, pbac_cache):
        """Regular user is denied with NotFoundError on experience.read for an unmatched slug."""
        user = MagicMock()
        user.id = REGULAR_USER_ID

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             pytest.raises(NotFoundError):
            db.execute.return_value = _make_orm_result(MOCK_EXP_DENIED)
            await service.run(DENIED_EXP_ID, current_user=user)


class TestPbacExecute:
    """execute(): PBAC on experience.run (no visibility check)."""

    @pytest.mark.asyncio
    async def test_admin_executes_any_experience(self, service, db, pbac_cache):
        """Admin bypasses experience.run PBAC check."""
        admin_user = MagicMock()
        admin_user.id = ADMIN_USER_ID

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.get_config_manager"), \
             patch("shu.services.experience_service.ExperienceExecutor") as mock_executor:
            db.execute.return_value = _make_orm_result(MOCK_EXP_DENIED)
            mock_run = MagicMock()
            mock_run.id = "run-1"
            mock_run.status = "succeeded"
            mock_executor.return_value.execute = AsyncMock(return_value=mock_run)
            result = await service.execute(DENIED_EXP_ID, current_user=admin_user)
        assert result.id == "run-1"

    @pytest.mark.asyncio
    async def test_user_executes_allowed_experience(self, service, db, pbac_cache):
        """Regular user passes experience.run PBAC when the slug matches their policy."""
        user = MagicMock()
        user.id = REGULAR_USER_ID

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.get_config_manager"), \
             patch("shu.services.experience_service.ExperienceExecutor") as mock_executor:
            db.execute.return_value = _make_orm_result(MOCK_EXP_ALLOWED)
            mock_run = MagicMock()
            mock_run.id = "run-1"
            mock_run.status = "succeeded"
            mock_executor.return_value.execute = AsyncMock(return_value=mock_run)
            result = await service.execute(ALLOWED_EXP_ID, current_user=user)
        assert result.id == "run-1"

    @pytest.mark.asyncio
    async def test_user_denied_execute_on_other_experience(self, service, db, pbac_cache):
        """Regular user is denied with NotFoundError on experience.run for an unmatched slug."""
        user = MagicMock()
        user.id = REGULAR_USER_ID

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             pytest.raises(NotFoundError):
            db.execute.return_value = _make_orm_result(MOCK_EXP_DENIED)
            await service.execute(DENIED_EXP_ID, current_user=user)

    @pytest.mark.asyncio
    async def test_shared_experience_executes_with_none_current_user(self, service, db, pbac_cache):
        """Shared experience resolves creator identity when current_user is None."""
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.get_config_manager"), \
             patch("shu.services.experience_service.ExperienceExecutor") as mock_executor:
            db.execute.return_value = _make_orm_result(MOCK_EXP_SHARED)
            mock_run = MagicMock()
            mock_run.id = "run-shared"
            mock_run.status = "succeeded"
            mock_executor.return_value.execute = AsyncMock(return_value=mock_run)
            result = await service.execute(SHARED_EXP_ID, current_user=None)
        assert result.id == "run-shared"
        mock_executor.return_value.execute.assert_called_once()
        call_kwargs = mock_executor.return_value.execute.call_args.kwargs
        assert call_kwargs["user_id"] is None
        assert call_kwargs["current_user"] == MOCK_EXP_SHARED.creator

    @pytest.mark.asyncio
    async def test_shared_experience_denied_when_creator_lacks_access(self, service, db, pbac_cache):
        """Shared experience is denied when creator's identity fails PBAC on experience.run."""
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             pytest.raises(NotFoundError):
            db.execute.return_value = _make_orm_result(MOCK_EXP_SHARED_DENIED)
            await service.execute(SHARED_DENIED_EXP_ID, current_user=None)

    @pytest.mark.asyncio
    async def test_shared_inactive_creator_returns_not_found_for_unauthorized_user(self, service, db, pbac_cache):
        """Unauthorized user gets NotFoundError (not AuthorizationError) for a shared experience with inactive creator.

        Ensures the PBAC denial fires before the creator-active guard to avoid
        leaking resource existence.
        """
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             pytest.raises(NotFoundError):
            db.execute.return_value = _make_orm_result(MOCK_EXP_SHARED_INACTIVE)
            await service.execute(SHARED_INACTIVE_EXP_ID, current_user=None)


class TestPbacListRuns:
    """list_runs(): PBAC on experience.read with slug."""

    @pytest.mark.asyncio
    async def test_admin_lists_runs_for_any_experience(self, service, db, pbac_cache):
        """Admin bypasses enforce_pbac and can list runs for any experience."""
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             patch.object(service, "_get_experience_by_id", return_value=MOCK_EXP_DENIED), \
             patch.object(service, "_check_visibility", new=AsyncMock(return_value=True)), \
             patch.object(service, "_execute_paginated_query", return_value=(0, [])), \
             patch.object(service, "_fetch_users_by_ids", return_value={}):
            result = await service.list_runs(DENIED_EXP_ID, user_id=ADMIN_USER_ID)
        assert result.total == 0

    @pytest.mark.asyncio
    async def test_user_lists_runs_for_allowed_experience(self, service, db, pbac_cache):
        """Regular user passes enforce_pbac when listing runs for an experience in their policy."""
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             patch.object(service, "_get_experience_by_id", return_value=MOCK_EXP_ALLOWED), \
             patch.object(service, "_check_visibility", new=AsyncMock(return_value=True)), \
             patch.object(service, "_execute_paginated_query", return_value=(0, [])), \
             patch.object(service, "_fetch_users_by_ids", return_value={}):
            result = await service.list_runs(ALLOWED_EXP_ID, user_id=REGULAR_USER_ID)
        assert result.total == 0

    @pytest.mark.asyncio
    async def test_user_denied_list_runs_for_other_experience(self, service, pbac_cache):
        """Regular user is denied with NotFoundError when listing runs for an unmatched experience."""
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             patch.object(service, "_get_experience_by_id", return_value=MOCK_EXP_DENIED), \
             pytest.raises(NotFoundError):
            await service.list_runs(DENIED_EXP_ID, user_id=REGULAR_USER_ID)


class TestPbacGetRun:
    """get_run(): PBAC on experience.read via the run's experience slug."""

    @pytest.mark.asyncio
    async def test_admin_gets_run_for_any_experience(self, service, db, pbac_cache):
        """Admin bypasses enforce_pbac and can fetch a run for any experience."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MOCK_RUN_DENIED
        db.execute.return_value = mock_result

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             patch.object(service, "_check_visibility", new=AsyncMock(return_value=True)), \
             patch.object(service, "_run_to_response", return_value=MagicMock()):
            result = await service.get_run(DENIED_RUN_ID, user_id=ADMIN_USER_ID)
        assert result is not None

    @pytest.mark.asyncio
    async def test_user_gets_run_for_allowed_experience(self, service, db, pbac_cache):
        """Regular user passes enforce_pbac when the run's experience slug matches their policy."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MOCK_RUN_ALLOWED
        db.execute.return_value = mock_result

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             patch.object(service, "_check_visibility", new=AsyncMock(return_value=True)), \
             patch.object(service, "_run_to_response", return_value=MagicMock()):
            result = await service.get_run(ALLOWED_RUN_ID, user_id=REGULAR_USER_ID)
        assert result is not None

    @pytest.mark.asyncio
    async def test_user_denied_get_run_for_other_experience(self, service, db, pbac_cache):
        """Regular user is denied with NotFoundError when the run's experience slug is unmatched."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MOCK_RUN_DENIED
        db.execute.return_value = mock_result

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             pytest.raises(NotFoundError):
            await service.get_run(DENIED_RUN_ID, user_id=REGULAR_USER_ID)


class TestPbacExportExperience:
    """export_experience(): PBAC via get_experience (experience.read)."""

    @pytest.mark.asyncio
    async def test_admin_exports_any_experience(self, service, pbac_cache):
        """Admin bypasses PBAC (via get_experience) and can export any experience."""
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             patch.object(service, "get_experience", return_value=RESP_DENIED), \
             patch.object(service, "_export_experience_to_yaml", return_value=("yaml", "file.yaml")):
            yaml_content, file_name = await service.export_experience(DENIED_EXP_ID, user_id=ADMIN_USER_ID)
        assert yaml_content == "yaml"

    @pytest.mark.asyncio
    async def test_user_exports_allowed_experience(self, service, pbac_cache):
        """Regular user passes PBAC (via get_experience) for an experience in their policy."""
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             patch.object(service, "get_experience", return_value=RESP_ALLOWED), \
             patch.object(service, "_export_experience_to_yaml", return_value=("yaml", "file.yaml")):
            yaml_content, file_name = await service.export_experience(ALLOWED_EXP_ID, user_id=REGULAR_USER_ID)
        assert yaml_content == "yaml"

    @pytest.mark.asyncio
    async def test_user_denied_export_other_experience(self, service, pbac_cache):
        """Regular user is denied export when get_experience returns None for an unmatched slug."""
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             patch.object(service, "get_experience", return_value=None), \
             pytest.raises(NotFoundError):
            await service.export_experience(DENIED_EXP_ID, user_id=REGULAR_USER_ID)


class TestPbacFilteredLists:
    """list_experiences and get_user_results: only allowed items returned."""

    @pytest.mark.asyncio
    async def test_list_experiences_admin_sees_all(self, service, pbac_cache):
        """Admin bypasses _pbac_filter so both experiences are returned."""
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             patch.object(
                 service, "_execute_paginated_query",
                 return_value=(2, [MOCK_EXP_ALLOWED, MOCK_EXP_DENIED]),
             ):
            result = await service.list_experiences(user_id=ADMIN_USER_ID)
        assert len(result.items) == 2

    @pytest.mark.asyncio
    async def test_list_experiences_user_sees_only_allowed(self, service, db, pbac_cache):
        """PBAC pre-filter excludes experiences whose slug is not in the user's policy."""
        # _exclude_denied_experiences queries all slugs via db.execute
        slug_result = MagicMock()
        slug_result.all.return_value = [(ALLOWED_EXP_SLUG,), (DENIED_EXP_SLUG,)]
        db.execute = AsyncMock(return_value=slug_result)

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             patch.object(
                 service, "_execute_paginated_query",
                 return_value=(1, [MOCK_EXP_ALLOWED]),
             ):
            result = await service.list_experiences(user_id=REGULAR_USER_ID)
        assert len(result.items) == 1
        assert result.items[0].slug == ALLOWED_EXP_SLUG

    @pytest.mark.asyncio
    async def test_get_user_results_admin_sees_all(self, service, db, pbac_cache):
        """Admin bypasses PBAC so all experiences appear in results."""
        slug_result = MagicMock()
        slug_result.all.return_value = [(ALLOWED_EXP_SLUG,), (DENIED_EXP_SLUG,)]

        count_result = MagicMock()
        count_result.scalar.return_value = 2

        exp_result = MagicMock()
        exp_result.scalars.return_value.all.return_value = [MOCK_EXP_ALLOWED, MOCK_EXP_DENIED]

        runs_result = MagicMock()
        runs_result.scalars.return_value.all.return_value = []

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return slug_result
            if call_count == 2:
                return count_result
            if call_count == 3:
                return exp_result
            return runs_result

        db.execute = mock_execute

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             patch.object(service, "_check_can_run", new=AsyncMock(return_value=(True, []))), \
             patch.object(service, "_get_plugin_loader", return_value=MagicMock(discover=MagicMock(return_value={}))):
            result = await service.get_user_results(user_id=ADMIN_USER_ID)
        assert len(result.experiences) == 2

    @pytest.mark.asyncio
    async def test_get_user_results_user_sees_only_allowed(self, service, db, pbac_cache):
        """PBAC pre-filter excludes experiences whose slug is not in the user's policy."""
        slug_result = MagicMock()
        slug_result.all.return_value = [(ALLOWED_EXP_SLUG,), (DENIED_EXP_SLUG,)]

        count_result = MagicMock()
        count_result.scalar.return_value = 1

        exp_result = MagicMock()
        exp_result.scalars.return_value.all.return_value = [MOCK_EXP_ALLOWED]

        runs_result = MagicMock()
        runs_result.scalars.return_value.all.return_value = []

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return slug_result
            if call_count == 2:
                return count_result
            if call_count == 3:
                return exp_result
            return runs_result

        db.execute = mock_execute

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.experience_service.POLICY_CACHE", pbac_cache), \
             patch.object(service, "_check_can_run", new=AsyncMock(return_value=(True, []))), \
             patch.object(service, "_get_plugin_loader", return_value=MagicMock(discover=MagicMock(return_value={}))):
            result = await service.get_user_results(user_id=REGULAR_USER_ID)
        assert len(result.experiences) == 1
        assert result.experiences[0].experience_name == ALLOWED_EXP_NAME
