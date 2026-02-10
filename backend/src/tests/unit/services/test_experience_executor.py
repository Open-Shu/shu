"""
Unit tests for ExperienceExecutor.

Tests cover:
- Context building
- Template rendering
- Condition evaluation
- Plugin step execution (mocked)
- KB step execution (mocked)
- LLM synthesis (mocked)
- Run state persistence
- Error handling
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.services.experience_executor import (
    ExperienceEvent,
    ExperienceEventType,
    ExperienceExecutor,
)


class TestExperienceEvent:
    """
    Tests for ExperienceEvent dataclass.
    """

    def test_to_dict_basic(self):
        """Test basic event serialization."""
        event = ExperienceEvent(ExperienceEventType.RUN_STARTED, {"run_id": "123"})
        result = event.to_dict()
        assert result == {"type": "run_started", "run_id": "123"}

    def test_to_dict_empty_data(self):
        """Test event with no data."""
        event = ExperienceEvent(ExperienceEventType.SYNTHESIS_STARTED)
        result = event.to_dict()
        assert result == {"type": "synthesis_started"}


class TestContextBuilding:
    """
    Tests for _build_initial_context_async method.
    """

    @pytest.fixture
    def executor(self):
        """Create an executor with mocked dependencies."""
        db = AsyncMock()
        config_manager = MagicMock()
        return ExperienceExecutor(db, config_manager)

    @pytest.fixture
    def mock_user(self):
        """Create a mock User object."""
        user = MagicMock()
        user.id = "user-123"
        user.email = "test@example.com"
        user.display_name = "Test User"
        return user

    @pytest.mark.asyncio
    async def test_build_context_basic(self, executor, mock_user):
        """Test basic context building."""
        experience = MagicMock()
        experience.include_previous_run = False

        # Mock the datetime formatting method
        executor._get_user_formatted_datetime = AsyncMock(return_value="Monday, January 15, 2024 at 2:30 PM EST")

        context = await executor._build_initial_context(
            experience=experience,
            user_id="user-123",
            current_user=mock_user,
            input_params={"query": "test"},
        )

        assert context["user"]["id"] == "user-123"
        assert context["user"]["email"] == "test@example.com"
        assert context["input"] == {"query": "test"}
        assert context["steps"] == {}
        assert context["previous_run"] is None
        assert context["now"] == "Monday, January 15, 2024 at 2:30 PM EST"

    @pytest.mark.asyncio
    async def test_build_context_with_previous_run(self, executor, mock_user):
        """Test context includes previous run data."""
        experience = MagicMock()
        experience.include_previous_run = True

        previous_run = MagicMock()
        previous_run.result_content = "Previous summary"
        previous_run.step_outputs = {"old_step": {"data": 123}}
        previous_run.finished_at = datetime(2024, 1, 1, tzinfo=UTC)

        executor._get_previous_run = AsyncMock(return_value=previous_run)
        executor._get_user_formatted_datetime = AsyncMock(return_value="Monday, January 15, 2024 at 2:30 PM EST")

        context = await executor._build_initial_context(
            experience=experience,
            user_id="user-123",
            current_user=mock_user,
            input_params={},
        )

        assert context["previous_run"]["result_content"] == "Previous summary"
        assert context["previous_run"]["step_outputs"] == {"old_step": {"data": 123}}
        assert context["now"] == "Monday, January 15, 2024 at 2:30 PM EST"


class TestTemplateRendering:
    """
    Tests for _render_template method. We'll keep this simple because it's done by Jinja.
    """

    @pytest.fixture
    def executor(self):
        db = AsyncMock()
        config_manager = MagicMock()
        return ExperienceExecutor(db, config_manager)

    def test_render_with_step_data(self, executor):
        """Test rendering template with step data."""
        context = {"steps": {"emails": {"data": {"count": 5}}}}
        result = executor._render_template("You have {{ steps.emails.data.count }} emails.", context)
        assert result == "You have 5 emails."


class TestParamsRendering:
    """
    Tests for _render_params method.
    """

    @pytest.fixture
    def executor(self):
        db = AsyncMock()
        config_manager = MagicMock()
        return ExperienceExecutor(db, config_manager)

    def test_render_params_with_templates(self, executor):
        """Test rendering params with Jinja2 expressions."""
        params_template = {
            "query": "{{ input.search_term }}",
            "limit": 10,  # Non-template value
        }
        context = {"input": {"search_term": "hello world"}}

        result = executor._render_params(params_template, context)

        assert result["query"] == "hello world"
        assert result["limit"] == 10

    def test_render_params_empty(self, executor):
        """Test rendering None params returns empty dict."""
        result = executor._render_params(None, {})
        assert result == {}


class TestRequiredStepCheck:
    """
    Tests for _check_should_run_step method.
    """

    @pytest.fixture
    def executor(self):
        db = AsyncMock()
        config_manager = MagicMock()
        return ExperienceExecutor(db, config_manager)

    def test_no_condition(self, executor):
        """Test step with no condition."""
        step = MagicMock()
        step.condition_template = None
        result, reason = executor._check_should_run_step(step, {})
        assert result is True
        assert reason is None

    def test_required_step_succeeded(self, executor):
        """Test when required step succeeded."""
        step = MagicMock()
        step.condition_template = "emails"
        context = {"steps": {"emails": {"data": {"count": 5}, "status": "succeeded"}}}
        result, reason = executor._check_should_run_step(step, context)
        assert result is True
        assert reason is None

    def test_required_step_failed(self, executor):
        """Test when required step failed."""
        step = MagicMock()
        step.condition_template = "emails"
        context = {"steps": {"emails": {"data": None, "status": "failed"}}}
        result, reason = executor._check_should_run_step(step, context)
        assert result is False
        assert "did not succeed" in reason

    def test_required_step_not_found(self, executor):
        """Test when required step doesn't exist."""
        step = MagicMock()
        step.condition_template = "emails"
        context = {"steps": {}}
        result, reason = executor._check_should_run_step(step, context)
        assert result is False
        assert "did not succeed" in reason


class TestStepSummary:
    """
    Tests for _build_step_summary method.
    """

    @pytest.fixture
    def executor(self):
        db = AsyncMock()
        config_manager = MagicMock()
        return ExperienceExecutor(db, config_manager)

    def test_plugin_step_with_count(self, executor):
        """Test summary for plugin step with count field."""
        step = MagicMock()
        step.step_type = "plugin"
        step.plugin_name = "gmail"

        output = {"count": 10}
        result = executor._build_step_summary(step, output)
        assert result == "Retrieved 10 items"

    def test_plugin_step_with_list(self, executor):
        """Test summary for plugin step with messages list."""
        step = MagicMock()
        step.step_type = "plugin"
        step.plugin_name = "gmail"

        output = {"messages": [{"id": 1}, {"id": 2}, {"id": 3}]}
        result = executor._build_step_summary(step, output)
        assert result == "Retrieved 3 messages"

    def test_kb_step_summary(self, executor):
        """Test summary for KB step."""
        step = MagicMock()
        step.step_type = "knowledge_base"
        step.step_key = "docs"

        output = {"results": [{"title": "Doc1"}, {"title": "Doc2"}]}
        result = executor._build_step_summary(step, output)
        assert result == "Found 2 KB results"


class TestPluginStepExecution:
    """Tests for _execute_plugin_step method."""

    @pytest.fixture
    def executor(self):
        db = AsyncMock()
        config_manager = MagicMock()
        return ExperienceExecutor(db, config_manager)

    @pytest.mark.asyncio
    async def test_execute_plugin_step_missing_name(self, executor):
        """Test that missing plugin_name raises error."""
        step = MagicMock()
        step.step_key = "test_step"
        step.plugin_name = None

        with pytest.raises(ValueError, match="missing plugin_name"):
            await executor._execute_plugin_step(step, {}, "user-123")

    @pytest.mark.asyncio
    @patch("shu.services.experience_executor.execute_plugin")
    async def test_execute_plugin_step_success(self, mock_execute_plugin, executor):
        """Test successful plugin execution."""
        mock_execute_plugin.return_value = {
            "status": "success",
            "data": {"messages": [{"id": 1}]},
        }

        step = MagicMock()
        step.step_key = "emails"
        step.plugin_name = "gmail"
        step.plugin_op = "list"
        step.params_template = {}

        result = await executor._execute_plugin_step(step, {}, "user-123")

        assert result == {"messages": [{"id": 1}]}
        mock_execute_plugin.assert_called_once()

    @pytest.mark.asyncio
    @patch("shu.services.experience_executor.execute_plugin")
    async def test_execute_plugin_step_failure(self, mock_execute_plugin, executor):
        """Test plugin execution failure."""
        mock_execute_plugin.return_value = {
            "status": "error",
            "error": {"message": "Something went wrong"},
        }

        step = MagicMock()
        step.step_key = "emails"
        step.plugin_name = "gmail"
        step.plugin_op = "list"
        step.params_template = {}

        with pytest.raises(ValueError, match="Something went wrong"):
            await executor._execute_plugin_step(step, {}, "user-123")


class TestKBStepExecution:
    """Tests for _execute_kb_step method."""

    @pytest.fixture
    def executor(self):
        db = AsyncMock()
        config_manager = MagicMock()
        return ExperienceExecutor(db, config_manager)

    @pytest.mark.asyncio
    async def test_execute_kb_step_missing_id(self, executor):
        """Test that missing knowledge_base_id raises error."""
        step = MagicMock()
        step.step_key = "docs"
        step.knowledge_base_id = None

        user = MagicMock()

        with pytest.raises(ValueError, match="missing knowledge_base_id"):
            await executor._execute_kb_step(step, {}, user)

    @pytest.mark.asyncio
    async def test_execute_kb_step_no_query(self, executor):
        """Test that empty query raises error."""
        step = MagicMock()
        step.step_key = "docs"
        step.knowledge_base_id = "kb-123"
        step.kb_query_template = None

        context = {"input": {}}  # No query in input
        user = MagicMock()

        with pytest.raises(ValueError, match="no query text"):
            await executor._execute_kb_step(step, context, user)

    @pytest.mark.asyncio
    @patch("shu.services.experience_executor.execute_rag_queries")
    async def test_execute_kb_step_success(self, mock_rag, executor):
        """Test successful KB query."""

        # Invoke the builder to verify signature
        def side_effect(*args, **kwargs):
            builder = args[6]  # request_builder
            # Call with expected 3 args
            builder("kb-123", {"search_type": "hybrid"}, "clean query")
            return (
                "clean query",
                {},
                [{"knowledge_base_id": "kb-123", "response": {"results": [{"id": 1}]}}],
            )

        mock_rag.side_effect = side_effect

        step = MagicMock()
        step.step_key = "docs"
        step.knowledge_base_id = "kb-123"
        step.kb_query_template = "some query"

        user = MagicMock()
        result = await executor._execute_kb_step(step, {}, user)

        assert result == {"results": [{"id": 1}]}


class TestRunManagement:
    """Tests for run creation and finalization."""

    @pytest.fixture
    def executor(self):
        db = AsyncMock()
        config_manager = MagicMock()
        return ExperienceExecutor(db, config_manager)

    @pytest.mark.asyncio
    async def test_create_run(self, executor):
        """Test run creation."""
        experience = MagicMock()
        experience.id = "exp-123"
        experience.model_configuration_id = "config-1"

        executor.db.add = MagicMock()
        executor.db.commit = AsyncMock()
        executor.db.refresh = AsyncMock()

        run = await executor._create_or_resume_run(experience, "user-123", {"query": "test"})

        executor.db.add.assert_called_once()
        executor.db.commit.assert_called_once()

        assert run.experience_id == "exp-123"
        assert run.user_id == "user-123"
        assert run.input_params == {"query": "test"}
        assert run.status == "running"
        assert run.model_configuration_id == "config-1"

    @pytest.mark.asyncio
    async def test_finalize_run_success(self, executor):
        """Test run finalization with success status."""
        run = MagicMock()
        executor.db.commit = AsyncMock()

        await executor._finalize_run(
            run,
            status="succeeded",
            step_states={"step1": {"status": "succeeded"}},
            step_outputs={"step1": {"data": 123}},
            result_content="Final summary",
            result_metadata={"tokens": 100},
        )

        assert run.status == "succeeded"
        assert run.result_content == "Final summary"
        executor.db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_finalize_run_failure(self, executor):
        """Test run finalization with failure status."""
        run = MagicMock()
        executor.db.commit = AsyncMock()

        await executor._finalize_run(
            run,
            status="failed",
            step_states={},
            step_outputs={},
            error_message="Something went wrong",
        )

        assert run.status == "failed"
        assert run.error_message == "Something went wrong"


class TestDatetimeFormatting:
    """Tests for user timezone datetime formatting."""

    @pytest.fixture
    def executor(self):
        """Create an executor with mocked dependencies."""
        db = AsyncMock()
        config_manager = MagicMock()
        return ExperienceExecutor(db, config_manager)

    @pytest.mark.asyncio
    async def test_get_user_formatted_datetime_with_timezone(self, executor):
        """Test datetime formatting with user timezone preference."""
        # Mock user preferences query result
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = "America/New_York"
        executor.db.execute.return_value = mock_result

        # Mock datetime.now to return a fixed time for consistent testing
        with patch("shu.services.experience_executor.datetime") as mock_datetime:
            fixed_utc_time = datetime(2024, 1, 15, 19, 30, 0, tzinfo=UTC)  # Monday 7:30 PM UTC
            mock_datetime.now.return_value = fixed_utc_time

            result = await executor._get_user_formatted_datetime("test-user-id")

            # Should format in Eastern Time (UTC-5 in January)
            # 7:30 PM UTC = 2:30 PM EST on Monday
            assert "Monday" in result
            assert "January 15, 2024" in result
            assert "2:30 PM" in result or "14:30" in result  # Handle different time formats

    @pytest.mark.asyncio
    async def test_get_user_formatted_datetime_fallback_to_utc(self, executor):
        """Test datetime formatting falls back to UTC when user timezone is invalid."""
        # Mock user preferences query result with invalid timezone
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = "Invalid/Timezone"
        executor.db.execute.return_value = mock_result

        with patch("shu.services.experience_executor.datetime") as mock_datetime:
            fixed_utc_time = datetime(2024, 1, 15, 19, 30, 0, tzinfo=UTC)
            mock_datetime.now.return_value = fixed_utc_time

            result = await executor._get_user_formatted_datetime("test-user-id")

            # Should fall back to UTC and include timezone info
            assert "Monday" in result
            assert "January 15, 2024" in result
            assert "7:30 PM" in result or "19:30" in result
            assert "UTC" in result

    @pytest.mark.asyncio
    async def test_get_user_formatted_datetime_no_preferences(self, executor):
        """Test datetime formatting when user has no timezone preferences."""
        # Mock user preferences query result returning None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        executor.db.execute.return_value = mock_result

        with patch("shu.services.experience_executor.datetime") as mock_datetime:
            fixed_utc_time = datetime(2024, 1, 15, 19, 30, 0, tzinfo=UTC)
            mock_datetime.now.return_value = fixed_utc_time

            result = await executor._get_user_formatted_datetime("test-user-id")

            # Should use UTC as default
            assert "Monday" in result
            assert "January 15, 2024" in result
            assert "7:30 PM" in result or "19:30" in result
            assert "UTC" in result

    @pytest.mark.asyncio
    async def test_get_user_formatted_datetime_db_error(self, executor):
        """Test datetime formatting when database query fails."""
        # Mock database error
        executor.db.execute.side_effect = Exception("Database error")

        with patch("shu.services.experience_executor.datetime") as mock_datetime:
            fixed_utc_time = datetime(2024, 1, 15, 19, 30, 0, tzinfo=UTC)
            mock_datetime.now.return_value = fixed_utc_time

            result = await executor._get_user_formatted_datetime("test-user-id")

            # Should handle error gracefully and fall back to UTC
            assert "Monday" in result
            assert "January 15, 2024" in result
            assert "UTC" in result


class TestPreviousRunBacklink:
    """Tests for previous run retrieval."""

    @pytest.fixture
    def executor(self):
        db = AsyncMock()
        config_manager = MagicMock()
        return ExperienceExecutor(db, config_manager)

    @pytest.mark.asyncio
    async def test_get_previous_run_found(self, executor):
        """Test retrieving previous successful run."""
        mock_run = MagicMock()
        mock_run.result_content = "Previous result"

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_run
        executor.db.execute = AsyncMock(return_value=mock_result)

        result = await executor._get_previous_run("exp-123", "user-123")

        assert result == mock_run

    @pytest.mark.asyncio
    async def test_get_previous_run_not_found(self, executor):
        """Test when no previous run exists."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        executor.db.execute = AsyncMock(return_value=mock_result)

        result = await executor._get_previous_run("exp-123", "user-123")

        assert result is None


class TestModelConfigurationValidation:
    """Tests for model configuration validation in executor."""

    @pytest.fixture
    def executor(self):
        """Create an executor with mocked dependencies."""
        db = AsyncMock()
        config_manager = MagicMock()
        mock_model_config_service = AsyncMock()
        return ExperienceExecutor(db, config_manager, mock_model_config_service)

    @pytest.fixture
    def mock_user(self):
        """Create a mock user."""
        user = MagicMock()
        user.id = "user-123"
        user.email = "test@example.com"
        return user

    @pytest.fixture
    def mock_model_config(self):
        """Create a mock model configuration."""
        config = MagicMock()
        config.id = "config-123"
        config.name = "Test Config"
        config.is_active = True
        config.llm_provider = MagicMock()
        config.llm_provider.name = "OpenAI"
        config.llm_provider.is_active = True
        config.model_name = "gpt-4"
        return config

    @pytest.mark.asyncio
    async def test_validate_and_load_model_config_success(self, executor, mock_user, mock_model_config):
        """Test successful model configuration validation and loading."""
        executor.model_config_service.validate_model_configuration_for_use.return_value = mock_model_config

        result = await executor._validate_and_load_model_config("config-123", mock_user)

        assert result == mock_model_config
        executor.model_config_service.validate_model_configuration_for_use.assert_called_once_with(
            "config-123", current_user=mock_user, include_relationships=True
        )

    @pytest.mark.asyncio
    async def test_validate_and_load_model_config_not_found(self, executor, mock_user):
        """Test when model configuration is not found."""
        from shu.core.exceptions import ModelConfigurationNotFoundError

        executor.model_config_service.validate_model_configuration_for_use.side_effect = (
            ModelConfigurationNotFoundError("config-123")
        )

        result = await executor._validate_and_load_model_config("config-123", mock_user)

        assert result is None

    @pytest.mark.asyncio
    async def test_validate_and_load_model_config_inactive(self, executor, mock_user):
        """Test when model configuration is inactive."""
        from shu.core.exceptions import ModelConfigurationInactiveError

        executor.model_config_service.validate_model_configuration_for_use.side_effect = (
            ModelConfigurationInactiveError("Test Config", "config-123")
        )

        result = await executor._validate_and_load_model_config("config-123", mock_user)

        assert result is None

    @pytest.mark.asyncio
    async def test_validate_and_load_model_config_provider_inactive(self, executor, mock_user):
        """Test when model configuration provider is inactive."""
        from shu.core.exceptions import ModelConfigurationProviderInactiveError

        executor.model_config_service.validate_model_configuration_for_use.side_effect = (
            ModelConfigurationProviderInactiveError("Test Config", "Test Provider")
        )

        result = await executor._validate_and_load_model_config("config-123", mock_user)

        assert result is None

    @pytest.mark.asyncio
    async def test_validate_and_load_model_config_generic_error(self, executor, mock_user):
        """Test when a generic error occurs during validation."""
        executor.model_config_service.validate_model_configuration_for_use.side_effect = Exception(
            "Database connection failed"
        )

        result = await executor._validate_and_load_model_config("config-123", mock_user)

        assert result is None

    @pytest.mark.asyncio
    async def test_model_configuration_validation_success(self, executor, mock_user, mock_model_config):
        """Test successful model configuration validation during execution."""
        executor.model_config_service.validate_model_configuration_for_use.return_value = mock_model_config

        mock_experience = MagicMock()
        mock_experience.id = "exp-123"
        mock_experience.model_configuration_id = "config-123"
        mock_experience.steps = []

        with (
            patch.object(executor, "_create_or_resume_run") as mock_create_run,
            patch.object(executor, "_build_initial_context") as mock_build_context,
            patch.object(executor, "_execute_steps_loop") as mock_execute_steps,
            patch.object(executor, "_synthesize_with_llm_streaming") as mock_synthesize,
            patch.object(executor, "_finalize_run") as mock_finalize,
        ):
            mock_run = MagicMock()
            mock_run.id = "run-123"
            mock_create_run.return_value = mock_run
            mock_build_context.return_value = {"steps": {}}

            # Mock async generators
            async def mock_steps_gen():
                yield MagicMock(type="step_completed")

            async def mock_synthesis_gen():
                yield "Hello"
                yield {"model": "gpt-4"}

            mock_execute_steps.return_value = mock_steps_gen()
            mock_synthesize.return_value = mock_synthesis_gen()

            # Execute streaming to test model config validation
            events = []
            async for event in executor.execute_streaming(mock_experience, "user-123", {}, mock_user):
                events.append(event)
                if len(events) >= 3:  # Just get a few events to test validation
                    break

            # Verify model configuration validation was called
            executor.model_config_service.validate_model_configuration_for_use.assert_called_once_with(
                "config-123", current_user=mock_user, include_relationships=True
            )

    @pytest.mark.asyncio
    async def test_model_configuration_validation_not_found(self, executor, mock_user):
        """Test when model configuration is not found during execution."""
        from shu.core.exceptions import ModelConfigurationNotFoundError

        executor.model_config_service.validate_model_configuration_for_use.side_effect = (
            ModelConfigurationNotFoundError("config-123")
        )

        mock_experience = MagicMock()
        mock_experience.id = "exp-123"
        mock_experience.model_configuration_id = "config-123"

        with (
            patch.object(executor, "_create_or_resume_run") as mock_create_run,
            patch.object(executor, "_finalize_run") as mock_finalize,
        ):
            mock_run = MagicMock()
            mock_run.id = "run-123"
            mock_create_run.return_value = mock_run

            # Execute streaming and expect error event
            events = []
            async for event in executor.execute_streaming(mock_experience, "user-123", {}, mock_user):
                events.append(event)

            # Should have error event
            error_events = [e for e in events if e.type == "error"]
            assert len(error_events) > 0
            assert "Model configuration validation failed" in error_events[0].data["message"]

    @pytest.mark.asyncio
    async def test_model_configuration_validation_inactive(self, executor, mock_user):
        """Test when model configuration is inactive during execution."""
        from shu.core.exceptions import ModelConfigurationInactiveError

        executor.model_config_service.validate_model_configuration_for_use.side_effect = (
            ModelConfigurationInactiveError("Test Config", "config-123")
        )

        mock_experience = MagicMock()
        mock_experience.id = "exp-123"
        mock_experience.model_configuration_id = "config-123"

        with (
            patch.object(executor, "_create_or_resume_run") as mock_create_run,
            patch.object(executor, "_finalize_run") as mock_finalize,
        ):
            mock_run = MagicMock()
            mock_run.id = "run-123"
            mock_create_run.return_value = mock_run

            # Execute streaming and expect error event
            events = []
            async for event in executor.execute_streaming(mock_experience, "user-123", {}, mock_user):
                events.append(event)

            # Should have error event
            error_events = [e for e in events if e.type == "error"]
            assert len(error_events) > 0
            assert "Model configuration validation failed" in str(error_events[0].data["message"])

    @pytest.mark.asyncio
    async def test_model_configuration_validation_inactive_provider(self, executor, mock_user):
        """Test when model configuration has inactive provider during execution."""
        from shu.core.exceptions import ModelConfigurationProviderInactiveError

        executor.model_config_service.validate_model_configuration_for_use.side_effect = (
            ModelConfigurationProviderInactiveError("Test Config", "Test Provider")
        )

        mock_experience = MagicMock()
        mock_experience.id = "exp-123"
        mock_experience.model_configuration_id = "config-123"

        with (
            patch.object(executor, "_create_or_resume_run") as mock_create_run,
            patch.object(executor, "_finalize_run") as mock_finalize,
        ):
            mock_run = MagicMock()
            mock_run.id = "run-123"
            mock_create_run.return_value = mock_run

            # Execute streaming and expect error event
            events = []
            async for event in executor.execute_streaming(mock_experience, "user-123", {}, mock_user):
                events.append(event)

            # Should have error event
            error_events = [e for e in events if e.type == "error"]
            assert len(error_events) > 0
            assert "Model configuration validation failed" in str(error_events[0].data["message"])


class TestModelConfigurationOptimization:
    """Tests for model configuration optimization in synthesis."""

    @pytest.fixture
    def executor(self):
        """Create an executor with mocked dependencies."""
        db = AsyncMock()
        config_manager = MagicMock()
        mock_model_config_service = AsyncMock()
        return ExperienceExecutor(db, config_manager, mock_model_config_service)

    @pytest.fixture
    def mock_user(self):
        """Create a mock user."""
        user = MagicMock()
        user.id = "user-123"
        user.email = "test@example.com"
        return user

    @pytest.fixture
    def mock_experience(self):
        """Create a mock experience with model configuration."""
        experience = MagicMock()
        experience.id = "exp-123"
        experience.model_configuration_id = "config-123"
        experience.inline_prompt_template = None
        experience.prompt = None
        return experience

    @pytest.fixture
    def mock_model_config(self):
        """Create a mock model configuration."""
        config = MagicMock()
        config.id = "config-123"
        config.name = "Test Config"
        config.is_active = True
        config.llm_provider = MagicMock()
        config.llm_provider.name = "OpenAI"
        config.llm_provider.is_active = True
        config.llm_provider_id = "provider-123"
        config.model_name = "gpt-4"
        config.parameter_overrides = {"temperature": 0.7}
        config.prompt = None  # No prompt configured
        return config

    @pytest.mark.asyncio
    async def test_synthesize_with_preloaded_model_config(
        self, executor, mock_user, mock_experience, mock_model_config
    ):
        """Test that _synthesize_with_llm_streaming uses preloaded model config without loading again."""
        context = {"steps": {}}

        # Mock the LLM service and client
        with patch("shu.services.experience_executor.LLMService") as mock_llm_service_class:
            mock_llm_service = AsyncMock()
            mock_llm_service_class.return_value = mock_llm_service

            mock_client = AsyncMock()
            mock_llm_service.get_client.return_value = mock_client

            # Mock the chat completion to return a simple response
            async def mock_chat_completion(**kwargs):
                # Simulate streaming response
                yield MagicMock(type="content_delta", content="Hello")
                yield MagicMock(type="final_message", tokens={"prompt": 10, "completion": 5})

            mock_client.chat_completion.return_value = mock_chat_completion()
            mock_client.close = AsyncMock()

            # Call synthesis with preloaded model config
            results = []
            async for chunk in executor._synthesize_with_llm_streaming(
                mock_experience, context, mock_user, mock_model_config
            ):
                results.append(chunk)

            # Verify that the LLM service was used correctly
            mock_llm_service.get_client.assert_called_once_with("provider-123")
            mock_client.chat_completion.assert_called_once()

            # Verify the results contain expected content
            assert len(results) >= 2  # At least content and metadata
            assert any("Hello" in str(result) for result in results)
            assert any(isinstance(result, dict) and "model" in result for result in results)

    @pytest.mark.asyncio
    async def test_synthesize_without_preloaded_model_config(
        self, executor, mock_user, mock_experience, mock_model_config
    ):
        """Test that _synthesize_with_llm_streaming loads model config when not provided."""
        context = {"steps": {}}

        # Mock the model config service to return our mock config
        executor.model_config_service.validate_model_configuration_for_use.return_value = mock_model_config

        # Mock the LLM service and client
        with patch("shu.services.experience_executor.LLMService") as mock_llm_service_class:
            mock_llm_service = AsyncMock()
            mock_llm_service_class.return_value = mock_llm_service

            mock_client = AsyncMock()
            mock_llm_service.get_client.return_value = mock_client

            # Mock the chat completion to return a simple response
            async def mock_chat_completion(**kwargs):
                yield MagicMock(type="content_delta", content="Hello")
                yield MagicMock(type="final_message", tokens={"prompt": 10, "completion": 5})

            mock_client.chat_completion.return_value = mock_chat_completion()
            mock_client.close = AsyncMock()

            # Call synthesis without preloaded model config
            results = []
            async for chunk in executor._synthesize_with_llm_streaming(mock_experience, context, mock_user):
                results.append(chunk)

            # Verify that validate_model_configuration_for_use WAS called since we didn't pass the config
            executor.model_config_service.validate_model_configuration_for_use.assert_called_once_with(
                "config-123", current_user=mock_user, include_relationships=True
            )

            # Verify that the LLM service was used correctly
            mock_llm_service.get_client.assert_called_once_with("provider-123")
            mock_client.chat_completion.assert_called_once()
