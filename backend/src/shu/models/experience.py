"""Experience models for Shu.

This module defines the Experience abstraction - configurable compositions
of data sources (plugins, knowledge bases), prompts, and LLM synthesis that
deliver specific user-facing outcomes (e.g., Morning Briefing, Inbox Triage).

Design Decision:
- Experience = Steps + Prompt Template + LLM Configuration
- Steps can be plugin calls, KB queries, or future agent network calls
- Experiences can run on triggers (manual, scheduled, cron) or be invoked via API
- Run history is persisted for auditing and cross-run continuity
"""

from datetime import UTC

from sqlalchemy import Boolean, Column, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON, TIMESTAMP
from sqlalchemy.orm import relationship

from shu.core.logging import get_logger

from .base import BaseModel

logger = get_logger(__name__)


class Experience(BaseModel):
    """Configurable composition of data sources, prompts, and LLM synthesis.

    Examples:
    - "Morning Briefing": gmail + calendar + gchat plugins → synthesis prompt
    - "Inbox Triage": gmail plugin → prioritization prompt
    - "Project Pulse": gmail + calendar + drive + jira → project status prompt

    """

    __tablename__ = "experiences"

    # Basic information
    name = Column(String(100), nullable=False, index=True)
    description = Column(Text, nullable=True)

    # Ownership & visibility
    created_by = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    visibility = Column(String(20), default="draft", nullable=False)  # draft, admin_only, published

    # Trigger configuration
    trigger_type = Column(String(20), default="manual", nullable=False)  # manual, scheduled, cron
    trigger_config = Column(JSON, nullable=True)
    # Examples:
    # scheduled: {"time": "08:00", "timezone": "America/Chicago"}
    # cron: {"cron": "0 8 * * 1-5", "timezone": "America/Chicago"}

    # Whether to include previous run output in context (backlink for continuity)
    include_previous_run = Column(Boolean, default=False, nullable=False)

    # LLM Configuration - using Model Configuration instead of direct provider reference
    model_configuration_id = Column(String, ForeignKey("model_configurations.id", ondelete="SET NULL"), nullable=True)

    # Prompt: can reference shared Prompt OR store inline template
    prompt_id = Column(String, ForeignKey("prompts.id", ondelete="SET NULL"), nullable=True)
    inline_prompt_template = Column(Text, nullable=True)  # Jinja2 template

    # Version control (forward-looking)
    version = Column(Integer, default=1, nullable=False)
    is_active_version = Column(Boolean, default=True, nullable=False)
    parent_version_id = Column(String, ForeignKey("experiences.id", ondelete="SET NULL"), nullable=True)

    # Constraints & budgets
    max_run_seconds = Column(Integer, default=120, nullable=False)
    token_budget = Column(Integer, nullable=True)

    # Scheduler fields
    next_run_at = Column(TIMESTAMP(timezone=True), nullable=True, index=True)
    last_run_at = Column(TIMESTAMP(timezone=True), nullable=True)

    # Relationships
    steps = relationship(
        "ExperienceStep",
        back_populates="experience",
        order_by="ExperienceStep.order",
        cascade="all, delete-orphan",
    )
    runs = relationship("ExperienceRun", back_populates="experience", cascade="all, delete-orphan")
    model_configuration = relationship("ModelConfiguration")
    prompt = relationship("Prompt")
    parent_version = relationship("Experience", remote_side="Experience.id")
    creator = relationship("User", foreign_keys=[created_by])

    def schedule_next(self, user_timezone: str | None = None) -> None:
        """Compute and set the next_run_at based on trigger_type and trigger_config.

        Args:
            user_timezone: Optional user timezone from UserPreferences. Used as fallback
                          if trigger_config doesn't specify a timezone override.

        Timezone priority:
        1. trigger_config["timezone"] - per-experience override
        2. user_timezone - from user preferences
        3. "UTC" - default fallback

        Called after each scheduled execution to advance to the next window.
        Manual experiences will have next_run_at = None.

        """
        from datetime import datetime, timedelta

        if self.trigger_type == "manual":
            self.next_run_at = None
            return

        config = self.trigger_config or {}
        now = datetime.now(UTC)

        # Timezone priority: config override > user preference > UTC
        tz_name = config.get("timezone") or user_timezone or "UTC"
        try:
            import zoneinfo

            local_tz = zoneinfo.ZoneInfo(tz_name)
        except Exception:
            logger.warning(
                "Invalid timezone '%s' for experience %s. Falling back to UTC.",
                tz_name,
                self.id or self.name,
            )
            local_tz = UTC

        if self.trigger_type == "scheduled":
            # One-time execution (scheduled_at)
            # We strictly enforce scheduled_at for "scheduled" type now.
            scheduled_at_str = config.get("scheduled_at")
            if scheduled_at_str:
                try:
                    # Parse ISO format (e.g. 2023-10-27T14:30)
                    target = datetime.fromisoformat(scheduled_at_str)
                    if target.tzinfo is None:
                        # Assuming the naive datetime provided is in the user's local time or UTC if unknown
                        # Ideally frontend sends ISO with timezone, but if not we assume local_tz
                        target = target.replace(tzinfo=local_tz)

                    target_utc = target.astimezone(UTC)

                    # If we have already run at or after this time, don't run again
                    # We use a small tolerance (1 second) to handle precision issues
                    if self.last_run_at and self.last_run_at >= (target_utc - timedelta(seconds=1)):
                        self.next_run_at = None
                    else:
                        self.next_run_at = target_utc
                    return
                except Exception as e:
                    logger.error("Invalid scheduled_at format: %s", e)
                    self.next_run_at = None
                    return

            # If no scheduled_at provided, we can't schedule anything
            self.next_run_at = None

        elif self.trigger_type == "cron":
            # Cron expression
            cron_expr = config.get("cron", "0 8 * * *")
            try:
                from croniter import croniter

                local_now = now.astimezone(local_tz)
                cron = croniter(cron_expr, local_now)
                next_local = cron.get_next(datetime)
                self.next_run_at = next_local.astimezone(UTC)
            except Exception as e:
                logger.error("Could not get next run: %s", e)
                # Fallback: schedule for next hour
                self.next_run_at = now + timedelta(hours=1)
        else:
            self.next_run_at = None

    def __repr__(self) -> str:
        """Represent as string."""
        return f"<Experience(id={self.id}, name='{self.name}', visibility='{self.visibility}')>"


class ExperienceStep(BaseModel):
    """Single step in an Experience: plugin call, KB query, or future step types.

    Steps execute sequentially by order. Each step's output is available
    to subsequent steps and the final prompt template via the context:
    - steps.<step_key>.data - the step's output data
    - steps.<step_key>.status - succeeded, failed, skipped
    """

    __tablename__ = "experience_steps"

    experience_id = Column(String, ForeignKey("experiences.id", ondelete="CASCADE"), nullable=False, index=True)
    order = Column(Integer, nullable=False)  # Execution order (0-indexed or 1-indexed)
    step_key = Column(String(50), nullable=False)  # Referenced in templates as steps.<step_key>

    # Step type determines how this step is executed
    step_type = Column(String(30), nullable=False, default="plugin")
    # Values: plugin, knowledge_base, (future: agent_network, function, condition, approval)

    # Plugin configuration (when step_type = "plugin")
    plugin_name = Column(String(100), nullable=True)
    plugin_op = Column(String(100), nullable=True)

    # Knowledge Base configuration (when step_type = "knowledge_base")
    knowledge_base_id = Column(String, ForeignKey("knowledge_bases.id", ondelete="SET NULL"), nullable=True)
    kb_query_template = Column(Text, nullable=True)  # Jinja2 template for query

    # Parameters template: JSON with optional Jinja2 expressions for dynamic values
    # Example: {"max_results": 50, "query": "subject:{{input.project_name}}"}
    # Resolved at runtime with access to: input (user params), steps (previous outputs), now, user
    params_template = Column(JSON, nullable=True)

    # Forward-looking: conditional execution (v1)
    condition_template = Column(Text, nullable=True)  # Jinja2 expression, if false step is skipped

    # Required identity scopes (computed from plugin manifest at runtime, cached here)
    required_scopes = Column(JSON, nullable=True)

    # Relationships
    experience = relationship("Experience", back_populates="steps")
    knowledge_base = relationship("KnowledgeBase")

    def __repr__(self) -> str:
        """Represent as string."""
        return f"<ExperienceStep(id={self.id}, step_key='{self.step_key}', type='{self.step_type}')>"


class ExperienceRun(BaseModel):
    """Execution record of an Experience.

    Stores the complete state of a run including:
    - Input parameters provided at run time
    - Per-step execution state and outputs
    - Final LLM result
    - Backlink to previous run for continuity experiences
    """

    __tablename__ = "experience_runs"

    experience_id = Column(String, ForeignKey("experiences.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)

    # Backlink to previous run (for experience continuity when include_previous_run=True)
    previous_run_id = Column(String, ForeignKey("experience_runs.id", ondelete="SET NULL"), nullable=True)

    # Model configuration used for this run (snapshot at execution time)
    model_configuration_id = Column(String, nullable=True)

    # Status tracking
    status = Column(String(20), default="pending", nullable=False, index=True)
    # Values: pending, running, succeeded, failed, cancelled
    started_at = Column(TIMESTAMP(timezone=True), nullable=True)
    finished_at = Column(TIMESTAMP(timezone=True), nullable=True)

    # Step-by-step execution state
    # Example: {"emails": {"status": "succeeded", "started_at": "...", "finished_at": "..."}}
    step_states = Column(JSON, nullable=True)

    # Inputs & Outputs
    input_params = Column(JSON, nullable=True)  # User-provided parameters at run time
    # Example: {"project_name": "Project Alpha", "days_back": 7}

    step_outputs = Column(JSON, nullable=True)  # {step_key: <output data>}
    # Example: {"emails": {"messages": [...], "count": 5}, "calendar": {"events": [...]}}

    # Final LLM result - stored for reference in future runs
    result_content = Column(Text, nullable=True)  # LLM output text
    result_metadata = Column(JSON, nullable=True)  # Token usage, timing, model info
    # Example: {"model": "gpt-4o", "tokens": {"prompt": 1250, "completion": 480}, "latency_ms": 2340}

    # Error tracking
    error_message = Column(Text, nullable=True)
    error_details = Column(JSON, nullable=True)
    # Example: {"type": "LLMError", "status_code": 429, "retry_after": 60}

    # Relationships
    experience = relationship("Experience", back_populates="runs")
    user = relationship("User", foreign_keys=[user_id])
    previous_run = relationship("ExperienceRun", remote_side="ExperienceRun.id")

    def __repr__(self) -> str:
        """Represent as string."""
        return f"<ExperienceRun(id={self.id}, experience_id='{self.experience_id}', status='{self.status}')>"


# Database indexes for performance
Index("idx_experiences_visibility", Experience.visibility)
Index("idx_experiences_created_by", Experience.created_by)
Index("idx_experiences_active_version", Experience.is_active_version)
Index("idx_experience_steps_experience", ExperienceStep.experience_id)
Index("idx_experience_runs_experience", ExperienceRun.experience_id)
Index("idx_experience_runs_user", ExperienceRun.user_id)
Index("idx_experience_runs_status", ExperienceRun.status)
