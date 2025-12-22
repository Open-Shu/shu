"""
Experience models for Shu.

This module defines the Experience abstraction - configurable compositions
of data sources (plugins, knowledge bases), prompts, and LLM synthesis that
deliver specific user-facing outcomes (e.g., Morning Briefing, Inbox Triage).

Design Decision:
- Experience = Steps + Prompt Template + LLM Configuration
- Steps can be plugin calls, KB queries, or future agent network calls
- Experiences can run on triggers (manual, scheduled, cron) or be invoked via API
- Run history is persisted for auditing and cross-run continuity
"""

from sqlalchemy import Column, String, Text, Boolean, Integer, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSON, TIMESTAMP
from sqlalchemy.orm import relationship

from .base import BaseModel


class Experience(BaseModel):
    """
    Configurable composition of data sources, prompts, and LLM synthesis.

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

    # LLM Configuration
    llm_provider_id = Column(String, ForeignKey("llm_providers.id", ondelete="SET NULL"), nullable=True)
    model_name = Column(String(100), nullable=True)

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

    # Relationships
    steps = relationship(
        "ExperienceStep",
        back_populates="experience",
        order_by="ExperienceStep.order",
        cascade="all, delete-orphan"
    )
    runs = relationship("ExperienceRun", back_populates="experience", cascade="all, delete-orphan")
    llm_provider = relationship("LLMProvider")
    prompt = relationship("Prompt")
    parent_version = relationship("Experience", remote_side="Experience.id")
    creator = relationship("User", foreign_keys=[created_by])

    def __repr__(self) -> str:
        return f"<Experience(id={self.id}, name='{self.name}', visibility='{self.visibility}')>"


class ExperienceStep(BaseModel):
    """
    Single step in an Experience: plugin call, KB query, or future step types.

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
        return f"<ExperienceStep(id={self.id}, step_key='{self.step_key}', type='{self.step_type}')>"


class ExperienceRun(BaseModel):
    """
    Execution record of an Experience.

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

    # Model used for this run (snapshot at execution time)
    model_provider_id = Column(String, nullable=True)
    model_name = Column(String(100), nullable=True)

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
        return f"<ExperienceRun(id={self.id}, experience_id='{self.experience_id}', status='{self.status}')>"


# Database indexes for performance
Index('idx_experiences_visibility', Experience.visibility)
Index('idx_experiences_created_by', Experience.created_by)
Index('idx_experiences_active_version', Experience.is_active_version)
Index('idx_experience_steps_experience', ExperienceStep.experience_id)
Index('idx_experience_runs_experience', ExperienceRun.experience_id)
Index('idx_experience_runs_user', ExperienceRun.user_id)
Index('idx_experience_runs_status', ExperienceRun.status)
