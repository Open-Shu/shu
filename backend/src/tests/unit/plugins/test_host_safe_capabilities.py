"""Tests for safe capability methods.

These tests verify the safe methods on host capabilities that plugins can use
to avoid try/except blocks for common error cases.

Note: We import directly from the module files (not through the package __init__.py)
to avoid circular import issues with other host capabilities that depend on
services and database modules.
"""

import importlib.util
import sys
from pathlib import Path

import pytest


def _import_module_directly(module_name: str, file_path: str):
    """Import a module directly from its file path, bypassing package __init__.py."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    # Temporarily add the module to sys.modules to handle relative imports
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# Get the path to the host capability modules
_host_dir = Path(__file__).parent.parent.parent.parent / "shu" / "plugins" / "host"

# Import base first (needed by other modules)
_base_module = _import_module_directly(
    "shu.plugins.host.base",
    str(_host_dir / "base.py")
)

# Import log_capability with base available
_log_module = _import_module_directly(
    "shu.plugins.host.log_capability",
    str(_host_dir / "log_capability.py")
)
LogCapability = _log_module.LogCapability

# Import utils_capability with base available
_utils_module = _import_module_directly(
    "shu.plugins.host.utils_capability",
    str(_host_dir / "utils_capability.py")
)
UtilsCapability = _utils_module.UtilsCapability


class TestLogCapability:
    """Tests for LogCapability."""

    @pytest.fixture
    def log(self):
        return LogCapability(plugin_name="test-plugin", user_id="user-123")

    def test_log_capability_immutable(self, log):
        """LogCapability is immutable."""
        with pytest.raises(AttributeError):
            log._plugin_name = "hacked"

    def test_make_extra_includes_plugin_context(self, log):
        """_make_extra includes plugin_name and user_id."""
        extra = log._make_extra()
        assert extra["plugin_name"] == "test-plugin"
        assert extra["user_id"] == "user-123"

    def test_make_extra_merges_custom_context(self, log):
        """_make_extra merges custom extra dict."""
        extra = log._make_extra({"custom_key": "custom_value"})
        assert extra["custom_key"] == "custom_value"
        assert extra["plugin_name"] == "test-plugin"

    def test_make_extra_includes_operation(self):
        """_make_extra includes operation when provided."""
        log = LogCapability(plugin_name="test-plugin", user_id="user-123", operation="sync")
        extra = log._make_extra()
        assert extra["operation"] == "sync"

    def test_make_extra_prevents_spoofing_plugin_name(self, log):
        """_make_extra prevents plugins from spoofing plugin_name."""
        extra = log._make_extra({"plugin_name": "malicious-plugin"})
        assert extra["plugin_name"] == "test-plugin"

    def test_make_extra_prevents_spoofing_user_id(self, log):
        """_make_extra prevents plugins from spoofing user_id."""
        extra = log._make_extra({"user_id": "admin-user"})
        assert extra["user_id"] == "user-123"

    def test_make_extra_prevents_spoofing_operation(self):
        """_make_extra prevents plugins from spoofing operation."""
        log = LogCapability(plugin_name="test-plugin", user_id="user-123", operation="sync")
        extra = log._make_extra({"operation": "admin-action"})
        assert extra["operation"] == "sync"

    def test_info_does_not_raise(self, log):
        """info() does not raise an exception."""
        log.info("Test message")

    def test_warning_does_not_raise(self, log):
        """warning() does not raise an exception."""
        log.warning("Warning message")

    def test_error_does_not_raise(self, log):
        """error() does not raise an exception."""
        log.error("Error message")

    def test_exception_does_not_raise(self, log):
        """exception() does not raise an exception."""
        log.exception("Exception message")

    def test_debug_does_not_raise(self, log):
        """debug() does not raise an exception."""
        log.debug("Debug message")


class TestUtilsCapabilityMapSafe:
    """Tests for UtilsCapability.map_safe."""

    @pytest.fixture
    def utils(self):
        return UtilsCapability(plugin_name="test-plugin", user_id="user-123")

    @pytest.mark.asyncio
    async def test_map_safe_all_succeed(self, utils):
        """map_safe returns all results when all items succeed."""
        async def double(x):
            return x * 2

        results, errors = await utils.map_safe([1, 2, 3], double)
        assert results == [2, 4, 6]
        assert errors == []

    @pytest.mark.asyncio
    async def test_map_safe_some_fail(self, utils):
        """map_safe collects errors for failed items."""
        async def maybe_fail(x):
            if x == 2:
                raise ValueError("Cannot process 2")
            return x * 2

        results, errors = await utils.map_safe([1, 2, 3], maybe_fail)
        assert results == [2, 6]
        assert len(errors) == 1
        assert errors[0][0] == 2
        assert isinstance(errors[0][1], ValueError)

    @pytest.mark.asyncio
    async def test_map_safe_all_fail(self, utils):
        """map_safe handles when all items fail."""
        async def always_fail(x):
            raise RuntimeError(f"Failed on {x}")

        results, errors = await utils.map_safe([1, 2, 3], always_fail)
        assert results == []
        assert len(errors) == 3

    @pytest.mark.asyncio
    async def test_map_safe_max_errors(self, utils):
        """map_safe stops after max_errors is reached."""
        async def always_fail(x):
            raise RuntimeError(f"Failed on {x}")

        results, errors = await utils.map_safe([1, 2, 3, 4, 5], always_fail, max_errors=2)
        assert results == []
        assert len(errors) == 2
        # Should have stopped after 2 errors

    @pytest.mark.asyncio
    async def test_map_safe_empty_list(self, utils):
        """map_safe handles empty list."""
        async def double(x):
            return x * 2

        results, errors = await utils.map_safe([], double)
        assert results == []
        assert errors == []

    @pytest.mark.asyncio
    async def test_map_safe_max_errors_zero_raises(self, utils):
        """map_safe raises ValueError when max_errors=0."""
        async def double(x):
            return x * 2

        with pytest.raises(ValueError, match="max_errors must be None or >= 1"):
            await utils.map_safe([1, 2, 3], double, max_errors=0)

    @pytest.mark.asyncio
    async def test_map_safe_max_errors_negative_raises(self, utils):
        """map_safe raises ValueError when max_errors is negative."""
        async def double(x):
            return x * 2

        with pytest.raises(ValueError, match="max_errors must be None or >= 1"):
            await utils.map_safe([1, 2, 3], double, max_errors=-1)


class TestUtilsCapabilityFilterSafe:
    """Tests for UtilsCapability.filter_safe."""

    @pytest.fixture
    def utils(self):
        return UtilsCapability(plugin_name="test-plugin", user_id="user-123")

    @pytest.mark.asyncio
    async def test_filter_safe_all_pass(self, utils):
        """filter_safe returns all items when all pass predicate."""
        async def is_positive(x):
            return x > 0

        kept, errors = await utils.filter_safe([1, 2, 3], is_positive)
        assert kept == [1, 2, 3]
        assert errors == []

    @pytest.mark.asyncio
    async def test_filter_safe_some_fail(self, utils):
        """filter_safe filters items and collects predicate errors."""
        async def maybe_fail(x):
            if x == 2:
                raise ValueError("Cannot check 2")
            return x > 1

        kept, errors = await utils.filter_safe([1, 2, 3], maybe_fail)
        assert kept == [3]
        assert len(errors) == 1
        assert errors[0][0] == 2

    @pytest.mark.asyncio
    async def test_filter_safe_none_pass(self, utils):
        """filter_safe returns empty when no items pass."""
        async def always_false(x):
            return False

        kept, errors = await utils.filter_safe([1, 2, 3], always_false)
        assert kept == []
        assert errors == []

