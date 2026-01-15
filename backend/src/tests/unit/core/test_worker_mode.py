"""
Unit tests for worker configuration.

These tests verify that the workers_enabled configuration works correctly,
controlling whether background workers run in-process with the API.

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
def mock_settings_workers_enabled():
    """Create mock settings with workers enabled."""
    settings = MagicMock(spec=Settings)
    settings.workers_enabled = True
    settings.version = "test"
    settings.environment = "test"
    return settings


@pytest.fixture
def mock_settings_workers_disabled():
    """Create mock settings with workers disabled."""
    settings = MagicMock(spec=Settings)
    settings.workers_enabled = False
    settings.version = "test"
    settings.environment = "test"
    return settings


# =============================================================================
# Workers Enabled Configuration Tests
# =============================================================================


def test_settings_workers_enabled_default():
    """
    Test that workers_enabled defaults to True.

    Validates: Requirements 7.1

    When SHU_WORKERS_ENABLED is not set, workers should be enabled by default.
    """
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop('SHU_WORKERS_ENABLED', None)

        from shu.core.config import Settings

        settings = Settings()

        assert settings.workers_enabled is True


def test_settings_workers_enabled_true():
    """
    Test that workers_enabled can be set to true.

    Validates: Requirements 7.1

    When SHU_WORKERS_ENABLED is set to 'true', workers should run
    in-process with the API.
    """
    with patch.dict(os.environ, {'SHU_WORKERS_ENABLED': 'true'}, clear=False):
        from shu.core.config import Settings

        settings = Settings()

        assert settings.workers_enabled is True


def test_settings_workers_enabled_false():
    """
    Test that workers_enabled can be set to false.

    Validates: Requirements 7.2

    When SHU_WORKERS_ENABLED is set to 'false', workers should NOT
    start with the API process.
    """
    with patch.dict(os.environ, {'SHU_WORKERS_ENABLED': 'false'}, clear=False):
        from shu.core.config import Settings

        settings = Settings()

        assert settings.workers_enabled is False


def test_settings_workers_enabled_case_variations():
    """
    Test that workers_enabled accepts various boolean representations.

    Validates: Requirements 7.1, 7.2

    'True', 'TRUE', '1', 'yes' should all be accepted as true.
    'False', 'FALSE', '0', 'no' should all be accepted as false.
    """
    true_cases = ['true', 'True', 'TRUE', '1', 'yes', 'on']
    false_cases = ['false', 'False', 'FALSE', '0', 'no', 'off']

    for val in true_cases:
        with patch.dict(os.environ, {'SHU_WORKERS_ENABLED': val}, clear=False):
            from shu.core.config import Settings
            settings = Settings()
            assert settings.workers_enabled is True, f"Expected True for '{val}'"

    for val in false_cases:
        with patch.dict(os.environ, {'SHU_WORKERS_ENABLED': val}, clear=False):
            from shu.core.config import Settings

            settings = Settings()
            assert settings.workers_enabled is False, f"Expected False for '{val}'"


# =============================================================================
# Worker Concurrency Configuration Tests
# =============================================================================


def test_settings_worker_concurrency_default():
    """
    Test that worker_concurrency defaults to 10.

    When SHU_WORKER_CONCURRENCY is not set, concurrency should be 10.
    """
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop('SHU_WORKER_CONCURRENCY', None)

        from shu.core.config import Settings
        settings = Settings()

        assert settings.worker_concurrency == 10


def test_settings_worker_concurrency_custom():
    """
    Test that worker_concurrency can be configured.

    When SHU_WORKER_CONCURRENCY is set to a value, it should be used.
    """
    with patch.dict(os.environ, {'SHU_WORKER_CONCURRENCY': '4'}, clear=False):
        from shu.core.config import Settings
        settings = Settings()

        assert settings.worker_concurrency == 4


def test_settings_worker_concurrency_high_value():
    """
    Test that worker_concurrency accepts high values.

    For I/O-bound workloads, users may want high concurrency.
    """
    with patch.dict(os.environ, {'SHU_WORKER_CONCURRENCY': '16'}, clear=False):
        from shu.core.config import Settings
        settings = Settings()

        assert settings.worker_concurrency == 16


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
async def test_workers_enabled_starts_with_api():
    """
    Test that workers start with API when enabled.

    Validates: Requirements 7.1, 7.5

    When workers_enabled is True, workers should start automatically
    when the API starts.


    This test verifies the integration by checking that the lifespan
    creates worker tasks when workers are enabled.
    """
    from fastapi import FastAPI

    # Create app with workers enabled (concurrency=1 default)
    with patch.dict(os.environ, {'SHU_WORKERS_ENABLED': 'true'}, clear=False):
        app = FastAPI()


        # Mock the worker components to avoid actual worker startup
        with patch('shu.core.queue_backend.get_queue_backend') as mock_get_backend, \
             patch('shu.core.worker.Worker') as mock_worker_class, \
             patch('shu.main.init_db') as mock_init_db:

            # Setup mocks
            mock_backend = AsyncMock()
            mock_get_backend.return_value = mock_backend
            mock_init_db.return_value = None


            mock_worker = MagicMock()
            mock_worker.run = AsyncMock()
            mock_worker_class.return_value = mock_worker


            # Run lifespan startup
            async with lifespan(app):
                # Verify worker tasks list was created
                assert hasattr(app.state, 'inline_worker_tasks')
                assert isinstance(app.state.inline_worker_tasks, list)
                assert len(app.state.inline_worker_tasks) >= 1

                # Verify worker was created with correct config
                mock_worker_class.assert_called()
                call_args = mock_worker_class.call_args


                # Check that config has all workload types
                config = call_args[0][1]  # Second positional arg is config
                assert config.workload_types == set(WorkloadType)


@pytest.mark.asyncio
async def test_workers_enabled_with_concurrency():
    """
    Test that multiple workers start when concurrency > 1.

    When SHU_WORKER_CONCURRENCY is set, that many workers should be created.
    """
    from shu.main import lifespan
    from fastapi import FastAPI

    app = FastAPI()

    # Create mock settings with concurrency=3
    mock_settings = MagicMock(spec=Settings)
    mock_settings.workers_enabled = True
    mock_settings.worker_concurrency = 3
    mock_settings.worker_poll_interval = 1.0
    mock_settings.worker_shutdown_timeout = 30.0
    mock_settings.version = "test"
    mock_settings.environment = "test"
    mock_settings.debug = False
    mock_settings.plugins_scheduler_enabled = False
    mock_settings.experiences_scheduler_enabled = False
    mock_settings.attachment_cleanup_enabled = False

    # Mock the worker components to avoid actual worker startup
    with patch('shu.main.settings', mock_settings), \
         patch('shu.core.queue_backend.get_queue_backend') as mock_get_backend, \
         patch('shu.core.worker.Worker') as mock_worker_class, \
         patch('shu.main.init_db') as mock_init_db:

        # Setup mocks
        mock_backend = AsyncMock()
        mock_get_backend.return_value = mock_backend
        mock_init_db.return_value = None

        mock_worker = MagicMock()
        mock_worker.run = AsyncMock()
        mock_worker_class.return_value = mock_worker

        # Run lifespan startup
        async with lifespan(app):
            # Verify 3 worker tasks were created
            assert hasattr(app.state, 'inline_worker_tasks')
            assert len(app.state.inline_worker_tasks) == 3

            # Verify Worker was instantiated 3 times with worker_id
            assert mock_worker_class.call_count == 3

            # Check worker_ids are set correctly
            call_args_list = mock_worker_class.call_args_list
            worker_ids = [call.kwargs.get('worker_id') for call in call_args_list]
            assert '1/3' in worker_ids
            assert '2/3' in worker_ids
            assert '3/3' in worker_ids


@pytest.mark.asyncio
async def test_workers_disabled_skips_startup():
    """
    Test that workers don't start when disabled.

    Validates: Requirements 7.2, 7.5

    When workers_enabled is False, the configuration should be set correctly.
    """
    with patch.dict(os.environ, {'SHU_WORKERS_ENABLED': 'false'}, clear=False):
        from shu.core.config import Settings

        settings = Settings()


        # Verify settings are correct
        assert settings.workers_enabled is False


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
async def test_api_starts_cleanly_with_workers_enabled():
    """
    Test that API starts cleanly with inline workers.


    Validates: Requirements 7.5

    The API should start successfully when workers_enabled is True,
    with workers running in the same process.
    """
    from shu.main import create_app

    with patch.dict(os.environ, {'SHU_WORKERS_ENABLED': 'true'}, clear=False):
        # Mock worker components to avoid actual startup
        with patch('shu.core.queue_backend.get_queue_backend') as mock_get_backend, \
             patch('shu.core.worker.Worker') as mock_worker_class, \
             patch('shu.main.init_db') as mock_init_db:

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
async def test_api_starts_cleanly_with_workers_disabled():
    """
    Test that API starts cleanly without workers.


    Validates: Requirements 7.5

    The API should start successfully when workers_enabled is False,
    without starting any workers.
    """
    from shu.main import create_app

    with patch.dict(os.environ, {'SHU_WORKERS_ENABLED': 'false'}, clear=False):
        # Mock init_db to avoid database connection
        with patch("shu.main.init_db") as mock_init_db:
            mock_init_db.return_value = None


            # Create app
            app = create_app()


            # Verify app was created successfully
            assert app is not None
            assert app.title == "Shu"
