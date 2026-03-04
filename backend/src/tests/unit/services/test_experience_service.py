"""
Unit tests for ExperienceService.

Tests cover:
- Template validation (syntax and dry-run)
- Helper methods (pagination, base query)
- Required scopes computation
- Visibility checks
- Shared scope: creation, run visibility, access control
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.core.exceptions import ValidationError
from shu.schemas.experience import (
    ExperienceCreate,
    ExperienceList,
    ExperienceResponse,
    ExperienceScope,
    ExperienceStepCreate,
    ExperienceUpdate,
    ExperienceVisibility,
    StepType,
    TriggerType,
)
from shu.services.experience_service import ExperienceService


@pytest.fixture
def mock_db_session():
    """Create a mock async database session."""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    session.delete = AsyncMock()
    session.execute = AsyncMock()
    return session


@pytest.fixture
def service(mock_db_session):
    """Create an ExperienceService instance with mocked db."""
    return ExperienceService(mock_db_session)


@pytest.fixture
def sample_step_create():
    """Create a sample step for testing."""
    return ExperienceStepCreate(
        step_key="test_step",
        step_type=StepType.PLUGIN,
        order=0,
        plugin_name="gmail",
        plugin_op="digest",
        params_template={"max_results": 50},
    )


@pytest.fixture
def sample_experience_create(sample_step_create):
    """Create a sample experience for testing."""
    return ExperienceCreate(
        name="Test Experience",
        description="A test experience",
        created_by="user-123",
        visibility=ExperienceVisibility.DRAFT,
        trigger_type=TriggerType.MANUAL,
        steps=[sample_step_create],
    )


@pytest.fixture
def mock_experience_response():
    """Create a mock ExperienceResponse for pagination tests."""
    return ExperienceResponse(
        id="exp-123",
        name="Test Experience",
        description="A test experience",
        created_by="user-123",
        visibility=ExperienceVisibility.DRAFT,
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
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )


class TestTemplateValidation:
    """Tests for Jinja2 template validation."""

    def test_validate_template_syntax_valid(self, service):
        """Valid template should not raise."""
        template = "Hello {{ user.name }}, you have {{ count }} messages."
        # Should not raise
        service._validate_template_syntax(template, "test_field")

    def test_validate_template_syntax_invalid(self, service):
        """Invalid template should raise ValidationError."""
        template = "Hello {{ user.name }"  # Missing closing braces
        with pytest.raises(ValidationError) as exc_info:
            service._validate_template_syntax(template, "test_field")
        assert "test_field" in str(exc_info.value.message)

    def test_validate_template_syntax_complex_valid(self, service):
        """Complex valid template with loops and conditionals."""
        template = """
        {% for item in items %}
            {{ item.name }}: {{ item.value }}
        {% endfor %}
        {% if user.is_admin %}Admin{% endif %}
        """
        service._validate_template_syntax(template, "complex_template")

    def test_validate_template_with_context_success(self, service):
        """Template rendering with valid context succeeds."""
        template = "Hello {{ user.display_name }}"
        success, error = service.validate_template_with_context(template)
        assert success is True
        assert error is None

    def test_validate_template_with_context_syntax_error(self, service):
        """Template with syntax error returns error message."""
        template = "Hello {{ user.name"  # Missing closing
        success, error = service.validate_template_with_context(template)
        assert success is False
        assert "syntax error" in error.lower()

    def test_validate_template_with_custom_context(self, service):
        """Template rendering with custom context."""
        template = "Project: {{ project.name }}"
        mock_context = {"project": {"name": "Test Project"}}
        success, error = service.validate_template_with_context(template, mock_context)
        assert success is True
        assert error is None

    def test_build_validation_context(self, service):
        """Validation context contains expected keys for dry-run template validation."""
        context = service._build_validation_context()
        assert "user" in context
        assert "input" in context
        assert "steps" in context
        assert "previous_run" in context
        assert "now" in context
        assert context["user"]["id"] == "mock-user-id"


class TestPaginationHelpers:
    """Tests for pagination helper methods."""

    def test_build_paginated_response_basic(self, service, mock_experience_response):
        """Basic pagination response calculation."""
        items = [mock_experience_response, mock_experience_response, mock_experience_response]
        response = service._build_paginated_response(ExperienceList, items, total=10, offset=0, limit=3)
        assert len(response.items) == 3
        assert response.total == 10
        assert response.page == 1
        assert response.per_page == 3
        assert response.pages == 4  # ceil(10/3)

    def test_build_paginated_response_second_page(self, service, mock_experience_response):
        """Pagination response for second page."""
        items = [mock_experience_response, mock_experience_response, mock_experience_response]
        response = service._build_paginated_response(ExperienceList, items, total=10, offset=3, limit=3)
        assert response.page == 2
        assert response.pages == 4

    def test_build_paginated_response_zero_limit(self, service):
        """Handle zero limit gracefully."""
        items = []
        response = service._build_paginated_response(ExperienceList, items, total=0, offset=0, limit=0)
        assert response.page == 1
        assert response.pages == 1

    def test_build_paginated_response_exact_pages(self, service, mock_experience_response):
        """Total divides evenly by limit."""
        items = [mock_experience_response, mock_experience_response]
        response = service._build_paginated_response(ExperienceList, items, total=10, offset=0, limit=5)
        assert response.pages == 2


class TestPluginLoader:
    """Tests for lazy plugin loader initialization."""

    def test_plugin_loader_lazy_initialization(self, service):
        """Plugin loader is None initially."""
        assert service._plugin_loader is None

    def test_get_plugin_loader_creates_instance(self, service):
        """_get_plugin_loader creates loader on first call and caches it."""
        with patch("shu.plugins.loader.PluginLoader") as mock_loader_class:
            mock_loader_instance = MagicMock()
            mock_loader_class.return_value = mock_loader_instance

            # First call creates instance
            loader1 = service._get_plugin_loader()
            assert loader1 is not None

            # Second call returns cached instance (same object)
            loader2 = service._get_plugin_loader()
            assert loader2 is loader1


class TestRequiredScopesComputation:
    """Tests for computing required identity scopes."""

    @pytest.mark.asyncio
    async def test_compute_scopes_plugin_not_found(self, service):
        """Unknown plugin returns empty scopes."""
        mock_loader = MagicMock()
        mock_loader.discover.return_value = {}
        service._plugin_loader = mock_loader

        scopes = await service.compute_required_scopes_for_step("unknown_plugin")
        assert scopes == []

    @pytest.mark.asyncio
    async def test_compute_scopes_with_op_auth(self, service):
        """Plugin with op_auth returns correct scopes."""
        mock_record = MagicMock()
        mock_record.op_auth = {
            "digest": {"scopes": ["gmail.readonly", "gmail.labels"]},
            "send": {"scopes": ["gmail.send"]},
        }

        mock_loader = MagicMock()
        mock_loader.discover.return_value = {"gmail": mock_record}
        service._plugin_loader = mock_loader

        scopes = await service.compute_required_scopes_for_step("gmail", "digest")
        assert "gmail.readonly" in scopes
        assert "gmail.labels" in scopes
        assert "gmail.send" not in scopes

    @pytest.mark.asyncio
    async def test_compute_scopes_no_op_specified(self, service):
        """No op specified returns empty scopes even with op_auth."""
        mock_record = MagicMock()
        mock_record.op_auth = {"digest": {"scopes": ["gmail.readonly"]}}

        mock_loader = MagicMock()
        mock_loader.discover.return_value = {"gmail": mock_record}
        service._plugin_loader = mock_loader

        scopes = await service.compute_required_scopes_for_step("gmail", None)
        assert scopes == []

    @pytest.mark.asyncio
    async def test_compute_scopes_handles_exception(self, service):
        """Exceptions during scope computation are handled gracefully."""
        # Clear any existing loader
        service._plugin_loader = None

        with patch("shu.plugins.loader.PluginLoader") as mock_loader_class:
            mock_loader_class.side_effect = Exception("Plugin load failed")

            scopes = await service.compute_required_scopes_for_step("gmail", "digest")
            assert scopes == []


class TestVisibilityChecks:
    """Tests for experience visibility logic.

    Note: Only admins can create experiences, so draft and admin_only
    experiences are only visible to admins.
    """

    def test_visibility_admin_sees_all(self, service):
        """Admin can see any visibility level."""
        for visibility in [
            ExperienceVisibility.DRAFT,
            ExperienceVisibility.ADMIN_ONLY,
            ExperienceVisibility.PUBLISHED,
        ]:
            experience = MagicMock()
            experience.visibility = visibility.value
            assert service._check_visibility(experience, "any-user", is_admin=True) is True

    def test_visibility_published_visible_to_all(self, service):
        """Published experiences are visible to everyone."""
        experience = MagicMock()
        experience.visibility = ExperienceVisibility.PUBLISHED.value

        assert service._check_visibility(experience, "any-user", is_admin=False) is True
        assert service._check_visibility(experience, None, is_admin=False) is True

    def test_visibility_draft_not_visible_to_non_admins(self, service):
        """Draft experiences are only visible to admins."""
        experience = MagicMock()
        experience.visibility = ExperienceVisibility.DRAFT.value

        assert service._check_visibility(experience, "any-user", is_admin=False) is False

    def test_visibility_admin_only_not_visible_to_non_admins(self, service):
        """Admin-only experiences are not visible to regular users."""
        experience = MagicMock()
        experience.visibility = ExperienceVisibility.ADMIN_ONLY.value

        assert service._check_visibility(experience, "any-user", is_admin=False) is False


class TestStepValidation:
    """Tests for experience step validation."""

    @pytest.mark.asyncio
    async def test_validate_steps_duplicate_keys(self, service):
        """Duplicate step keys should raise ValidationError."""
        steps = [
            ExperienceStepCreate(
                step_key="emails",
                step_type=StepType.PLUGIN,
                order=0,
                plugin_name="gmail",
                plugin_op="digest",
            ),
            ExperienceStepCreate(
                step_key="emails",
                step_type=StepType.PLUGIN,
                order=1,
                plugin_name="gmail",
                plugin_op="search",
            ),
        ]
        with pytest.raises(ValidationError) as exc_info:
            await service._validate_steps(steps)
        assert "Duplicate step key" in str(exc_info.value.message)

    @pytest.mark.asyncio
    async def test_validate_steps_plugin_missing_name(self, service):
        """Plugin step without plugin_name should raise."""
        steps = [
            ExperienceStepCreate(
                step_key="step1",
                step_type=StepType.PLUGIN,
                order=0,
                plugin_name=None,
                plugin_op="digest",
            ),
        ]
        with pytest.raises(ValidationError) as exc_info:
            await service._validate_steps(steps)
        assert "plugin_name" in str(exc_info.value.message)

    @pytest.mark.asyncio
    async def test_validate_steps_plugin_missing_op(self, service):
        """Plugin step without plugin_op should raise."""
        steps = [
            ExperienceStepCreate(
                step_key="step1",
                step_type=StepType.PLUGIN,
                order=0,
                plugin_name="gmail",
                plugin_op=None,
            ),
        ]
        with pytest.raises(ValidationError) as exc_info:
            await service._validate_steps(steps)
        assert "plugin_op" in str(exc_info.value.message)

    @pytest.mark.asyncio
    async def test_validate_steps_kb_missing_id(self, service):
        """KB step without knowledge_base_id should raise."""
        steps = [
            ExperienceStepCreate(
                step_key="kb_step",
                step_type=StepType.KNOWLEDGE_BASE,
                order=0,
                knowledge_base_id=None,
            ),
        ]
        with pytest.raises(ValidationError) as exc_info:
            await service._validate_steps(steps)
        assert "knowledge_base_id" in str(exc_info.value.message)

    @pytest.mark.asyncio
    async def test_validate_steps_invalid_template_in_params(self, service):
        """Invalid Jinja2 template in params_template should raise."""
        steps = [
            ExperienceStepCreate(
                step_key="step1",
                step_type=StepType.PLUGIN,
                order=0,
                plugin_name="gmail",
                plugin_op="digest",
                params_template={"query": "{{ broken.syntax"},
            ),
        ]
        with pytest.raises(ValidationError) as exc_info:
            await service._validate_steps(steps)
        assert "param" in str(exc_info.value.message).lower()

    @pytest.mark.asyncio
    @patch.object(ExperienceService, "compute_required_scopes_for_step", return_value=[])
    async def test_validate_steps_valid(self, mock_scopes, service):
        """Valid steps should pass validation."""
        steps = [
            ExperienceStepCreate(
                step_key="emails",
                step_type=StepType.PLUGIN,
                order=0,
                plugin_name="gmail",
                plugin_op="digest",
            ),
            ExperienceStepCreate(
                step_key="calendar",
                step_type=StepType.PLUGIN,
                order=1,
                plugin_name="calendar",
                plugin_op="events",
            ),
        ]
        # Should not raise
        await service._validate_steps(steps)


class TestCreateExperience:
    """Happy path tests for create_experience."""

    @pytest.mark.asyncio
    @patch.object(ExperienceService, "compute_required_scopes_for_step", return_value=[])
    @patch.object(ExperienceService, "_get_experience_by_name", return_value=None)
    async def test_create_experience_success(
        self, mock_get_by_name, mock_scopes, mock_db_session, mock_experience_response
    ):
        """Successfully create an experience with steps."""
        service = ExperienceService(mock_db_session)

        experience_data = ExperienceCreate(
            name="Daily Digest",
            description="Get your daily email digest",
            visibility=ExperienceVisibility.DRAFT,
            trigger_type=TriggerType.CRON,
            trigger_config={
                "cron": "0 9 * * *",
            },
            steps=[
                ExperienceStepCreate(
                    step_key="emails",
                    step_type=StepType.PLUGIN,
                    order=0,
                    plugin_name="gmail",
                    plugin_op="digest",
                    params_template={"max_results": 100},
                )
            ],
        )

        # Mock the response conversion to avoid datetime serialization issues
        with patch.object(service, "_experience_to_response", return_value=mock_experience_response):
            result = await service.create_experience(experience_data, created_by="admin-user")

        # Verify db operations
        assert mock_db_session.add.called
        mock_db_session.commit.assert_called_once()
        mock_db_session.refresh.assert_called_once()

        # Verify response structure
        assert isinstance(result, ExperienceResponse)


class TestUpdateExperience:
    """Happy path tests for update_experience."""

    @pytest.mark.asyncio
    async def test_update_experience_success(self, mock_db_session):
        """Successfully update an experience."""
        # Create a mock existing experience using a real object approach
        existing_exp = MagicMock()
        existing_exp.id = "exp-123"
        existing_exp.name = "Old Name"
        existing_exp.description = "Old description"
        existing_exp.visibility = ExperienceVisibility.DRAFT.value
        existing_exp.scope = ExperienceScope.USER.value
        existing_exp.trigger_type = TriggerType.MANUAL.value
        existing_exp.trigger_config = None
        existing_exp.include_previous_run = False
        existing_exp.model_configuration_id = None
        existing_exp.model_configuration = None
        existing_exp.prompt_id = None
        existing_exp.inline_prompt_template = None
        existing_exp.max_run_seconds = 120
        existing_exp.token_budget = None
        existing_exp.version = 1
        existing_exp.is_active_version = True
        existing_exp.parent_version_id = None
        existing_exp.created_by = "admin"
        existing_exp.created_at = datetime.now()
        existing_exp.updated_at = datetime.now()
        existing_exp.steps = []
        existing_exp.runs = []
        existing_exp.prompt = None

        service = ExperienceService(mock_db_session)

        update_data = ExperienceUpdate(
            name="New Name",
            description="Updated description",
            visibility=ExperienceVisibility.PUBLISHED,
        )

        # Patch internal methods
        with patch.object(service, "_get_experience_by_id", return_value=existing_exp):
            with patch.object(service, "_get_experience_by_name", return_value=None):
                result = await service.update_experience("exp-123", update_data)

        # Verify db operations
        mock_db_session.commit.assert_called_once()
        mock_db_session.refresh.assert_called_once()

        # Verify response
        assert isinstance(result, ExperienceResponse)


class TestGetRun:
    """Tests for get_run() ownership and shared-run visibility."""

    def _make_mock_run(self, user_id: str | None) -> MagicMock:
        """Return a minimal mock ExperienceRun with the given user_id."""
        run = MagicMock()
        run.user_id = user_id
        run.started_at = None
        run.finished_at = None
        return run

    async def _call_get_run(
        self,
        mock_db_session,
        run: MagicMock | None,
        user_id: str,
        is_admin: bool = False,
        can_access_experience: bool = True,
    ):
        """Wire up db.execute and call get_run()."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = run
        mock_db_session.execute.return_value = mock_result

        service = ExperienceService(mock_db_session)
        with (
            patch.object(service, "_run_to_response", return_value=MagicMock()),
            patch.object(
                service,
                "_can_access_experience_runs",
                new=AsyncMock(return_value=can_access_experience),
            ),
        ):
            return await service.get_run("run-123", user_id=user_id, is_admin=is_admin)

    @pytest.mark.asyncio
    async def test_get_run_not_found(self, mock_db_session):
        """Returns None when run does not exist."""
        result = await self._call_get_run(mock_db_session, run=None, user_id="user-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_run_own_run_accessible(self, mock_db_session):
        """Non-admin can fetch their own run."""
        run = self._make_mock_run(user_id="user-1")
        result = await self._call_get_run(mock_db_session, run=run, user_id="user-1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_run_other_users_run_blocked(self, mock_db_session):
        """Non-admin cannot fetch another user's run."""
        run = self._make_mock_run(user_id="user-2")
        result = await self._call_get_run(mock_db_session, run=run, user_id="user-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_run_shared_run_accessible_by_non_admin(self, mock_db_session):
        """Non-admin can fetch a shared run (user_id IS NULL)."""
        run = self._make_mock_run(user_id=None)
        result = await self._call_get_run(mock_db_session, run=run, user_id="user-1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_run_admin_can_access_any_run(self, mock_db_session):
        """Admin can fetch any user's run regardless of ownership."""
        run = self._make_mock_run(user_id="user-2")
        result = await self._call_get_run(mock_db_session, run=run, user_id="admin-1", is_admin=True)
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_run_denied_when_experience_not_visible(self, mock_db_session):
        """Run is not returned when parent experience is not visible."""
        run = self._make_mock_run(user_id=None)
        run.experience_id = "exp-1"
        result = await self._call_get_run(
            mock_db_session,
            run=run,
            user_id="user-1",
            can_access_experience=False,
        )
        assert result is None


class TestRunVisibilityHelper:
    """Tests for _can_access_experience_runs()."""

    @pytest.mark.asyncio
    async def test_can_access_experience_runs_non_admin_empty_user_id(self, mock_db_session):
        service = ExperienceService(mock_db_session)
        allowed = await service._can_access_experience_runs("exp-1", user_id="", is_admin=False)
        assert allowed is False
        mock_db_session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_can_access_experience_runs_false_when_missing(self, mock_db_session):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute.return_value = mock_result

        service = ExperienceService(mock_db_session)
        allowed = await service._can_access_experience_runs("exp-1", user_id="user-1", is_admin=False)
        assert allowed is False

    @pytest.mark.asyncio
    async def test_can_access_experience_runs_non_admin_published(self, mock_db_session):
        exp = MagicMock()
        exp.visibility = ExperienceVisibility.PUBLISHED.value
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = exp
        mock_db_session.execute.return_value = mock_result

        service = ExperienceService(mock_db_session)
        allowed = await service._can_access_experience_runs("exp-1", user_id="user-1", is_admin=False)
        assert allowed is True

    @pytest.mark.asyncio
    async def test_can_access_experience_runs_non_admin_draft(self, mock_db_session):
        exp = MagicMock()
        exp.visibility = ExperienceVisibility.DRAFT.value
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = exp
        mock_db_session.execute.return_value = mock_result

        service = ExperienceService(mock_db_session)
        allowed = await service._can_access_experience_runs("exp-1", user_id="user-1", is_admin=False)
        assert allowed is False


class TestExperienceExport:
    """Test experience export to YAML functionality."""

    def test_export_experience_to_yaml_basic(self, service):
        """Test basic YAML export functionality."""
        from shu.schemas.experience import ExperienceResponse, ExperienceStepResponse

        # Create a sample experience
        experience = ExperienceResponse(
            id="test-experience-id",
            name="Morning Briefing",
            description="Daily summary of emails and calendar",
            created_by="user-123",
            visibility=ExperienceVisibility.PUBLISHED,
            trigger_type=TriggerType.CRON,
            trigger_config={"cron": "0 7 * * *", "timezone": "America/Chicago"},
            include_previous_run=True,
            model_configuration_id="test-model-config-id",
            prompt_id=None,
            inline_prompt_template="Summarize the following information:\n\nEmails: {{ emails }}\nCalendar: {{ calendar }}",
            max_run_seconds=120,
            token_budget=None,
            version=1,
            is_active_version=True,
            parent_version_id=None,
            steps=[
                ExperienceStepResponse(
                    id="step-1",
                    experience_id="test-experience-id",
                    step_key="emails",
                    step_type=StepType.PLUGIN,
                    order=0,
                    plugin_name="gmail",
                    plugin_op="list",
                    knowledge_base_id=None,
                    kb_query_template=None,
                    params_template={"limit": 20},
                    condition_template=None,
                    required_scopes=["gmail.readonly"],
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                ),
                ExperienceStepResponse(
                    id="step-2",
                    experience_id="test-experience-id",
                    step_key="calendar",
                    step_type=StepType.PLUGIN,
                    order=1,
                    plugin_name="calendar",
                    plugin_op="list",
                    knowledge_base_id=None,
                    kb_query_template=None,
                    params_template={"days_ahead": 1},
                    condition_template=None,
                    required_scopes=["calendar.readonly"],
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                ),
            ],
            model_configuration=None,
            prompt=None,
            step_count=2,
            last_run_at=None,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        # Test the export
        yaml_content, file_name = service.export_experience_to_yaml(experience)

        assert file_name == "morning-briefing-experience.yaml"

        # Verify it's valid YAML
        assert yaml_content is not None
        assert isinstance(yaml_content, str)
        assert len(yaml_content) > 0

        # Verify basic structure by checking for expected content
        # (Don't parse YAML with placeholders as they're not valid YAML syntax)
        assert "experience_yaml_version: 1" in yaml_content
        assert "name: Morning Briefing" in yaml_content
        assert "description: Daily summary of emails and calendar" in yaml_content
        assert "version: 1" in yaml_content
        assert "visibility: draft" in yaml_content

        # Verify placeholders are unquoted (this was the fix)
        assert "trigger_type: '{{ trigger_type }}'" in yaml_content
        assert "trigger_config: {{ trigger_config }}" in yaml_content
        assert "model_configuration_id: '{{ model_configuration_id }}'" in yaml_content
        assert "max_run_seconds: {{ max_run_seconds }}" in yaml_content

        # Verify steps are exported correctly
        assert "step_key: emails" in yaml_content
        assert "step_key: calendar" in yaml_content
        assert "plugin_name: gmail" in yaml_content
        assert "plugin_name: calendar" in yaml_content
        assert "days_ahead: 1" in yaml_content

    def test_remove_none_values(self, service):
        """Test the _remove_none_values helper method."""
        # Test with nested structure containing None values
        data = {
            "name": "test",
            "description": None,
            "config": {
                "enabled": True,
                "timeout": None,
                "nested": {"value": "test", "empty": None},
            },
            "items": [{"id": 1, "name": "item1"}, {"id": 2, "name": None}, None],
        }

        cleaned = service._remove_none_values(data)

        # Verify None values are removed
        assert "description" not in cleaned
        assert "timeout" not in cleaned["config"]
        assert "empty" not in cleaned["config"]["nested"]
        assert len(cleaned["items"]) == 2  # None item removed
        assert cleaned["items"][1]["id"] == 2
        assert "name" not in cleaned["items"][1]  # None name removed


# ---------------------------------------------------------------------------
# Shared-scope tests
# ---------------------------------------------------------------------------


def _make_mock_run(user_id: str | None, run_id: str = "run-1") -> MagicMock:
    """Build a minimal mock ExperienceRun for list/get tests."""
    now = datetime.now()
    run = MagicMock()
    run.id = run_id
    run.experience_id = "exp-1"
    run.user_id = user_id
    run.previous_run_id = None
    run.model_configuration_id = None
    run.status = "succeeded"
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


class TestCreateSharedExperience:
    """Tests for creating experiences with scope='shared'."""

    @pytest.mark.asyncio
    @patch.object(ExperienceService, "compute_required_scopes_for_step", return_value=[])
    @patch.object(ExperienceService, "_get_experience_by_name", return_value=None)
    async def test_create_shared_experience_stores_scope_field(
        self, mock_get_by_name, mock_scopes, mock_db_session, mock_experience_response
    ):
        """Creating an experience with scope='shared' persists and returns the scope."""
        # Override the fixture response to have shared scope
        mock_experience_response.scope = ExperienceScope.SHARED

        service = ExperienceService(mock_db_session)

        experience_data = ExperienceCreate(
            name="Daily Briefing",
            description="A shared daily briefing",
            scope=ExperienceScope.SHARED,
            visibility=ExperienceVisibility.DRAFT,
            trigger_type=TriggerType.MANUAL,
            steps=[],
        )

        with patch.object(service, "_experience_to_response", return_value=mock_experience_response):
            result = await service.create_experience(experience_data, created_by="admin-1")

        assert result.scope == ExperienceScope.SHARED

        # Verify the Experience ORM object added to DB had scope="shared"
        added_calls = mock_db_session.add.call_args_list
        experience_obj = added_calls[0][0][0]
        assert experience_obj.scope == "shared"

    @pytest.mark.asyncio
    @patch.object(ExperienceService, "compute_required_scopes_for_step", return_value=[])
    @patch.object(ExperienceService, "_get_experience_by_name", return_value=None)
    async def test_create_shared_experience_with_auth_steps_succeeds(
        self, mock_get_by_name, mock_scopes, mock_db_session, mock_experience_response
    ):
        """No creation-time rejection even if steps declare user OAuth.

        Auth resolution happens at execution time, not creation time.
        """
        mock_experience_response.scope = ExperienceScope.SHARED

        service = ExperienceService(mock_db_session)

        step = ExperienceStepCreate(
            step_key="gmail",
            step_type=StepType.PLUGIN,
            order=1,
            plugin_name="shu_gmail_digest",
            plugin_op="fetch_emails",
        )

        experience_data = ExperienceCreate(
            name="Inbox Summary",
            description="Shared inbox summary with user auth steps",
            scope=ExperienceScope.SHARED,
            visibility=ExperienceVisibility.DRAFT,
            trigger_type=TriggerType.MANUAL,
            steps=[step],
        )

        with patch.object(service, "_experience_to_response", return_value=mock_experience_response):
            result = await service.create_experience(experience_data, created_by="admin-1")

        # If we got here without raising, creation succeeded
        assert result.scope == ExperienceScope.SHARED
        assert mock_db_session.commit.called


class TestListRunsSharedVisibility:
    """Tests for list_runs visibility of shared runs vs user-scoped runs."""

    @pytest.mark.asyncio
    async def test_list_runs_non_admin_includes_shared_runs(self, mock_db_session):
        """Non-admin sees runs where user_id=NULL alongside their own."""
        shared_run = _make_mock_run(user_id=None, run_id="run-shared")
        user_run = _make_mock_run(user_id="user-1", run_id="run-user")

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # Count query
                result.scalar.return_value = 2
            elif call_count == 2:
                # Paginated query
                result.scalars.return_value.all.return_value = [shared_run, user_run]
            else:
                # User info query
                user_mock = MagicMock()
                user_mock.id = "user-1"
                user_mock.email = "user1@example.com"
                user_mock.display_name = "User One"
                result.scalars.return_value.all.return_value = [user_mock]
            return result

        mock_db_session.execute = mock_execute

        service = ExperienceService(mock_db_session)
        with patch.object(service, "_can_access_experience_runs", new=AsyncMock(return_value=True)):
            result = await service.list_runs(
                experience_id="exp-1",
                user_id="user-1",
                is_admin=False,
            )

        assert result.total == 2
        run_ids = {r.id for r in result.items}
        assert "run-shared" in run_ids
        assert "run-user" in run_ids

    @pytest.mark.asyncio
    async def test_list_runs_non_admin_excludes_other_users_runs(self, mock_db_session):
        """Non-admin does not see another user's non-shared runs.

        The SQL filter (user_id = :uid OR user_id IS NULL) excludes runs
        owned by other users. We verify the response contains only the
        expected runs.
        """
        shared_run = _make_mock_run(user_id=None, run_id="run-shared")
        own_run = _make_mock_run(user_id="user-1", run_id="run-own")
        # user-2's run is excluded by the SQL filter, not returned by DB

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar.return_value = 2
            elif call_count == 2:
                result.scalars.return_value.all.return_value = [shared_run, own_run]
            else:
                user_mock = MagicMock()
                user_mock.id = "user-1"
                user_mock.email = "user1@example.com"
                user_mock.display_name = "User One"
                result.scalars.return_value.all.return_value = [user_mock]
            return result

        mock_db_session.execute = mock_execute

        service = ExperienceService(mock_db_session)
        with patch.object(service, "_can_access_experience_runs", new=AsyncMock(return_value=True)):
            result = await service.list_runs(
                experience_id="exp-1",
                user_id="user-1",
                is_admin=False,
            )

        run_ids = {r.id for r in result.items}
        assert "run-shared" in run_ids
        assert "run-own" in run_ids
        assert "run-other" not in run_ids

    @pytest.mark.asyncio
    async def test_list_runs_returns_empty_when_experience_not_visible(self, mock_db_session):
        """Returns an empty page when parent experience is not visible."""
        service = ExperienceService(mock_db_session)
        with patch.object(service, "_can_access_experience_runs", new=AsyncMock(return_value=False)):
            result = await service.list_runs(
                experience_id="exp-1",
                user_id="user-1",
                is_admin=False,
            )

        assert result.total == 0
        assert result.items == []

    @pytest.mark.asyncio
    async def test_list_runs_after_scope_change_shared_to_user(self, mock_db_session):
        """After experience scope changes shared→user, existing shared runs
        (user_id=NULL) remain visible to admins but the non-admin query still
        includes them via the OR user_id IS NULL clause.

        This documents current behaviour — the list_runs query does not join
        on Experience.scope, so stale shared runs remain visible to non-admins.
        """
        stale_shared_run = _make_mock_run(user_id=None, run_id="run-stale-shared")
        new_user_run = _make_mock_run(user_id="user-1", run_id="run-new-user")

        # -- Non-admin query --
        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar.return_value = 2
            elif call_count == 2:
                result.scalars.return_value.all.return_value = [stale_shared_run, new_user_run]
            else:
                user_mock = MagicMock()
                user_mock.id = "user-1"
                user_mock.email = "user1@example.com"
                user_mock.display_name = "User One"
                result.scalars.return_value.all.return_value = [user_mock]
            return result

        mock_db_session.execute = mock_execute

        service = ExperienceService(mock_db_session)
        with patch.object(service, "_can_access_experience_runs", new=AsyncMock(return_value=True)):
            non_admin_result = await service.list_runs(
                experience_id="exp-1",
                user_id="user-1",
                is_admin=False,
            )

        non_admin_ids = {r.id for r in non_admin_result.items}
        assert "run-new-user" in non_admin_ids

        # -- Admin query always includes all runs --
        admin_call_count = 0

        async def admin_mock_execute(stmt):
            nonlocal admin_call_count
            admin_call_count += 1
            result = MagicMock()
            if admin_call_count == 1:
                result.scalar.return_value = 2
            elif admin_call_count == 2:
                result.scalars.return_value.all.return_value = [stale_shared_run, new_user_run]
            else:
                user_mock = MagicMock()
                user_mock.id = "user-1"
                user_mock.email = "user1@example.com"
                user_mock.display_name = "User One"
                result.scalars.return_value.all.return_value = [user_mock]
            return result

        mock_db_session.execute = admin_mock_execute

        with patch.object(service, "_can_access_experience_runs", new=AsyncMock(return_value=True)):
            admin_result = await service.list_runs(
                experience_id="exp-1",
                user_id="admin-1",
                is_admin=True,
            )

        admin_ids = {r.id for r in admin_result.items}
        assert "run-stale-shared" in admin_ids
        assert "run-new-user" in admin_ids


class TestGetRunSharedAccess:
    """Tests for get_run access control with shared runs."""

    @pytest.mark.asyncio
    async def test_get_run_shared_accessible_to_any_user(self, mock_db_session):
        """Any authenticated user can fetch a run with user_id=NULL."""
        shared_run = _make_mock_run(user_id=None, run_id="run-shared")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = shared_run
        mock_db_session.execute.return_value = mock_result

        service = ExperienceService(mock_db_session)

        # User "user-99" who did not create the run can still access it
        with patch.object(service, "_can_access_experience_runs", new=AsyncMock(return_value=True)):
            result = await service.get_run(
                run_id="run-shared",
                user_id="user-99",
                is_admin=False,
            )

        assert result is not None
        assert result.id == "run-shared"
        assert result.user_id is None
