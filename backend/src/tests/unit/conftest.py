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
    mock.llm_encryption_key = None
    return mock
