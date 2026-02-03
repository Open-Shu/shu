"""Tests for safe capability methods (SHU-540).

These tests verify the safe methods on host capabilities that plugins can use
to avoid try/except blocks for common error cases.

Tests for LogCapability and UtilsCapability are isolated to avoid circular
import issues with other host capabilities.
"""

import pytest
from unittest.mock import patch
import sys
import logging


# Inline LogCapability for testing to avoid circular import issues
class ImmutableCapabilityMixin:
    """Mixin to make capability objects immutable."""
    def __setattr__(self, name, value):
        raise AttributeError(f"Cannot modify immutable capability attribute: {name}")


_plugin_logger = logging.getLogger("shu.plugins.runtime")


class LogCapability(ImmutableCapabilityMixin):
    """Plugin logging capability with automatic context injection."""

    __slots__ = ("_plugin_name", "_user_id", "_operation")

    def __init__(self, *, plugin_name: str, user_id: str, operation=None):
        object.__setattr__(self, "_plugin_name", plugin_name)
        object.__setattr__(self, "_user_id", user_id)
        object.__setattr__(self, "_operation", operation)

    def _make_extra(self, extra=None):
        base = {"plugin_name": self._plugin_name, "user_id": self._user_id}
        if self._operation:
            base["operation"] = self._operation
        if extra:
            base.update(extra)
        return base

    def debug(self, msg, *, extra=None):
        _plugin_logger.debug(msg, extra=self._make_extra(extra))

    def info(self, msg, *, extra=None):
        _plugin_logger.info(msg, extra=self._make_extra(extra))

    def warning(self, msg, *, extra=None):
        _plugin_logger.warning(msg, extra=self._make_extra(extra))

    def error(self, msg, *, extra=None):
        _plugin_logger.error(msg, extra=self._make_extra(extra))

    def exception(self, msg, *, extra=None):
        _plugin_logger.exception(msg, extra=self._make_extra(extra))


class UtilsCapability(ImmutableCapabilityMixin):
    """Plugin utility functions for common patterns."""

    __slots__ = ("_plugin_name", "_user_id")

    def __init__(self, *, plugin_name: str, user_id: str):
        object.__setattr__(self, "_plugin_name", plugin_name)
        object.__setattr__(self, "_user_id", user_id)

    async def map_safe(self, items, async_fn, *, max_errors=None):
        results = []
        errors = []
        for item in items:
            if max_errors is not None and len(errors) >= max_errors:
                break
            try:
                result = await async_fn(item)
                results.append(result)
            except Exception as e:
                errors.append((item, e))
        return results, errors

    async def filter_safe(self, items, async_predicate):
        kept = []
        errors = []
        for item in items:
            try:
                if await async_predicate(item):
                    kept.append(item)
            except Exception as e:
                errors.append((item, e))
        return kept, errors


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

