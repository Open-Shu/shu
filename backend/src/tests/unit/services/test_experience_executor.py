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

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any, Dict

from shu.services.experience_executor import ExperienceExecutor, ExperienceEvent, ExperienceEventType


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
        assert isinstance(context["now"], datetime)
    
    @pytest.mark.asyncio
    async def test_build_context_with_previous_run(self, executor, mock_user):
        """Test context includes previous run data."""
        experience = MagicMock()
        experience.include_previous_run = True
        
        previous_run = MagicMock()
        previous_run.result_content = "Previous summary"
        previous_run.step_outputs = {"old_step": {"data": 123}}
        previous_run.finished_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        
        executor._get_previous_run = AsyncMock(return_value=previous_run)
        
        context = await executor._build_initial_context(
            experience=experience,
            user_id="user-123",
            current_user=mock_user,
            input_params={},
        )
        
        assert context["previous_run"]["result_content"] == "Previous summary"
        assert context["previous_run"]["step_outputs"] == {"old_step": {"data": 123}}


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
        result = executor._render_template(
            "You have {{ steps.emails.data.count }} emails.",
            context
        )
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
                [
                    {
                        "knowledge_base_id": "kb-123",
                        "response": {"results": [{"id": 1}]}
                    }
                ]
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
        experience.llm_provider_id = "provider-1"
        experience.model_name = "gpt-4"
        
        executor.db.add = MagicMock()
        executor.db.commit = AsyncMock()
        executor.db.refresh = AsyncMock()
        
        run = await executor._create_run(experience, "user-123", {"query": "test"})
        
        executor.db.add.assert_called_once()
        executor.db.commit.assert_called_once()
    
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
