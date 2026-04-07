"""
Database models for Shu RAG Backend.

This package contains SQLAlchemy models for all database tables
used by the Shu application.
"""

from .access_policy import AccessPolicy, AccessPolicyBinding, AccessPolicyStatement
from .agent_memory import AgentMemory
from .attachment import Attachment, MessageAttachment
from .base import Base
from .document import (
    ENTITY_TYPE_EMAIL_ADDRESS,
    ENTITY_TYPE_ORGANIZATION,
    # Backward-compatible constants
    ENTITY_TYPE_PERSON,
    ROLE_AUTHOR,
    ROLE_DECISION_MAKER,
    ROLE_MENTIONED,
    ROLE_RECIPIENT,
    ROLE_SUBJECT,
    # TypedDicts for JSONB structures
    CapabilityManifest,
    Document,
    DocumentChunk,
    DocumentParticipant,
    DocumentProject,
    DocumentQuery,
    # Enums (preferred)
    ParticipantEntityType,
    ParticipantRole,
    RelationalContext,
)
from .experience import Experience, ExperienceDependency, ExperienceRun, ExperienceStep
from .knowledge_base import KnowledgeBase
from .llm_provider import Conversation, LLMModel, LLMProvider, LLMUsage, Message
from .mcp_server_connection import McpServerConnection
from .model_configuration import ModelConfiguration
from .model_configuration_kb_prompt import ModelConfigurationKBPrompt
from .plugin_execution import PluginExecution
from .plugin_feed import PluginFeed
from .plugin_registry import PluginDefinition
from .plugin_storage import PluginStorage
from .plugin_subscription import PluginSubscription
from .prompt import EntityType, Prompt, PromptAssignment
from .provider_credential import ProviderCredential
from .provider_identity import ProviderIdentity
from .rbac import (
    GroupRole,
    UserGroup,
    UserGroupMembership,
)
from .system_setting import SystemSetting
from .user_preferences import UserPreferences

# Note: User model is in auth.models to avoid circular imports

__all__ = [
    "ENTITY_TYPE_EMAIL_ADDRESS",
    "ENTITY_TYPE_ORGANIZATION",
    "ENTITY_TYPE_PERSON",
    "ROLE_AUTHOR",
    "ROLE_DECISION_MAKER",
    "ROLE_MENTIONED",
    "ROLE_RECIPIENT",
    "ROLE_SUBJECT",
    "AccessPolicy",
    "AccessPolicyBinding",
    "AccessPolicyStatement",
    "AgentMemory",
    "Base",
    "CapabilityManifest",
    "Conversation",
    "Document",
    "DocumentChunk",
    "DocumentParticipant",
    "DocumentProject",
    "DocumentQuery",
    "EntityType",
    "Experience",
    "ExperienceDependency",
    "ExperienceRun",
    "ExperienceStep",
    "GroupRole",
    "KnowledgeBase",
    "LLMModel",
    "LLMProvider",
    "LLMUsage",
    "McpServerConnection",
    "Message",
    "ModelConfiguration",
    "ModelConfigurationKBPrompt",
    "ParticipantEntityType",
    "ParticipantRole",
    "PluginDefinition",
    "PluginStorage",
    "Prompt",
    "PromptAssignment",
    "ProviderCredential",
    "ProviderIdentity",
    "RelationalContext",
    "SystemSetting",
    "UserGroup",
    "UserGroupMembership",
    "UserPreferences",
]
