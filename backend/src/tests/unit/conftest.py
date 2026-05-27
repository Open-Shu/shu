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

# Force a deployment mode that's valid alongside a tenant id. The tenant-
# isolation cross-field validator rejects SHU_TENANT_ID under self_hosted and
# multi_tenant; `silo` is the combo the suite relies on.
os.environ["SHU_DEPLOYMENT_MODE"] = "silo"
os.environ.setdefault("SHU_TENANT_ID", "00000000-0000-0000-0000-000000000001")

# shu.core.config runs `load_dotenv(override=True)` at import, which reloads the
# developer's .env and overwrites the line above — if that .env sets
# SHU_DEPLOYMENT_MODE=multi_tenant it collides with SHU_TENANT_ID and breaks
# collection for the entire suite. Wrap load_dotenv (before any shu import binds
# it via `from dotenv import load_dotenv`) so .env still supplies every other
# setting, but the test deployment mode is re-pinned after each load. Keeps the
# unit suite hermetic against a local .env without touching production config.
import dotenv as _dotenv

_real_load_dotenv = _dotenv.load_dotenv


def _load_dotenv_pinning_test_mode(*args, **kwargs):
    result = _real_load_dotenv(*args, **kwargs)
    os.environ["SHU_DEPLOYMENT_MODE"] = "silo"
    return result


_dotenv.load_dotenv = _load_dotenv_pinning_test_mode

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
def _default_tenant_context():
    """Set a fixed tenant_context for every unit test.

    The SHU-761 before_flush listener refuses to flush tenant-scoped objects
    without a context — that's correct in production, but unit tests rarely
    construct a tenant context explicitly. A fixed UUID matching the silo
    SHU_TENANT_ID above keeps existing tests passing while preserving the
    listener's protection: anything that tries to insert a *different* tenant
    still raises CrossTenantInsertError.

    Tests that need to exercise the missing-context path should pop the
    context with ``tenant_context.reset(token)``.
    """
    from shu.core.tenant import _lookup_tenant_for_user, tenant_context

    # Clear the process-local async-LRU on _lookup_tenant_for_user before
    # every test. The cache is sized at 4096 and persists across tests, so a
    # test that patches the underlying lookup (or the SD function) to return
    # one tenant_id for a given user_id will see the stale earlier value if a
    # prior test mocked it differently. Clearing per-test gives us
    # isolation; production still benefits from cross-request caching.
    _lookup_tenant_for_user.cache_clear()

    token = tenant_context.set("00000000-0000-0000-0000-000000000001")
    yield
    tenant_context.reset(token)


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
    """Reset per-tenant billing caches between tests, with a None-sentinel default.

    SHU-761 moved the cache from a single ``_cache`` singleton to
    ``_cache_by_tenant`` keyed by tenant_id. Tests should default to
    "no CP / enforcement disabled" — pre-populate the test tenant's slot
    with ``None`` so ``get_billing_state_cache()`` short-circuits without
    attempting a real CpClient build (which would invoke the http_client
    singleton and trip on cross-test event-loop binding).

    Tests that need a real-looking cache use ``install_stub_cache`` which
    overwrites the slot with a StubBillingStateCache.
    """
    from shu.billing import billing_state_cache as billing_state_cache_module
    from shu.billing.billing_state_cache import reset_billing_state_cache
    from shu.core.tenant import tenant_context

    reset_billing_state_cache()
    test_tid = tenant_context.get(None)
    if test_tid is not None:
        billing_state_cache_module._cache_by_tenant[test_tid] = None

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
    from shu.billing.entitlements import EntitlementSet, LimitSet

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
        limits=LimitSet(),
        subscription_status=None,
        current_period_start=None,
        current_period_end=None,
        cancel_at_period_end=False,
        canceled_at=None,
        usage_markup_multiplier=None,
    )


def healthy_billing_state():
    """BillingState representing 'tenant is paying, no enforcement'."""
    from shu.billing.cp_client import HEALTHY_DEFAULT

    return HEALTHY_DEFAULT


@pytest.fixture
def install_stub_cache():
    """Install a stub billing-state cache for the test's current tenant_context.

    Returns a callable `_install(state) -> StubBillingStateCache` so a single
    test can replace the cache value mid-flight (e.g. simulating a CP poll
    that flips the gate). Writes the stub into ``_cache_by_tenant`` keyed by
    the autouse-fixture's tenant_id so ``get_billing_state_cache()`` finds it.

    Resets on both setup and teardown so a test that requests this fixture
    but never calls `_install` sees an empty cache dict (the unconfigured-CP
    bypass path) regardless of test execution order.
    """
    from shu.billing import billing_state_cache as billing_state_cache_module
    from shu.core.tenant import tenant_context

    # No reset at setup/teardown — the autouse ``_reset_billing_state_cache_between_tests``
    # fixture handles that (and pre-populates the test tenant's slot with None
    # so an un-installed test sees "cache disabled"). Doing it here too would
    # clobber that pre-population and trigger the lazy real-CP build path.

    def _install(value) -> StubBillingStateCache:
        stub = StubBillingStateCache(value)
        # The autouse _default_tenant_context fixture sets tenant_context to a
        # fixed test UUID; install for that tenant so the lookup finds us.
        tid = tenant_context.get()
        billing_state_cache_module._cache_by_tenant[tid] = stub
        return stub

    yield _install


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
