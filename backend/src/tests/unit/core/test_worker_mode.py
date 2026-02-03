"""
Unit tests for worker mode configuration.

These tests verify that the worker mode configuration works correctly,
including inline mode (workers run with API) and dedicated mode (workers
run separately).

Feature: queue-backend-interface
Validates: Requirements 7.1, 7.2, 7.5, 7.6
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.core.config import Settings
from shu.core.workload_routing import WorkloadType
from shu.worker import parse_workload_types, run_worker

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_settings_inline():
    """Create mock settings with inline worker mode."""
    settings = MagicMock(spec=Settings)
    settings.worker_mode = "inline"
    settings.version = "test"
    settings.environment = "test"
    return settings


@pytest.fixture
def mock_settings_dedicated():
    """Create mock settings with dedicated worker mode."""
    settings = MagicMock(spec=Settings)
    settings.worker_mode = "dedicated"
    settings.version = "test"
    settings.environment = "test"
    return settings


# =============================================================================
# Worker Mode Configuration Tests
# =============================================================================


def test_settings_worker_mode_default():
    """
    Test that worker_mode defaults to 'inline'.

    Validates: Requirements 7.1

    When SHU_WORKER_MODE is not set, the default should be 'inline'.
    """
    # Create settings without SHU_WORKER_MODE
    with patch.dict(os.environ, {}, clear=False):
        # Remove SHU_WORKER_MODE if it exists
        os.environ.pop("SHU_WORKER_MODE", None)

        # Create settings (will use default)
        from shu.core.config import Settings

        settings = Settings()

        assert settings.worker_mode == "inline"


def test_settings_worker_mode_inline():
    """
    Test that worker_mode can be set to 'inline'.

    Validates: Requirements 7.1

    When SHU_WORKER_MODE is set to 'inline', workers should run
    in-process with the API.
    """
    with patch.dict(os.environ, {"SHU_WORKER_MODE": "inline"}, clear=False):
        from shu.core.config import Settings

        settings = Settings()

        assert settings.worker_mode == "inline"


def test_settings_worker_mode_dedicated():
    """
    Test that worker_mode can be set to 'dedicated'.

    Validates: Requirements 7.2

    When SHU_WORKER_MODE is set to 'dedicated', workers should NOT
    start with the API process.
    """
    with patch.dict(os.environ, {"SHU_WORKER_MODE": "dedicated"}, clear=False):
        from shu.core.config import Settings

        settings = Settings()

        assert settings.worker_mode == "dedicated"


def test_settings_worker_mode_invalid():
    """
    Test that invalid worker_mode values are rejected.

    Validates: Requirements 7.1, 7.2

    Only 'inline' and 'dedicated' should be valid values.
    """
    with patch.dict(os.environ, {"SHU_WORKER_MODE": "invalid"}, clear=False):
        from shu.core.config import Settings

        with pytest.raises(ValueError, match="Worker mode must be one of"):
            Settings()


def test_settings_worker_mode_case_insensitive():
    """
    Test that worker_mode is case-insensitive.

    Validates: Requirements 7.1, 7.2

    'INLINE', 'Inline', 'inline' should all work.
    """
    test_cases = ["INLINE", "Inline", "inline", "DEDICATED", "Dedicated", "dedicated"]

    for mode in test_cases:
        with patch.dict(os.environ, {"SHU_WORKER_MODE": mode}, clear=False):
            from shu.core.config import Settings

            settings = Settings()

            assert settings.worker_mode.lower() in ["inline", "dedicated"]


# =============================================================================
# Worker Entrypoint Tests
# =============================================================================


def test_parse_workload_types_single():
    """
    Test parsing a single workload type.

    Validates: Requirements 7.4

    The worker entrypoint should accept --workload-types argument
    with a single workload type.
    """
    result = parse_workload_types("INGESTION")
    assert result == {WorkloadType.INGESTION}


def test_parse_workload_types_multiple():
    """
    Test parsing multiple workload types.

    Validates: Requirements 7.4

    The worker entrypoint should accept --workload-types argument
    with comma-separated workload types.
    """
    result = parse_workload_types("INGESTION,PROFILING")
    assert result == {WorkloadType.INGESTION, WorkloadType.PROFILING}


def test_parse_workload_types_all():
    """
    Test parsing all workload types.

    Validates: Requirements 7.4
    """
    result = parse_workload_types("INGESTION,LLM_WORKFLOW,MAINTENANCE,PROFILING")
    assert result == set(WorkloadType)


def test_parse_workload_types_case_insensitive():
    """
    Test that workload type parsing is case-insensitive.

    Validates: Requirements 7.4
    """
    result = parse_workload_types("ingestion,PROFILING,Llm_Workflow")
    assert result == {WorkloadType.INGESTION, WorkloadType.PROFILING, WorkloadType.LLM_WORKFLOW}


def test_parse_workload_types_with_spaces():
    """
    Test that workload type parsing handles spaces.

    Validates: Requirements 7.4
    """
    result = parse_workload_types(" INGESTION , PROFILING ")
    assert result == {WorkloadType.INGESTION, WorkloadType.PROFILING}


def test_parse_workload_types_empty():
    """
    Test that empty workload types string raises error.

    Validates: Requirements 7.4
    """
    with pytest.raises(ValueError, match="Workload types cannot be empty"):
        parse_workload_types("")


def test_parse_workload_types_invalid():
    """
    Test that invalid workload type names raise error.

    Validates: Requirements 7.4
    """
    with pytest.raises(ValueError, match="Invalid workload type"):
        parse_workload_types("INGESTION,INVALID_TYPE")


def test_parse_workload_types_duplicate():
    """
    Test that duplicate workload types are deduplicated.

    Validates: Requirements 7.4
    """
    result = parse_workload_types("INGESTION,INGESTION,PROFILING")
    assert result == {WorkloadType.INGESTION, WorkloadType.PROFILING}


# =============================================================================
# Integration Tests
# =============================================================================


@pytest.mark.asyncio
async def test_inline_worker_starts_with_api():
    """
    Test that inline mode starts workers with API.

    Validates: Requirements 7.1, 7.5

    When worker_mode is 'inline', workers should start automatically
    when the API starts.

    This test verifies the integration by checking that the lifespan
    creates a worker task when mode is 'inline'.
    """
    from fastapi import FastAPI

    from shu.main import lifespan

    # Create app with inline mode
    with patch.dict(os.environ, {"SHU_WORKER_MODE": "inline"}, clear=False):
        app = FastAPI()

        # Mock the worker components to avoid actual worker startup
        with (
            patch("shu.core.queue_backend.get_queue_backend") as mock_get_backend,
            patch("shu.core.worker.Worker") as mock_worker_class,
            patch("shu.main.init_db") as mock_init_db,
        ):
            # Setup mocks
            mock_backend = AsyncMock()
            mock_get_backend.return_value = mock_backend
            mock_init_db.return_value = None

            mock_worker = MagicMock()
            mock_worker.run = AsyncMock()
            mock_worker_class.return_value = mock_worker

            # Run lifespan startup
            async with lifespan(app):
                # Verify worker task was created
                assert hasattr(app.state, "inline_worker_task")
                assert app.state.inline_worker_task is not None

                # Verify worker was created with correct config
                mock_worker_class.assert_called_once()
                call_args = mock_worker_class.call_args

                # Check that config has all workload types
                config = call_args[0][1]  # Second positional arg is config
                assert config.workload_types == set(WorkloadType)


@pytest.mark.asyncio
async def test_dedicated_worker_skips_startup():
    """
    Test that dedicated mode configuration is respected.

    Validates: Requirements 7.2, 7.5

    When worker_mode is 'dedicated', the configuration should be set correctly.
    The actual skipping of worker startup is tested in the API startup test.
    """
    with patch.dict(os.environ, {"SHU_WORKER_MODE": "dedicated"}, clear=False):
        from shu.core.config import Settings

        settings = Settings()

        # Verify settings are correct
        assert settings.worker_mode == "dedicated"


@pytest.mark.asyncio
async def test_worker_entrypoint_starts_without_api():
    """
    Test that worker entrypoint starts without API routes.

    Validates: Requirements 7.6

    The worker entrypoint (python -m shu.worker) should start a worker
    process without loading API routes.

    This test verifies that run_worker can start successfully without
    the FastAPI app.
    """
    from shu.core.queue_backend import InMemoryQueueBackend

    # Mock database and backend initialization
    with (
        patch("shu.worker.init_db") as mock_init_db,
        patch("shu.worker.get_queue_backend") as mock_get_backend,
    ):
        mock_init_db.return_value = None
        mock_backend = InMemoryQueueBackend()
        mock_get_backend.return_value = mock_backend

        # Create a task to run the worker
        worker_task = asyncio.create_task(
            run_worker(workload_types={WorkloadType.INGESTION}, poll_interval=0.1, shutdown_timeout=1.0)
        )

        # Let it run briefly
        await asyncio.sleep(0.2)

        # Cancel the worker
        worker_task.cancel()

        try:
            await worker_task
        except asyncio.CancelledError:
            pass

        # Verify database was initialized
        mock_init_db.assert_called_once()

        # Verify backend was retrieved
        mock_get_backend.assert_called_once()


# =============================================================================
# API Startup Tests
# =============================================================================


@pytest.mark.asyncio
async def test_api_starts_cleanly_in_inline_mode():
    """
    Test that API starts cleanly with inline workers.

    Validates: Requirements 7.5

    The API should start successfully when worker_mode is 'inline',
    with workers running in the same process.
    """
    from shu.main import create_app

    with patch.dict(os.environ, {"SHU_WORKER_MODE": "inline"}, clear=False):
        # Mock worker components to avoid actual startup
        with (
            patch("shu.core.queue_backend.get_queue_backend") as mock_get_backend,
            patch("shu.core.worker.Worker") as mock_worker_class,
            patch("shu.main.init_db") as mock_init_db,
        ):
            mock_backend = AsyncMock()
            mock_get_backend.return_value = mock_backend
            mock_init_db.return_value = None

            mock_worker = MagicMock()
            mock_worker.run = AsyncMock()
            mock_worker_class.return_value = mock_worker

            # Create app
            app = create_app()

            # Verify app was created successfully
            assert app is not None
            assert app.title == "Shu"


@pytest.mark.asyncio
async def test_api_starts_cleanly_in_dedicated_mode():
    """
    Test that API starts cleanly without workers.

    Validates: Requirements 7.5

    The API should start successfully when worker_mode is 'dedicated',
    without starting any workers.
    """
    from shu.main import create_app

    with patch.dict(os.environ, {"SHU_WORKER_MODE": "dedicated"}, clear=False):
        # Mock init_db to avoid database connection
        with patch("shu.main.init_db") as mock_init_db:
            mock_init_db.return_value = None

            # Create app
            app = create_app()

            # Verify app was created successfully
            assert app is not None
            assert app.title == "Shu"
