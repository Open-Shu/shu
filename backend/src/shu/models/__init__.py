"""
Database models for Shu RAG Backend.

This package contains SQLAlchemy models for all database tables
used by the Shu application.
"""

from .base import Base
from .knowledge_base import KnowledgeBase

from .prompt import Prompt, PromptAssignment, EntityType
from .document import (
    Document,
    DocumentChunk,
    DocumentQuery,
    DocumentParticipant,
    DocumentProject,
    # Enums (preferred)
    ParticipantEntityType,
    ParticipantRole,
    # TypedDicts for JSONB structures
    CapabilityManifest,
    RelationalContext,
    # Backward-compatible constants
    ENTITY_TYPE_PERSON,
    ENTITY_TYPE_ORGANIZATION,
    ENTITY_TYPE_EMAIL_ADDRESS,
    ROLE_AUTHOR,
    ROLE_RECIPIENT,
    ROLE_MENTIONED,
    ROLE_DECISION_MAKER,
    ROLE_SUBJECT,
)
from .llm_provider import LLMProvider, LLMModel, LLMUsage, Conversation, Message
from .attachment import Attachment, MessageAttachment
from .model_configuration import ModelConfiguration
from .model_configuration_kb_prompt import ModelConfigurationKBPrompt
from .user_preferences import UserPreferences

from .provider_identity import ProviderIdentity
from .provider_credential import ProviderCredential
from .rbac import UserGroup, UserGroupMembership, KnowledgeBasePermission, PermissionLevel, GroupRole
from .plugin_registry import PluginDefinition
from .agent_memory import AgentMemory
from .plugin_storage import PluginStorage
from .plugin_execution import PluginExecution
from .plugin_feed import PluginFeed
from .plugin_subscription import PluginSubscription
from .system_setting import SystemSetting
from .experience import Experience, ExperienceStep, ExperienceRun

# Note: User model is in auth.models to avoid circular imports

__all__ = [
    "Base",
    "KnowledgeBase",

    "Prompt",
    "PromptAssignment",
    "EntityType",
    "Document",
    "DocumentChunk",
    "DocumentQuery",
    "DocumentParticipant",
    "DocumentProject",
    # Enums (preferred for new code)
    "ParticipantEntityType",
    "ParticipantRole",
    # TypedDicts for JSONB structures
    "CapabilityManifest",
    "RelationalContext",
    # Backward-compatible constants
    "ENTITY_TYPE_PERSON",
    "ENTITY_TYPE_ORGANIZATION",
    "ENTITY_TYPE_EMAIL_ADDRESS",
    "ROLE_AUTHOR",
    "ROLE_RECIPIENT",
    "ROLE_MENTIONED",
    "ROLE_DECISION_MAKER",
    "ROLE_SUBJECT",
    "LLMProvider",
    "LLMModel",
    "LLMUsage",
    "Conversation",
    "Message",
    "ModelConfiguration",
    "ModelConfigurationKBPrompt",
    "UserPreferences",

    "ProviderIdentity",
    "ProviderCredential",
    "UserGroup",
    "UserGroupMembership",
    "KnowledgeBasePermission",
    "PermissionLevel",
    "GroupRole",
    "PluginDefinition",
    "AgentMemory",
    "PluginStorage",
    "SystemSetting",
    "Experience",
    "ExperienceStep",
    "ExperienceRun",
]
