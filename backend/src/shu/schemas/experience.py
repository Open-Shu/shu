"""Experience schemas for Shu.

This module defines Pydantic schemas for the Experience Platform entities:
- Experience: configurable compositions of data sources, prompts, and LLM synthesis
- ExperienceStep: individual steps (plugin calls, KB queries)
- ExperienceRun: execution history records
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ============================================================================
# Enums
# ============================================================================


class ExperienceVisibility(str, Enum):
    """Visibility levels for experiences."""

    DRAFT = "draft"
    ADMIN_ONLY = "admin_only"
    PUBLISHED = "published"


class TriggerType(str, Enum):
    """Trigger types for experience execution."""

    MANUAL = "manual"
    SCHEDULED = "scheduled"
    CRON = "cron"


class StepType(str, Enum):
    """Types of experience steps."""

    PLUGIN = "plugin"
    KNOWLEDGE_BASE = "knowledge_base"
    # Future: AGENT_NETWORK = "agent_network"


class RunStatus(str, Enum):
    """Status values for experience runs."""

    QUEUED = "queued"
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ============================================================================
# ExperienceStep Schemas
# ============================================================================


class ExperienceStepBase(BaseModel):
    """Base schema for experience steps."""

    model_config = ConfigDict(protected_namespaces=())

    step_key: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Key to reference this step in templates (e.g., 'emails')",
    )
    step_type: StepType = Field(default=StepType.PLUGIN, description="Type of step")
    order: int = Field(..., ge=0, description="Execution order (0-indexed)")

    # Plugin configuration
    plugin_name: str | None = Field(None, max_length=100, description="Plugin name (when step_type=plugin)")
    plugin_op: str | None = Field(None, max_length=100, description="Plugin operation (when step_type=plugin)")

    # KB configuration
    knowledge_base_id: str | None = Field(None, description="Knowledge base ID (when step_type=knowledge_base)")
    kb_query_template: str | None = Field(None, description="Jinja2 query template (when step_type=knowledge_base)")

    # Parameters template
    params_template: dict[str, Any] | None = Field(None, description="Parameters with optional Jinja2 expressions")

    # Conditional execution
    condition_template: str | None = Field(None, description="Jinja2 condition for skipping step")

    @field_validator("step_key")
    @classmethod
    def validate_step_key(cls, v: str) -> str:
        """Validate step key format."""
        v = v.strip()
        if not v:
            raise ValueError("Step key cannot be empty")
        # Ensure it's a valid identifier for template access
        if not v.isidentifier():
            raise ValueError(
                f"Step key '{v}' must be a valid Python/Jinja2 identifier (start with letter/underscore, contain only alphanumerics/underscore)"
            )
        return v


class ExperienceStepCreate(ExperienceStepBase):
    """Schema for creating experience steps (used within experience creation)."""

    pass


class ExperienceStepUpdate(BaseModel):
    """Schema for updating experience steps."""

    model_config = ConfigDict(protected_namespaces=())

    step_key: str | None = Field(None, min_length=1, max_length=50)
    step_type: StepType | None = None
    order: int | None = Field(None, ge=0)
    plugin_name: str | None = Field(None, max_length=100)
    plugin_op: str | None = Field(None, max_length=100)
    knowledge_base_id: str | None = None
    kb_query_template: str | None = None
    params_template: dict[str, Any] | None = None
    condition_template: str | None = None


class ExperienceStepResponse(ExperienceStepBase):
    """Schema for experience step responses."""

    id: str
    experience_id: str
    required_scopes: list[str] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Experience Schemas
# ============================================================================


class ExperienceBase(BaseModel):
    """Base schema for experiences."""

    model_config = ConfigDict(protected_namespaces=())

    name: str = Field(..., min_length=1, max_length=100, description="Experience name")
    description: str | None = Field(None, description="User-friendly description")
    visibility: ExperienceVisibility = Field(default=ExperienceVisibility.DRAFT, description="Visibility level")

    # Trigger configuration
    trigger_type: TriggerType = Field(default=TriggerType.MANUAL, description="How the experience is triggered")
    trigger_config: dict[str, Any] | None = Field(None, description="Trigger-specific configuration")

    # Continuity
    include_previous_run: bool = Field(default=False, description="Include previous run output in context")

    # LLM configuration
    model_configuration_id: str | None = Field(None, description="Model configuration to use for LLM synthesis")

    # Prompt configuration
    prompt_id: str | None = Field(None, description="Reference to shared prompt")
    inline_prompt_template: str | None = Field(None, description="Jinja2 prompt template")

    # Constraints
    max_run_seconds: int = Field(default=120, ge=1, le=600, description="Maximum run duration")
    token_budget: int | None = Field(None, ge=0, description="Token budget limit")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate experience name."""
        if not v.strip():
            raise ValueError("Experience name cannot be empty")
        return v.strip()


class ExperienceCreate(ExperienceBase):
    """Schema for creating experiences."""

    steps: list[ExperienceStepCreate] = Field(default_factory=list, description="Experience steps")


class ExperienceUpdate(BaseModel):
    """Schema for updating experiences."""

    model_config = ConfigDict(protected_namespaces=())

    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    visibility: ExperienceVisibility | None = None
    trigger_type: TriggerType | None = None
    trigger_config: dict[str, Any] | None = None
    include_previous_run: bool | None = None
    model_configuration_id: str | None = None
    prompt_id: str | None = None
    inline_prompt_template: str | None = None
    max_run_seconds: int | None = Field(None, ge=1, le=600)
    token_budget: int | None = Field(None, ge=0)

    # Steps can be replaced entirely
    steps: list[ExperienceStepCreate] | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate experience name."""
        if v is not None and not v.strip():
            raise ValueError("Experience name cannot be empty")
        return v.strip() if v else v


class ExperienceResponse(ExperienceBase):
    """Schema for experience responses."""

    id: str
    created_by: str
    created_at: datetime
    updated_at: datetime

    # Version info
    version: int
    is_active_version: bool
    parent_version_id: str | None = None

    # Expanded relationships
    steps: list[ExperienceStepResponse] = Field(default_factory=list)
    model_configuration: dict[str, Any] | None = Field(
        None, description="Model configuration details when include_relationships=True"
    )
    prompt: dict[str, Any] | None = None

    # Computed
    step_count: int = Field(default=0, description="Number of steps")
    last_run_at: datetime | None = Field(None, description="When experience was last run")

    model_config = ConfigDict(from_attributes=True)


class ExperienceList(BaseModel):
    """Schema for paginated experience lists."""

    items: list[ExperienceResponse]
    total: int
    page: int = 1
    per_page: int = 50
    pages: int

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# ExperienceRun Schemas
# ============================================================================


class ExperienceRunRequest(BaseModel):
    """Schema for requesting an experience run."""

    model_config = ConfigDict(protected_namespaces=())

    input_params: dict[str, Any] | None = Field(None, description="User-provided parameters")


class ExperienceRunResponse(BaseModel):
    """Schema for experience run responses."""

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: str
    experience_id: str
    user_id: str
    previous_run_id: str | None = None

    # Model configuration used for this run (snapshot at execution time)
    model_configuration_id: str | None = Field(None, description="Model configuration ID used for this run")

    # Status
    status: RunStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None

    # Data
    input_params: dict[str, Any] | None = None
    step_states: dict[str, Any] | None = None
    step_outputs: dict[str, Any] | None = None
    result_content: str | None = None
    result_metadata: dict[str, Any] | None = Field(
        None, description="Includes model configuration snapshot and execution metadata"
    )

    # Error
    error_message: str | None = None
    error_details: dict[str, Any] | None = None

    created_at: datetime
    updated_at: datetime

    # User info (populated when listing runs)
    user: dict[str, Any] | None = Field(None, description="User info (id, email)")

    # Computed
    duration_seconds: float | None = Field(None, description="Run duration in seconds")


class ExperienceRunList(BaseModel):
    """Schema for paginated run lists."""

    items: list[ExperienceRunResponse]
    total: int
    page: int = 1
    per_page: int = 50
    pages: int

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# User Dashboard Schemas
# ============================================================================


class ExperienceResultSummary(BaseModel):
    """Schema for user dashboard - summary of latest experience result."""

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    experience_id: str
    experience_name: str
    experience_description: str | None = None

    # Latest run info
    latest_run_id: str | None = None
    latest_run_status: RunStatus | None = None
    latest_run_finished_at: datetime | None = None
    result_preview: str | None = Field(None, description="Truncated result content")

    # User can run?
    can_run: bool = Field(default=True, description="Whether user has required identities")
    missing_identities: list[str] = Field(default_factory=list)


class UserExperienceResults(BaseModel):
    """Schema for user's experience results dashboard."""

    experiences: list[ExperienceResultSummary]
    total: int
