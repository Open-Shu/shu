"""Experience Executor for Shu.

This module provides the execution engine for Experiences - runs steps (plugins/KB queries),
builds runtime context, renders Jinja2 templates, calls the LLM for synthesis, and persists run state.

Design follows the proven pattern from MorningBriefingOrchestrator.
"""

from __future__ import annotations

import asyncio
import json
import zoneinfo
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from jinja2 import DebugUndefined, TemplateSyntaxError, UndefinedError
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.models import User
from ..core.config import ConfigurationManager, get_settings_instance
from ..core.exceptions import ModelConfigurationError
from ..core.logging import get_logger
from ..experiences.steps.decision_control import DecisionControlStep
from ..llm.service import LLMService
from ..models.experience import Experience, ExperienceRun, ExperienceStep
from ..models.model_configuration import ModelConfiguration
from ..models.user_preferences import UserPreferences
from ..schemas.query import QueryRequest
from ..services.model_configuration_service import ModelConfigurationService
from ..services.plugin_execution import execute_plugin
from ..services.query_service import QueryService
from ..services.rag_query_processing import execute_rag_queries
from .chat_types import ChatContext

logger = get_logger(__name__)


def _sanitize_for_json(obj: Any) -> Any:
    """Recursively convert datetime objects to ISO strings for JSON serialization."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(item) for item in obj]
    return obj


class ExperienceEventType(str, Enum):
    """Types of events emitted during experience execution."""

    RUN_STARTED = "run_started"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    STEP_SKIPPED = "step_skipped"
    STEP_FAILED = "step_failed"
    SYNTHESIS_STARTED = "synthesis_started"
    CONTENT_DELTA = "content_delta"
    RUN_COMPLETED = "run_completed"
    ERROR = "error"
    FINAL_MESSAGE = "final_message"


@dataclass
class ExperienceEvent:
    """Event emitted during experience execution for SSE streaming."""

    type: ExperienceEventType
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type.value, **self.data}


class ExperienceExecutor:
    """Execute experiences: run steps, render templates, synthesize with LLM.

    Supports both streaming (for manual execution via API) and non-streaming
    (for scheduled execution) modes.
    """

    def __init__(
        self,
        db: AsyncSession,
        config_manager: ConfigurationManager,
        model_config_service: ModelConfigurationService | None = None,
    ) -> None:
        self.db = db
        self.config_manager = config_manager
        self.settings = get_settings_instance()
        self.model_config_service = model_config_service or ModelConfigurationService(db)

        # Create sandboxed Jinja2 environment for template rendering
        # Using DebugUndefined to provide better error messages for missing variables
        self.jinja_env = SandboxedEnvironment(
            autoescape=False,
            undefined=DebugUndefined,
        )

    async def _validate_and_load_model_config(
        self,
        model_configuration_id: str,
        current_user: User,
    ) -> ModelConfiguration | None:
        """Validate and load model configuration for use.

        Args:
            model_configuration_id: ID of the model configuration to load
            current_user: Current user for access validation

        Returns:
            ModelConfiguration if valid, None if validation fails

        Raises:
            Does not raise - returns None and logs errors on failure

        """
        try:
            return await self.model_config_service.validate_model_configuration_for_use(
                model_configuration_id, current_user=current_user, include_relationships=True
            )
        except Exception as e:
            error_message = (
                str(e) if isinstance(e, ModelConfigurationError) else f"Failed to load model configuration: {e!s}"
            )

            logger.error(
                "Model configuration validation failed | config_id=%s user=%s error=%s",
                model_configuration_id,
                current_user.email,
                error_message,
            )
            return None

    async def execute_streaming(
        self,
        experience: Experience,
        user_id: str,
        input_params: dict[str, Any],
        current_user: User,
        run_id: str | None = None,
    ) -> AsyncGenerator[ExperienceEvent, None]:
        """Execute an experience with streaming events for SSE.

        Yields ExperienceEvent objects as execution progresses:
        - run_started, step_started, step_completed/failed/skipped
        - synthesis_started, content_delta (LLM tokens)
        - run_completed or error

        Args:
            run_id: Optional pre-created ExperienceRun ID (e.g., from queue scheduler).
                If provided, the existing run is transitioned to "running" instead of
                creating a new one.

        """
        # Load model configuration if specified
        model_config: ModelConfiguration | None = None
        if experience.model_configuration_id:
            model_config = await self._validate_and_load_model_config(experience.model_configuration_id, current_user)

            if model_config is None:
                # Validation failed - create failed run and return error
                error_message = (
                    f"Model configuration validation failed for config_id={experience.model_configuration_id}"
                )

                run = await self._create_or_resume_run(experience, user_id, input_params, run_id=run_id)
                await self._finalize_run(run, "failed", {}, {}, error_message=error_message, model_config=None)
                yield ExperienceEvent(
                    ExperienceEventType.ERROR,
                    {
                        "message": error_message,
                        "error_type": "ModelConfigurationError",
                        "config_id": experience.model_configuration_id,
                    },
                )
                return

        run = await self._create_or_resume_run(experience, user_id, input_params, run_id=run_id)
        yield ExperienceEvent(ExperienceEventType.RUN_STARTED, {"run_id": run.id, "experience_id": experience.id})

        # Runtime state
        step_outputs: dict[str, Any] = {}
        step_states: dict[str, Any] = {}
        final_content: str = ""
        result_metadata: dict[str, Any] = {}

        try:
            # Enforce max_run_seconds timeout
            timeout_seconds = experience.max_run_seconds or 120
            async with asyncio.timeout(timeout_seconds):
                # Build initial context
                context = await self._build_initial_context(experience, user_id, current_user, input_params)

                # Execute steps
                async for event in self._execute_steps_loop(
                    experience, context, user_id, current_user, step_states, step_outputs
                ):
                    yield event

                # Sanity check: if steps exist but none succeeded, skip LLM synthesis and fail
                if experience.steps and not self._has_successful_steps(step_states):
                    error_msg = "No steps succeeded - skipping LLM synthesis"
                    logger.warning(
                        "Experience execution failed: no successful steps | experience=%s user=%s",
                        experience.id,
                        user_id,
                    )
                    await self._finalize_run(
                        run,
                        "failed",
                        step_states,
                        step_outputs,
                        error_message=error_msg,
                        model_config=model_config,
                    )
                    yield ExperienceEvent(ExperienceEventType.ERROR, {"message": error_msg})
                    return

                # LLM Synthesis
                yield ExperienceEvent(ExperienceEventType.SYNTHESIS_STARTED, {})

                async for chunk in self._synthesize_with_llm_streaming(experience, context, current_user, model_config):
                    if isinstance(chunk, dict):
                        result_metadata = chunk
                    else:
                        final_content += chunk
                        yield ExperienceEvent(ExperienceEventType.CONTENT_DELTA, {"content": chunk})

                # Finalize
                await self._finalize_run(
                    run,
                    "succeeded",
                    step_states,
                    step_outputs,
                    result_content=final_content,
                    result_metadata=result_metadata,
                    model_config=model_config,
                )
                yield ExperienceEvent(
                    ExperienceEventType.RUN_COMPLETED,
                    {"run_id": run.id, "result_content": final_content},
                )

        except TimeoutError:
            error_msg = f"Experience execution timed out after {timeout_seconds}s"
            await self._finalize_run(
                run,
                "failed",
                step_states,
                step_outputs,
                error_message=error_msg,
                model_config=model_config,
            )
            yield ExperienceEvent(ExperienceEventType.ERROR, {"message": error_msg})

        except Exception as e:
            error_msg = f"Experience execution failed: {e!s}"
            logger.exception("Experience execution failed", extra={"experience_id": experience.id})
            await self._finalize_run(
                run,
                "failed",
                step_states,
                step_outputs,
                error_message=error_msg,
                model_config=model_config,
            )
            yield ExperienceEvent(ExperienceEventType.ERROR, {"message": error_msg})

    async def execute(
        self,
        experience: Experience,
        user_id: str,
        input_params: dict[str, Any],
        current_user: User,
        run_id: str | None = None,
    ) -> ExperienceRun:
        """Execute an experience without streaming (for scheduled execution).

        Args:
            run_id: Optional pre-created ExperienceRun ID (e.g., from queue scheduler).

        """
        run: ExperienceRun | None = None

        # Consume events - timeout is enforced within execute_streaming
        async for event in self.execute_streaming(experience, user_id, input_params, current_user, run_id=run_id):
        async for event in self.execute_streaming(experience, user_id, input_params, current_user, run_id=run_id):
            if event.type == ExperienceEventType.RUN_STARTED:
                run_id = event.data.get("run_id")
                if run_id:
                    result = await self.db.execute(select(ExperienceRun).where(ExperienceRun.id == run_id))
                    run = result.scalars().first()
            elif event.type in (ExperienceEventType.RUN_COMPLETED, ExperienceEventType.ERROR):
                break

        if run:
            await self.db.refresh(run)
            return run

        raise RuntimeError("Failed to create experience run")

    async def _execute_steps_loop(
        self,
        experience: Experience,
        context: dict[str, Any],
        user_id: str,
        current_user: User,
        step_states: dict[str, Any],
        step_outputs: dict[str, Any],
    ) -> AsyncGenerator[ExperienceEvent, None]:
        """Iterate and execute all experience steps."""
        for step in experience.steps:
            step_start = datetime.now(UTC)
            yield ExperienceEvent(
                ExperienceEventType.STEP_STARTED,
                {"step_key": step.step_key, "step_type": step.step_type},
            )

            # Check condition
            should_run, skip_reason = self._check_should_run_step(step, context)
            if not should_run:
                step_states[step.step_key] = {
                    "status": "skipped",
                    "reason": skip_reason,
                    "started_at": step_start.isoformat(),
                    "finished_at": datetime.now(UTC).isoformat(),
                }
                yield ExperienceEvent(
                    ExperienceEventType.STEP_SKIPPED,
                    {"step_key": step.step_key, "reason": skip_reason},
                )
                continue

            try:
                output = await self._execute_step(step, context, user_id, current_user)
                step_end = datetime.now(UTC)

                # Update context
                step_outputs[step.step_key] = output
                context["steps"][step.step_key] = {"data": output, "status": "succeeded"}

                step_states[step.step_key] = {
                    "status": "succeeded",
                    "started_at": step_start.isoformat(),
                    "finished_at": step_end.isoformat(),
                }

                yield ExperienceEvent(
                    ExperienceEventType.STEP_COMPLETED,
                    {
                        "step_key": step.step_key,
                        "summary": self._build_step_summary(step, output),
                        "data": output,  # Include the actual step output data
                    },
                )

            except Exception as e:
                step_end = datetime.now(UTC)
                error_msg = str(e)
                logger.exception(
                    "Experience step failed",
                    extra={
                        "step_key": step.step_key,
                        "experience_id": experience.id,
                        "error": error_msg,
                    },
                )

                # Store failed state but continue (graceful degradation)
                context["steps"][step.step_key] = {
                    "data": None,
                    "status": "failed",
                    "error": error_msg,
                }
                step_states[step.step_key] = {
                    "status": "failed",
                    "error": error_msg,
                    "started_at": step_start.isoformat(),
                    "finished_at": step_end.isoformat(),
                }

    def _check_should_run_step(self, step: ExperienceStep, context: dict[str, Any]) -> tuple[bool, str | None]:
        """Determine if a step should run based on its condition.

        The condition can be:
        1. A Jinja2 template that evaluates to a boolean (e.g., "{{ decision.should_execute }}")
        2. Empty/None - step always runs

        Args:
            step: The experience step to check
            context: The current execution context

        Returns:
            Tuple of (should_run, skip_reason)

        """
        if not step.condition_template:
            return True, None

        # Render the condition template
        rendered_condition = self._render_template(step.condition_template, context)

        # Evaluate the rendered condition as a boolean
        # Handle common boolean representations
        condition_lower = rendered_condition.strip().lower()

        if condition_lower in ("true", "1", "yes"):
            return True, None
        if condition_lower in ("false", "0", "no", "none", ""):
            return False, f"Condition '{step.condition_template}' evaluated to false"
        # If it's not a clear boolean, log a warning and default to False for safety
        logger.warning(
            f"Step condition '{step.condition_template}' evaluated to ambiguous value: '{rendered_condition}'. "
            f"Treating as False. Expected 'true' or 'false'."
        )
        return False, f"Condition '{step.condition_template}' evaluated to ambiguous value: '{rendered_condition}'"

    def _has_successful_steps(self, step_states: dict[str, Any]) -> bool:
        """Check if at least one step succeeded."""
        return any(state.get("status") == "succeeded" for state in step_states.values())

    async def _create_or_resume_run(
        self,
        experience: Experience,
        user_id: str,
        input_params: dict[str, Any],
        run_id: str | None = None,
    ) -> ExperienceRun:
        """Create a new ExperienceRun or resume a pre-created queued run.

        If run_id is provided, loads the existing run and transitions it to
        "running" only if the current status allows it (queued or pending).
        Otherwise creates a new run (for on-demand execution).

        Raises:
            ValueError: If the run exists but is in a terminal or already-running
                state that cannot transition to "running".

        """
        if run_id:
            result = await self.db.execute(select(ExperienceRun).where(ExperienceRun.id == run_id))
            run = result.scalar_one_or_none()
            if run:
                # Ownership validation: ensure the run belongs to this experience and user
                if run.experience_id != str(experience.id):
                    raise ValueError(f"Run {run_id} belongs to experience '{run.experience_id}', not '{experience.id}'")
                if run.user_id != str(user_id):
                    raise PermissionError(f"Run {run_id} belongs to a different user")

                # Only allow transition from queued/pending → running
                allowed_statuses = {"queued", "pending"}
                if run.status not in allowed_statuses:
                    raise ValueError(
                        f"Cannot resume run {run_id}: status is '{run.status}', expected one of {allowed_statuses}"
                    )

                # Refresh from authoritative source so stale queue-time values are overwritten
                run.input_params = input_params
                run.model_configuration_id = experience.model_configuration_id
                run.status = "running"
                run.started_at = datetime.now(UTC)
                await self.db.commit()
                await self.db.refresh(run)
                return run
            # If run not found, fall through and create a new one
            logger.warning(
                "Pre-created run not found, creating new run",
                extra={"run_id": run_id, "experience_id": experience.id},
            )

        run = ExperienceRun(
            experience_id=experience.id,
            user_id=user_id,
            model_configuration_id=experience.model_configuration_id,
            status="running",
            started_at=datetime.now(UTC),
            input_params=input_params,
            step_states={},
            step_outputs={},
            result_metadata={},
        )
        self.db.add(run)
        await self.db.commit()
        await self.db.refresh(run)
        return run

    async def _finalize_run(
        self,
        run: ExperienceRun,
        status: str,
        step_states: dict[str, Any],
        step_outputs: dict[str, Any],
        result_content: str | None = None,
        result_metadata: dict[str, Any] | None = None,
        error_message: str | None = None,
        model_config: ModelConfiguration | None = None,
    ) -> None:
        """Update run with final state including model configuration snapshot."""
        run.status = status
        run.finished_at = datetime.now(UTC)
        # Sanitize JSON fields to ensure all datetime objects are converted to strings
        run.step_states = _sanitize_for_json(step_states)
        run.step_outputs = _sanitize_for_json(step_outputs)

        if result_content:
            run.result_content = result_content

        if error_message:
            run.error_message = error_message

        # Build comprehensive result metadata including model config snapshot
        final_metadata = result_metadata or {}

        if model_config:
            final_metadata["model_configuration"] = {
                "id": model_config.id,
                "name": model_config.name,
                "description": model_config.description,
                "provider_id": model_config.llm_provider_id,
                "provider_name": model_config.llm_provider.name if model_config.llm_provider else None,
                "model_name": model_config.model_name,
                "parameter_overrides": model_config.parameter_overrides,
            }

        run.result_metadata = _sanitize_for_json(final_metadata)
        await self.db.commit()

    async def _get_previous_run(
        self,
        experience_id: str,
        user_id: str,
    ) -> ExperienceRun | None:
        """Get the most recent successful run for backlink."""
        result = await self.db.execute(
            select(ExperienceRun)
            .where(
                ExperienceRun.experience_id == experience_id,
                ExperienceRun.user_id == user_id,
                ExperienceRun.status == "succeeded",
            )
            .order_by(ExperienceRun.finished_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def _get_user_formatted_datetime(self, user_id: str) -> str:
        """Get current datetime formatted in user's timezone with weekday.

        Returns a human-readable format like: "Monday, January 15, 2024 at 2:30 PM PST"
        Falls back to UTC if user timezone is not available or invalid.
        """
        now_utc = datetime.now(UTC)

        # Try to get user's timezone preference
        user_tz_str = "UTC"  # Default fallback
        try:
            result = await self.db.execute(select(UserPreferences.timezone).where(UserPreferences.user_id == user_id))
            user_timezone = result.scalar_one_or_none()
            if user_timezone:
                user_tz_str = user_timezone
        except Exception as e:
            logger.warning("Failed to get user timezone preference: %s", e)

        # Convert to user's timezone
        try:
            user_tz = zoneinfo.ZoneInfo(user_tz_str)
            now_local = now_utc.astimezone(user_tz)
        except Exception as e:
            logger.warning("Invalid timezone '%s', falling back to UTC: %s", user_tz_str, e)
            now_local = now_utc
            user_tz_str = "UTC"

        # Format with weekday and timezone abbreviation
        # Example: "Monday, January 15, 2024 at 2:30 PM PST"
        formatted = now_local.strftime("%A, %B %d, %Y at %I:%M %p %Z")

        # If %Z doesn't give us a nice abbreviation, append the timezone name
        if not formatted.split()[-1] or formatted.split()[-1] == now_local.strftime("%z"):
            formatted = now_local.strftime("%A, %B %d, %Y at %I:%M %p") + f" ({user_tz_str})"

        return formatted

    async def _build_initial_context(
        self,
        experience: Experience,
        user_id: str,
        current_user: User,
        input_params: dict[str, Any],
    ) -> dict[str, Any]:
        """Build initial Jinja2 template context."""
        # Get previous run if needed
        previous_run: ExperienceRun | None = None
        if experience.include_previous_run:
            previous_run = await self._get_previous_run(experience.id, user_id)

        formatted_now = await self._get_user_formatted_datetime(user_id)

        context = {
            "user": {
                "id": str(current_user.id),
                "email": current_user.email,
                "display_name": getattr(current_user, "display_name", None) or current_user.email,
            },
            "input": input_params or {},
            "steps": {},  # Starts empty
            "previous_run": None,
            "now": formatted_now,
        }

        if previous_run:
            context["previous_run"] = {
                "result_content": previous_run.result_content,
                "step_outputs": previous_run.step_outputs,
                "finished_at": previous_run.finished_at,
            }

        return context

    def _render_template(self, template: str, context: dict[str, Any]) -> str:
        """Safely render a Jinja2 template."""
        try:
            tmpl = self.jinja_env.from_string(template)
            return tmpl.render(**context)
        except (TemplateSyntaxError, UndefinedError) as e:
            logger.warning("Template rendering failed: %s", e)
            return template

    def _render_params(self, params_template: dict | None, context: dict) -> dict[str, Any]:
        """Render Jinja2 expressions in params_template values."""
        if not params_template:
            return {}

        rendered = {}
        for key, value in params_template.items():
            if isinstance(value, str) and "{{" in value:
                rendered[key] = self._render_template(value, context)
            else:
                rendered[key] = value
        return rendered

    async def _execute_step(
        self,
        step: ExperienceStep,
        context: dict[str, Any],
        user_id: str,
        current_user: User,
    ) -> dict[str, Any]:
        """Execute a single step (plugin, KB, or decision_control)."""
        if step.step_type == "plugin":
            return await self._execute_plugin_step(step, context, user_id)
        if step.step_type == "knowledge_base":
            return await self._execute_kb_step(step, context, current_user)
        if step.step_type == "decision_control":
            return await self._execute_decision_control_step(step, context)
        raise ValueError(f"Unknown step type: {step.step_type}")

    async def _execute_plugin_step(
        self,
        step: ExperienceStep,
        context: dict[str, Any],
        user_id: str,
    ) -> dict[str, Any]:
        """Execute plugin reusing the shared service logic."""
        plugin_name = step.plugin_name
        if not plugin_name:
            raise ValueError(f"Step {step.step_key} missing plugin_name")

        params = self._render_params(step.params_template, context)

        # Note: execute_plugin expects the operation argument explicitly
        op = step.plugin_op

        exec_result = await execute_plugin(self.db, plugin_name, op, params, user_id)

        logger.info(
            "Plugin execution result | plugin=%s op=%s status=%s has_data=%s",
            plugin_name,
            op,
            exec_result.get("status"),
            exec_result.get("data") is not None,
        )

        if exec_result.get("status") != "success":
            error = exec_result.get("error")
            msg = error.get("message") if isinstance(error, dict) else str(error)
            raise ValueError(msg or "Plugin execution failed")

        return exec_result.get("data") or {}

    async def _execute_kb_step(
        self,
        step: ExperienceStep,
        context: dict[str, Any],
        current_user: User,
    ) -> dict[str, Any]:
        """Execute KB query using shared RAG processing logic."""
        kb_id = step.knowledge_base_id
        if not kb_id:
            raise ValueError(f"Step {step.step_key} missing knowledge_base_id")

        query_text = ""
        if step.kb_query_template:
            query_text = self._render_template(step.kb_query_template, context)
        else:
            query_text = context.get("input", {}).get("query", "")

        if not query_text:
            raise ValueError(f"Step {step.step_key} has no query text")

        def _builder(kb_id, rag_config, query):
            return QueryRequest(
                query=query,
                query_type=rag_config.get("search_type", "hybrid"),
                limit=rag_config.get("max_results", 10),
                similarity_threshold=rag_config.get("search_threshold", 0.7),
                include_metadata=True,
            )

        query_service = QueryService(self.db, self.config_manager)
        _, _, responses = await execute_rag_queries(
            self.db, self.config_manager, query_service, current_user, query_text, [kb_id], _builder
        )

        if not responses:
            return {"results": []}

        # Return the first response payload (since we only queried one KB)
        return responses[0]["response"]

    async def _execute_decision_control_step(
        self,
        step: ExperienceStep,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute decision control step.

        Args:
            step: Experience step with decision control configuration
            context: Workflow context with player data

        Returns:
            Decision result with should_execute, rationale, and metadata

        """
        # Get decision control configuration from params_template
        config = {}
        if step.params_template:
            config = self._render_params(step.params_template, context)

        # Create decision control step instance
        decision_step = DecisionControlStep()

        # Execute decision logic
        # Note: host parameter is optional, we pass None for now
        # In the future, we could pass a host object with audit capabilities
        result = await decision_step.execute(step.step_key, config, context, host=None)

        # Guard against None return (should not happen with current implementation, but defensive)
        if result is None:
            logger.warning("Decision control step '%s' returned None, using safe default", step.step_key)
            result = {"should_execute": False, "rationale": "No decision returned", "confidence": 0.0, "metadata": {}}

        logger.info(
            "Decision control step '%s' executed: should_execute=%s, rationale=%s",
            step.step_key,
            result.get("should_execute"),
            result.get("rationale"),
        )

        return result

    async def _synthesize_with_llm_streaming(
        self,
        experience: Experience,
        context: dict[str, Any],
        current_user: User,
        model_config: ModelConfiguration | None = None,
    ) -> AsyncGenerator[Any, None]:
        """Render prompt and stream LLM synthesis using model configuration.

        Implements prompt resolution priority: inline > experience > model config
        Applies parameter overrides from model configuration.

        Args:
            experience: Experience being executed
            context: Template context with step outputs
            current_user: Current user for access validation
            model_config: Pre-loaded model configuration (optional, will load if not provided)

        """
        if not experience.model_configuration_id:
            # No LLM configured - return default prompt
            yield self._build_default_prompt(context)
            yield {"model": None, "tokens": {}}
            return

        # Use provided model config or load it if not provided
        if model_config is None:
            model_config = await self._validate_and_load_model_config(experience.model_configuration_id, current_user)

            if model_config is None:
                # Model configuration validation failed during synthesis
                error_message = (
                    f"Model configuration validation failed for config_id={experience.model_configuration_id}"
                )

                logger.error(
                    "Model configuration validation failed during synthesis | experience=%s config_id=%s",
                    experience.id,
                    experience.model_configuration_id,
                )

                # Return default prompt and indicate error in metadata
                yield self._build_default_prompt(context)
                yield {
                    "model": None,
                    "tokens": {},
                    "error": error_message,
                    "error_type": "ModelConfigurationError",
                    "model_configuration_id": experience.model_configuration_id,
                }
                return

        # Determine system prompt using priority: inline > experience prompt > model config prompt
        system_prompt_content = ""
        prompt_source = "none"

        if experience.inline_prompt_template:
            system_prompt_content = self._render_template(experience.inline_prompt_template, context)
            prompt_source = "inline"
        elif experience.prompt:
            system_prompt_content = self._render_template(experience.prompt.content, context)
            prompt_source = "experience"
        elif model_config.prompt:
            system_prompt_content = self._render_template(model_config.prompt.content, context)
            prompt_source = "model_config"

        # Build user message from step outputs
        user_content = self._build_default_prompt(context)

        # Get LLM client using model configuration
        llm_service = LLMService(self.db)
        client = await llm_service.get_client(model_config.llm_provider_id)

        try:
            # Build messages for LLM
            messages = ChatContext.from_dicts(
                [{"role": "user", "content": user_content}], system_prompt=system_prompt_content
            )

            # Apply parameter overrides from model configuration
            model_overrides = {}

            # Apply parameter overrides if they exist
            if model_config.parameter_overrides:
                logger.debug(
                    "Applying parameter overrides from model configuration | config=%s overrides=%s",
                    model_config.name,
                    model_config.parameter_overrides,
                )
                model_overrides.update(model_config.parameter_overrides)

            logger.debug(
                "Starting LLM synthesis | experience=%s model_config=%s provider=%s model=%s prompt_source=%s",
                experience.id,
                model_config.name,
                model_config.llm_provider.name,
                model_config.model_name,
                prompt_source,
            )

            # Stream LLM response
            stream_gen = await client.chat_completion(
                messages=messages,
                model=model_config.model_name,
                stream=True,
                model_overrides=model_overrides,
            )

            async for event in stream_gen:
                if event.type == "content_delta":
                    yield event.content
                elif event.type == "final_message":
                    # Build comprehensive metadata including model configuration details
                    yield {
                        "model": model_config.model_name,
                        "provider_id": model_config.llm_provider_id,
                        "provider_name": model_config.llm_provider.name,
                        "model_configuration_id": model_config.id,
                        "model_configuration_name": model_config.name,
                        "prompt_source": prompt_source,
                        "system_prompt_content": system_prompt_content,
                        "user_content": user_content,
                        "parameter_overrides": model_config.parameter_overrides,
                        "tokens": getattr(event, "tokens", {}),
                    }

        except Exception as e:
            logger.exception(
                "LLM synthesis failed | experience=%s model_config=%s error=%s",
                experience.id,
                model_config.name,
                str(e),
            )
            # Return default prompt and error metadata
            yield self._build_default_prompt(context)
            yield {
                "model": model_config.model_name,
                "provider_id": model_config.llm_provider_id,
                "model_configuration_id": model_config.id,
                "model_configuration_name": model_config.name,
                "error": str(e),
                "tokens": {},
            }
        finally:
            try:
                await client.close()
            except Exception:
                pass

    def _build_default_prompt(self, context: dict[str, Any]) -> str:
        """Build a default synthesis prompt from step outputs."""
        steps = context.get("steps", {})

        # If there are no steps in this experience, we assume the system prompt contains all the info.
        if not steps:
            return "Complete your assignment."

        parts = ["Based on the following data, provide a summary:\n"]
        for step_key, step_data in steps.items():
            if step_data.get("status") == "succeeded" and step_data.get("data"):
                parts.append(f"\n## {step_key}\n")
                data = step_data.get("data", {})
                # Try to format data nicely
                if isinstance(data, dict):
                    parts.append(json.dumps(data, indent=2, default=str))
                else:
                    parts.append(str(data))

        return "\n".join(parts)

    def _build_step_summary(self, step: ExperienceStep, output: dict[str, Any]) -> str:
        """Build a human-readable summary for a step's output."""
        if step.step_type == "plugin":
            # Try to extract count or summary from output
            if isinstance(output, dict):
                count = output.get("count")
                if count is not None:
                    return f"Retrieved {count} items"
                # Check for common list patterns
                for key in ["messages", "events", "items", "results"]:
                    if key in output and isinstance(output[key], list):
                        return f"Retrieved {len(output[key])} {key}"
            return f"Plugin {step.plugin_name} completed"

        if step.step_type == "knowledge_base":
            if isinstance(output, dict):
                results = output.get("results", [])
                return f"Found {len(results)} KB results"
            return "KB query completed"

        return f"Step {step.step_key} completed"
