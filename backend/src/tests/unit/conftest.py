"""
Shared pytest fixtures and path setup for unit tests.
"""

import os
import sys
from datetime import UTC, datetime
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

# Add backend/ to sys.path so migrations.* imports work for migration tests.
# The migration files use `from migrations.helpers import ...` which requires backend/ on path.
BACKEND_ROOT = Path(__file__).resolve().parents[3]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Add backend/migrations to sys.path so versions.* imports work for migration tests.
MIGRATIONS_ROOT = BACKEND_ROOT / "migrations"
if str(MIGRATIONS_ROOT) not in sys.path:
    sys.path.insert(0, str(MIGRATIONS_ROOT))

import pytest


@pytest.fixture(autouse=True)
def _clear_active_check_cache_between_tests():
    """Drop the resolver's positive-result active-check cache between tests.

    The cache is module-level global state. Without this, a test that lets the
    real ``ensure_provider_and_model_active`` succeed would hide a follow-up
    test's inactive-path assertion for up to the TTL.
    """
    from shu.core.external_model_resolver import _clear_active_check_cache

    _clear_active_check_cache()
    yield
    _clear_active_check_cache()


# =============================================================================
# SHU-703 billing-state cache stubs
#
# Seven test files exercise the subscription-gate path; consolidating the
# stub class + fixture here keeps each test file focused on its own contract
# instead of reproducing the cache-injection scaffolding.
# =============================================================================


@pytest.fixture(autouse=True)
def _reset_billing_state_cache_between_tests():
    """Always reset the module-level `_cache` between tests.

    Tests that drive the FastAPI lifespan (test_worker_mode.py) call
    `initialize_billing_state_cache()`, which leaves `_cache` pointing at a
    real `BillingStateCache` backed by an httpx client. The lifespan's
    shutdown closes that client, but `_cache` stays set — so the next test
    that hits `assert_subscription_active()` (e.g. via the embedding-service
    gate) calls `cache.get()` on the stale cache and gets
    'Cannot send a request, as the client has been closed.'
    Belt-and-suspenders cleanup; cheap (just sets `_cache = None`).
    """
    from shu.billing.billing_state_cache import reset_billing_state_cache

    yield
    reset_billing_state_cache()


class StubBillingStateCache:
    """Async stand-in for `BillingStateCache` — only `.get()` is needed.

    Tests inject this via `install_stub_cache(state)` so the helper under
    test sees a deterministic BillingState without spinning a real cache.
    """

    def __init__(self, value):
        self.value = value

    async def get(self):
        return self.value


def disabled_billing_state(
    *,
    grace_days: int = 7,
    payment_failed_at: datetime | None = None,
):
    """BillingState representing 'CP has paused this tenant'.

    Default `payment_failed_at` is a fixed 2026-01-01 — tests asserting on
    grace_deadline values get a stable input without each one inventing one.
    Trial/grant fields default to inert values; tests that target trial
    behavior construct dedicated states inline.
    """
    from decimal import Decimal

    from shu.billing.cp_client import BillingState
    from shu.billing.entitlements import EntitlementSet

    return BillingState(
        openrouter_key_disabled=True,
        payment_failed_at=payment_failed_at or datetime(2026, 1, 1, tzinfo=UTC),
        payment_grace_days=grace_days,
        entitlements=EntitlementSet(),
        is_trial=False,
        trial_deadline=None,
        total_grant_amount=Decimal(0),
        remaining_grant_amount=Decimal(0),
        seat_price_usd=Decimal(0),
    )


def healthy_billing_state():
    """BillingState representing 'tenant is paying, no enforcement'."""
    from shu.billing.cp_client import HEALTHY_DEFAULT

    return HEALTHY_DEFAULT


@pytest.fixture
def install_stub_cache():
    """Install a stub billing-state cache singleton, restore None on teardown.

    Returns a callable `_install(state) -> StubBillingStateCache` so a single
    test can replace the cache value mid-flight (e.g. simulating a CP poll
    that flips the gate). Patches the module-level `_cache` directly — the
    helper only consumes the singleton via `get_billing_state_cache()`, so
    the stub's surface is enough.

    Resets on both setup and teardown so a test that requests this fixture
    but never calls `_install` sees `_cache=None` (the self-hosted bypass
    path) regardless of test execution order.
    """
    from shu.billing import billing_state_cache as billing_state_cache_module
    from shu.billing.billing_state_cache import reset_billing_state_cache

    reset_billing_state_cache()

    def _install(value) -> StubBillingStateCache:
        stub = StubBillingStateCache(value)
        billing_state_cache_module._cache = stub
        return stub

    yield _install
    reset_billing_state_cache()


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
    # User model (required for relationships)
    from shu.auth.models import User  # noqa: F401
    from shu.models import (  # noqa: F401
        AgentMemory,
        Base,
        Conversation,
        Document,
        DocumentChunk,
        DocumentParticipant,
        DocumentProject,
        DocumentQuery,
        KnowledgeBase,
        LLMModel,
        LLMProvider,
        LLMUsage,
        Message,
        ModelConfiguration,
        ModelConfigurationKBPrompt,
        PluginDefinition,
        PluginStorage,
        Prompt,
        PromptAssignment,
        ProviderCredential,
        ProviderIdentity,
        SystemSetting,
        UserGroup,
        UserGroupMembership,
        UserPreferences,
    )
    from shu.models.attachment import Attachment, MessageAttachment  # noqa: F401
    from shu.models.plugin_execution import PluginExecution  # noqa: F401
    from shu.models.plugin_feed import PluginFeed  # noqa: F401
    from shu.models.plugin_subscription import PluginSubscription  # noqa: F401

    # Additional models not in __all__
    from shu.models.provider_type_definition import ProviderTypeDefinition  # noqa: F401
except ImportError as e:
    import warnings

    warnings.warn(f"Could not import all models for SQLAlchemy registry: {e}")
