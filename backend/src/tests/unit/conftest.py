"""
Shared pytest fixtures and path setup for unit tests.
"""

import sys
from pathlib import Path

# Add backend/src to sys.path so shu.* imports work when running pytest from repo root.
PROJECT_SRC = Path(__file__).resolve().parents[2]
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

# Register all SQLAlchemy models to ensure relationship resolution works.
# This is needed because SQLAlchemy resolves all relationships when any model is instantiated.
# Import all models directly rather than using registry which may be incomplete.
try:
    # Core models
    from shu.models import (  # noqa: F401
        Base, KnowledgeBase, Prompt, PromptAssignment,
        Document, DocumentChunk, DocumentQuery, DocumentParticipant, DocumentProject,
        LLMProvider, LLMModel, LLMUsage, Conversation, Message,
        ModelConfiguration, ModelConfigurationKBPrompt, UserPreferences,
        ProviderIdentity, ProviderCredential,
        UserGroup, UserGroupMembership, KnowledgeBasePermission,
        PluginDefinition, AgentMemory, PluginStorage,
        SystemSetting,
    )
    # Additional models not in __all__
    from shu.models.provider_type_definition import ProviderTypeDefinition  # noqa: F401
    from shu.models.plugin_execution import PluginExecution  # noqa: F401
    from shu.models.plugin_feed import PluginFeed  # noqa: F401
    from shu.models.plugin_subscription import PluginSubscription  # noqa: F401
    from shu.models.attachment import Attachment, MessageAttachment  # noqa: F401
    # User model (required for relationships)
    from shu.auth.models import User  # noqa: F401
except ImportError as e:
    import warnings
    warnings.warn(f"Could not import all models for SQLAlchemy registry: {e}")
