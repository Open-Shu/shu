"""
Shared pytest fixtures and path setup for unit tests.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Set required environment variables BEFORE any shu imports to prevent
# Pydantic Settings validation errors. These are test-only defaults.
os.environ.setdefault("SHU_DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test_db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("SHU_LLM_ENCRYPTION_KEY", "5n7s4FR2ctJo5EBLUIgx_cKuX-ydpE5jg-xSMlKz5zQ=")
os.environ.setdefault("SHU_OAUTH_ENCRYPTION_KEY", "Ngyzgo3L2B3D_b6MXEffwnS68hPMGS_4YwWRrtNSwQs=")

# Add backend/src to sys.path so shu.* imports work when running pytest from repo root.
PROJECT_SRC = Path(__file__).resolve().parents[2]
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

import pytest

@pytest.fixture
def mock_settings():
    """Provide a mock Settings object for tests that need custom configuration."""
    mock = MagicMock()
    # Set common defaults that tests might need
    mock.title = "Shu"
    mock.database_url = "postgresql+asyncpg://test:test@localhost:5432/test_db"
    mock.jwt_secret_key = "test-secret-key"
    mock.debug = False
    mock.environment = "development"
    mock.log_level = "DEBUG"
    mock.redis_url = "redis://localhost:6379"
    mock.chat_attachment_storage_dir = "/tmp/test_attachments"
    mock.chat_attachment_max_size = 20 * 1024 * 1024
    mock.chat_attachment_allowed_types = ["pdf", "txt", "png", "jpg"]
    mock.chat_attachment_ttl_days = 14
    mock.llm_encryption_key = "5n7s4FR2ctJo5EBLUIgx_cKuX-ydpE5jg-xSMlKz5zQ="
    mock.oauth_encryption_key = "Ngyzgo3L2B3D_b6MXEffwnS68hPMGS_4YwWRrtNSwQs="
    return mock

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
