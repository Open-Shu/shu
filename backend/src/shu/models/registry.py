"""Model registry for Shu.

This module ensures all SQLAlchemy models are imported and registered
before any model relationships are initialized. This prevents circular
import issues and ensures all models are available for relationship resolution.
"""


def register_all_models():
    """Import all SQLAlchemy models to ensure they're registered with SQLAlchemy.

    This function should be called before any SQLAlchemy operations that might
    trigger relationship initialization, especially in worker processes.
    """
    # Import all models from the main models package
    # Import User model from auth package to ensure it's available for relationships
    from ..auth.models import User, UserRole
    from . import (
        AgentMemory,
        Base,
        Conversation,
        Document,
        DocumentChunk,
        EntityType,
        KnowledgeBase,
        LLMModel,
        LLMProvider,
        LLMUsage,
        Message,
        ModelConfiguration,
        PluginDefinition,
        PluginExecution,
        PluginFeed,
        Prompt,
        PromptAssignment,
        SystemSetting,
        UserPreferences,
    )

    # Return all model classes for reference if needed
    return {
        "Base": Base,
        "KnowledgeBase": KnowledgeBase,
        "Prompt": Prompt,
        "PromptAssignment": PromptAssignment,
        "EntityType": EntityType,
        "Document": Document,
        "DocumentChunk": DocumentChunk,
        "LLMProvider": LLMProvider,
        "LLMModel": LLMModel,
        "LLMUsage": LLMUsage,
        "Conversation": Conversation,
        "Message": Message,
        "ModelConfiguration": ModelConfiguration,
        "UserPreferences": UserPreferences,
        "User": User,
        "UserRole": UserRole,
        "PluginDefinition": PluginDefinition,
        "AgentMemory": AgentMemory,
        "PluginExecution": PluginExecution,
        "PluginFeed": PluginFeed,
        "SystemSetting": SystemSetting,
    }
