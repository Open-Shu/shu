"""Unit tests for setup_logging() handler installation under hosted vs OSS profiles."""

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from shu.core import logging as shu_logging
from shu.core.logging import ManagedFileHandler, setup_logging


@pytest.fixture(autouse=True)
def _reset_logging_state() -> None:
    """Reset module-level globals and root logger handlers around each test.

    setup_logging() mutates module globals and the root logger; tests would
    otherwise leak handler state into each other.
    """
    shu_logging._LOGGING_CONFIGURED = False
    shu_logging._managed_file_handler = None
    saved_handlers = logging.root.handlers.copy()
    saved_level = logging.root.level
    try:
        logging.root.handlers.clear()
        yield
    finally:
        shu_logging._LOGGING_CONFIGURED = False
        shu_logging._managed_file_handler = None
        logging.root.handlers.clear()
        for h in saved_handlers:
            logging.root.addHandler(h)
        logging.root.setLevel(saved_level)


def _stub_settings(log_dir: str) -> object:
    class _S:
        pass

    s = _S()
    s.environment = "test"
    s.log_level = "INFO"
    s.log_format = "json"
    s.log_dir = log_dir
    s.log_retention_days = 14
    return s


class TestSetupLoggingHostedProfile:
    """log_dir='' must skip the ManagedFileHandler and install only StreamHandler."""

    def test_empty_log_dir_installs_only_stream_handler(self) -> None:
        with patch("shu.core.logging.get_settings_instance", return_value=_stub_settings("")):
            setup_logging()

        root = logging.root
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0], logging.StreamHandler)
        assert not isinstance(root.handlers[0], ManagedFileHandler)
        assert shu_logging.get_managed_file_handler() is None

    def test_empty_log_dir_log_maintenance_consumers_noop(self) -> None:
        with patch("shu.core.logging.get_settings_instance", return_value=_stub_settings("")):
            setup_logging()

        # run_log_cleanup() and rotate-checks should silently no-op when no
        # ManagedFileHandler is installed — the explicit None-guard contract
        # used by scheduler_service.LogMaintenanceSource and worker._run_log_maintenance.
        assert shu_logging.get_managed_file_handler() is None
        shu_logging.run_log_cleanup()


class TestSetupLoggingOssProfile:
    """log_dir pointing at a real path must install both handlers (regression test)."""

    def test_populated_log_dir_installs_file_and_stream_handlers(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        with patch("shu.core.logging.get_settings_instance", return_value=_stub_settings(str(log_dir))):
            setup_logging()

        root = logging.root
        handler_types = {type(h) for h in root.handlers}
        assert ManagedFileHandler in handler_types
        assert any(
            isinstance(h, logging.StreamHandler) and not isinstance(h, ManagedFileHandler) for h in root.handlers
        )
        assert shu_logging.get_managed_file_handler() is not None
        assert log_dir.is_dir()
