"""
Experience schemas for Shu.

This module defines Pydantic schemas for the Experience Platform entities:
- Experience: configurable compositions of data sources, prompts, and LLM synthesis
- ExperienceStep: individual steps (plugin calls, KB queries)
- ExperienceRun: execution history records
"""

from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum


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

    step_key: str = Field(..., min_length=1, max_length=50, description="Key to reference this step in templates (e.g., 'emails')")
    step_type: StepType = Field(default=StepType.PLUGIN, description="Type of step")
    order: int = Field(..., ge=0, description="Execution order (0-indexed)")

    # Plugin configuration
    plugin_name: Optional[str] = Field(None, max_length=100, description="Plugin name (when step_type=plugin)")
    plugin_op: Optional[str] = Field(None, max_length=100, description="Plugin operation (when step_type=plugin)")

    # KB configuration
    knowledge_base_id: Optional[str] = Field(None, description="Knowledge base ID (when step_type=knowledge_base)")
    kb_query_template: Optional[str] = Field(None, description="Jinja2 query template (when step_type=knowledge_base)")

    # Parameters template
    params_template: Optional[Dict[str, Any]] = Field(None, description="Parameters with optional Jinja2 expressions")

    # Conditional execution
    condition_template: Optional[str] = Field(None, description="Jinja2 condition for skipping step")

    @field_validator('step_key')
    @classmethod
    def validate_step_key(cls, v):
        """Validate step key format."""
        if not v.strip():
            raise ValueError("Step key cannot be empty")
        # Ensure it's a valid identifier for template access
        if not v.replace('_', '').isalnum():
            raise ValueError("Step key must contain only alphanumeric characters and underscores")
        return v.strip()


class ExperienceStepCreate(ExperienceStepBase):
    """Schema for creating experience steps (used within experience creation)."""
    pass


class ExperienceStepUpdate(BaseModel):
    """Schema for updating experience steps."""

    model_config = ConfigDict(protected_namespaces=())

    step_key: Optional[str] = Field(None, min_length=1, max_length=50)
    step_type: Optional[StepType] = None
    order: Optional[int] = Field(None, ge=0)
    plugin_name: Optional[str] = Field(None, max_length=100)
    plugin_op: Optional[str] = Field(None, max_length=100)
    knowledge_base_id: Optional[str] = None
    kb_query_template: Optional[str] = None
    params_template: Optional[Dict[str, Any]] = None
    condition_template: Optional[str] = None


class ExperienceStepResponse(ExperienceStepBase):
    """Schema for experience step responses."""

    id: str
    experience_id: str
    required_scopes: Optional[List[str]] = None
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
    description: Optional[str] = Field(None, description="User-friendly description")
    visibility: ExperienceVisibility = Field(default=ExperienceVisibility.DRAFT, description="Visibility level")

    # Trigger configuration
    trigger_type: TriggerType = Field(default=TriggerType.MANUAL, description="How the experience is triggered")
    trigger_config: Optional[Dict[str, Any]] = Field(None, description="Trigger-specific configuration")

    # Continuity
    include_previous_run: bool = Field(default=False, description="Include previous run output in context")

    # LLM configuration
    llm_provider_id: Optional[str] = Field(None, description="LLM provider to use")
    model_name: Optional[str] = Field(None, max_length=100, description="Model name")

    # Prompt configuration
    prompt_id: Optional[str] = Field(None, description="Reference to shared prompt")
    inline_prompt_template: Optional[str] = Field(None, description="Jinja2 prompt template")

    # Constraints
    max_run_seconds: int = Field(default=120, ge=1, le=600, description="Maximum run duration")
    token_budget: Optional[int] = Field(None, ge=0, description="Token budget limit")

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        """Validate experience name."""
        if not v.strip():
            raise ValueError("Experience name cannot be empty")
        return v.strip()


class ExperienceCreate(ExperienceBase):
    """Schema for creating experiences."""

    steps: List[ExperienceStepCreate] = Field(default_factory=list, description="Experience steps")


class ExperienceUpdate(BaseModel):
    """Schema for updating experiences."""

    model_config = ConfigDict(protected_namespaces=())

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    visibility: Optional[ExperienceVisibility] = None
    trigger_type: Optional[TriggerType] = None
    trigger_config: Optional[Dict[str, Any]] = None
    include_previous_run: Optional[bool] = None
    llm_provider_id: Optional[str] = None
    model_name: Optional[str] = Field(None, max_length=100)
    prompt_id: Optional[str] = None
    inline_prompt_template: Optional[str] = None
    max_run_seconds: Optional[int] = Field(None, ge=1, le=600)
    token_budget: Optional[int] = Field(None, ge=0)

    # Steps can be replaced entirely
    steps: Optional[List[ExperienceStepCreate]] = None

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
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
    parent_version_id: Optional[str] = None

    # Expanded relationships
    steps: List[ExperienceStepResponse] = Field(default_factory=list)
    llm_provider: Optional[Dict[str, Any]] = None
    prompt: Optional[Dict[str, Any]] = None

    # Computed
    step_count: int = Field(default=0, description="Number of steps")
    last_run_at: Optional[datetime] = Field(None, description="When experience was last run")

    model_config = ConfigDict(from_attributes=True)


class ExperienceList(BaseModel):
    """Schema for paginated experience lists."""

    items: List[ExperienceResponse]
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

    input_params: Optional[Dict[str, Any]] = Field(None, description="User-provided parameters")


class ExperienceRunResponse(BaseModel):
    """Schema for experience run responses."""

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: str
    experience_id: str
    user_id: str
    previous_run_id: Optional[str] = None

    # Model snapshot
    model_provider_id: Optional[str] = None
    model_name: Optional[str] = None

    # Status
    status: RunStatus
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    # Data
    input_params: Optional[Dict[str, Any]] = None
    step_states: Optional[Dict[str, Any]] = None
    step_outputs: Optional[Dict[str, Any]] = None
    result_content: Optional[str] = None
    result_metadata: Optional[Dict[str, Any]] = None

    # Error
    error_message: Optional[str] = None
    error_details: Optional[Dict[str, Any]] = None

    created_at: datetime
    updated_at: datetime

    # Computed
    duration_seconds: Optional[float] = Field(None, description="Run duration in seconds")


class ExperienceRunList(BaseModel):
    """Schema for paginated run lists."""

    items: List[ExperienceRunResponse]
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
    experience_description: Optional[str] = None

    # Latest run info
    latest_run_id: Optional[str] = None
    latest_run_status: Optional[RunStatus] = None
    latest_run_finished_at: Optional[datetime] = None
    result_preview: Optional[str] = Field(None, description="Truncated result content")

    # User can run?
    can_run: bool = Field(default=True, description="Whether user has required identities")
    missing_identities: List[str] = Field(default_factory=list)


class UserExperienceResults(BaseModel):
    """Schema for user's experience results dashboard."""

    experiences: List[ExperienceResultSummary]
    total: int
