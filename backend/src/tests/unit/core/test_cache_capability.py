"""
Property-based tests for CacheCapability.

Feature: unified-cache-interface
"""

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from shu.core.cache_backend import (
    CacheConnectionError,
    CacheOperationError,
    InMemoryCacheBackend,
)

# Import cache_capability module directly to avoid circular import through __init__.py
# First import the base module which has no problematic dependencies
_base_spec = importlib.util.spec_from_file_location(
    "shu.plugins.host.base",
    Path(__file__).parent.parent.parent.parent / "shu" / "plugins" / "host" / "base.py",
)
_base_module = importlib.util.module_from_spec(_base_spec)
sys.modules["shu.plugins.host.base"] = _base_module
_base_spec.loader.exec_module(_base_module)

# Now import cache_capability
_cache_spec = importlib.util.spec_from_file_location(
    "shu.plugins.host.cache_capability",
    Path(__file__).parent.parent.parent.parent / "shu" / "plugins" / "host" / "cache_capability.py",
)
_cache_module = importlib.util.module_from_spec(_cache_spec)
sys.modules["shu.plugins.host.cache_capability"] = _cache_module
_cache_spec.loader.exec_module(_cache_module)

CacheCapability = _cache_module.CacheCapability


# Test Strategies
plugin_name_strategy = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz_0123456789"),
    min_size=1,
    max_size=50,
)
user_id_strategy = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789-_"),
    min_size=1,
    max_size=50,
)
cache_key_strategy = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters=("\x00",)),
    min_size=1,
    max_size=100,
)
json_value_strategy = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-1000000, max_value=1000000),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(min_size=0, max_size=100),
    st.lists(st.integers(min_value=-100, max_value=100), max_size=10),
    st.dictionaries(
        keys=st.text(min_size=1, max_size=20),
        values=st.one_of(st.integers(), st.text(max_size=20), st.booleans()),
        max_size=5,
    ),
)


class MockCacheBackend:
    """Mock CacheBackend that records all operations."""

    def __init__(self):
        self._data: dict[str, str] = {}
        self._operations: list = []
        self._should_fail: bool = False
        self._fail_exception: Exception | None = None

    def set_should_fail(self, should_fail: bool, exception: Exception | None = None):
        self._should_fail = should_fail
        self._fail_exception = exception or CacheConnectionError("Mock error")

    async def get(self, key: str) -> str | None:
        self._operations.append(("get", key))
        if self._should_fail:
            raise self._fail_exception
        return self._data.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> bool:
        self._operations.append(("set", key, value, ttl_seconds))
        if self._should_fail:
            raise self._fail_exception
        self._data[key] = value
        return True

    async def delete(self, key: str) -> bool:
        self._operations.append(("delete", key))
        if self._should_fail:
            raise self._fail_exception
        if key in self._data:
            del self._data[key]
            return True
        return False

    async def exists(self, key: str) -> bool:
        self._operations.append(("exists", key))
        if self._should_fail:
            raise self._fail_exception
        return key in self._data

    async def expire(self, key: str, ttl_seconds: int) -> bool:
        self._operations.append(("expire", key, ttl_seconds))
        if self._should_fail:
            raise self._fail_exception
        return key in self._data

    async def incr(self, key: str, amount: int = 1) -> int:
        self._operations.append(("incr", key, amount))
        if self._should_fail:
            raise self._fail_exception
        current = int(self._data.get(key, "0"))
        new_value = current + amount
        self._data[key] = str(new_value)
        return new_value

    async def decr(self, key: str, amount: int = 1) -> int:
        self._operations.append(("decr", key, amount))
        if self._should_fail:
            raise self._fail_exception
        current = int(self._data.get(key, "0"))
        new_value = current - amount
        self._data[key] = str(new_value)
        return new_value

    def get_last_operation(self):
        return self._operations[-1] if self._operations else None

    def get_operations(self):
        return self._operations.copy()

    def clear_operations(self):
        self._operations.clear()


@pytest.fixture
def mock_backend() -> MockCacheBackend:
    return MockCacheBackend()


@pytest.fixture
def inmemory_backend() -> InMemoryCacheBackend:
    return InMemoryCacheBackend(cleanup_interval_seconds=0)


class TestProperty7NamespaceKeyFormatting:
    """Property 7: Namespace key formatting. **Validates: Requirements 5.2**"""

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        plugin_name=plugin_name_strategy,
        user_id=user_id_strategy,
        key=cache_key_strategy,
        value=json_value_strategy,
    )
    async def test_set_uses_correct_namespace_format(self, plugin_name: str, user_id: str, key: str, value: Any):
        """Property test: set() uses correct namespace format. **Validates: Requirements 5.2**"""
        mock_backend = MockCacheBackend()
        capability = CacheCapability(plugin_name=plugin_name, user_id=user_id, backend=mock_backend)
        await capability.set(key, value)
        expected_key = f"tool_cache:{plugin_name}:{user_id}:{key}"
        last_op = mock_backend.get_last_operation()
        assert last_op is not None, "No operation was recorded"
        assert last_op[0] == "set", f"Expected 'set' operation, got {last_op[0]}"
        assert last_op[1] == expected_key, f"Expected namespaced key '{expected_key}', got '{last_op[1]}'"

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(plugin_name=plugin_name_strategy, user_id=user_id_strategy, key=cache_key_strategy)
    async def test_get_uses_correct_namespace_format(self, plugin_name: str, user_id: str, key: str):
        """Property test: get() uses correct namespace format. **Validates: Requirements 5.2**"""
        mock_backend = MockCacheBackend()
        capability = CacheCapability(plugin_name=plugin_name, user_id=user_id, backend=mock_backend)
        await capability.get(key)
        expected_key = f"tool_cache:{plugin_name}:{user_id}:{key}"
        last_op = mock_backend.get_last_operation()
        assert last_op is not None, "No operation was recorded"
        assert last_op[0] == "get", f"Expected 'get' operation, got {last_op[0]}"
        assert last_op[1] == expected_key, f"Expected namespaced key '{expected_key}', got '{last_op[1]}'"

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(plugin_name=plugin_name_strategy, user_id=user_id_strategy, key=cache_key_strategy)
    async def test_delete_uses_correct_namespace_format(self, plugin_name: str, user_id: str, key: str):
        """Property test: delete() uses correct namespace format. **Validates: Requirements 5.2**"""
        mock_backend = MockCacheBackend()
        capability = CacheCapability(plugin_name=plugin_name, user_id=user_id, backend=mock_backend)
        await capability.delete(key)
        expected_key = f"tool_cache:{plugin_name}:{user_id}:{key}"
        last_op = mock_backend.get_last_operation()
        assert last_op is not None, "No operation was recorded"
        assert last_op[0] == "delete", f"Expected 'delete' operation, got {last_op[0]}"
        assert last_op[1] == expected_key, f"Expected namespaced key '{expected_key}', got '{last_op[1]}'"

    @pytest.mark.asyncio
    async def test_namespace_isolation_between_plugins(self, mock_backend: MockCacheBackend):
        """Unit test: Different plugins have isolated namespaces."""
        cap1 = CacheCapability(plugin_name="plugin_a", user_id="user123", backend=mock_backend)
        cap2 = CacheCapability(plugin_name="plugin_b", user_id="user123", backend=mock_backend)
        await cap1.set("shared_key", "value_a")
        await cap2.set("shared_key", "value_b")
        ops = mock_backend.get_operations()
        assert len(ops) == 2
        assert ops[0][1] == "tool_cache:plugin_a:user123:shared_key"
        assert ops[1][1] == "tool_cache:plugin_b:user123:shared_key"

    @pytest.mark.asyncio
    async def test_namespace_isolation_between_users(self, mock_backend: MockCacheBackend):
        """Unit test: Different users have isolated namespaces."""
        cap1 = CacheCapability(plugin_name="my_plugin", user_id="user_a", backend=mock_backend)
        cap2 = CacheCapability(plugin_name="my_plugin", user_id="user_b", backend=mock_backend)
        await cap1.set("user_data", {"name": "Alice"})
        await cap2.set("user_data", {"name": "Bob"})
        ops = mock_backend.get_operations()
        assert len(ops) == 2
        assert ops[0][1] == "tool_cache:my_plugin:user_a:user_data"
        assert ops[1][1] == "tool_cache:my_plugin:user_b:user_data"


class TestProperty8GracefulErrorHandling:
    """Property 8: CacheCapability graceful error handling. **Validates: Requirements 5.4**"""

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        plugin_name=plugin_name_strategy,
        user_id=user_id_strategy,
        key=cache_key_strategy,
        value=json_value_strategy,
    )
    async def test_set_handles_backend_errors_gracefully(self, plugin_name: str, user_id: str, key: str, value: Any):
        """Property test: set() does not raise exceptions on backend errors. **Validates: Requirements 5.4**"""
        mock_backend = MockCacheBackend()
        mock_backend.set_should_fail(True, CacheConnectionError("Connection failed"))
        capability = CacheCapability(plugin_name=plugin_name, user_id=user_id, backend=mock_backend)
        try:
            await capability.set(key, value)
        except Exception as e:
            pytest.fail(f"set() raised an exception: {type(e).__name__}: {e}")

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(plugin_name=plugin_name_strategy, user_id=user_id_strategy, key=cache_key_strategy)
    async def test_get_handles_backend_errors_gracefully(self, plugin_name: str, user_id: str, key: str):
        """Property test: get() returns None on backend errors. **Validates: Requirements 5.4**"""
        mock_backend = MockCacheBackend()
        mock_backend.set_should_fail(True, CacheConnectionError("Connection failed"))
        capability = CacheCapability(plugin_name=plugin_name, user_id=user_id, backend=mock_backend)
        try:
            result = await capability.get(key)
            assert result is None, f"Expected None on error, got {result!r}"
        except Exception as e:
            pytest.fail(f"get() raised an exception: {type(e).__name__}: {e}")

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(plugin_name=plugin_name_strategy, user_id=user_id_strategy, key=cache_key_strategy)
    async def test_delete_handles_backend_errors_gracefully(self, plugin_name: str, user_id: str, key: str):
        """Property test: delete() does not raise exceptions on backend errors. **Validates: Requirements 5.4**"""
        mock_backend = MockCacheBackend()
        mock_backend.set_should_fail(True, CacheConnectionError("Connection failed"))
        capability = CacheCapability(plugin_name=plugin_name, user_id=user_id, backend=mock_backend)
        try:
            await capability.delete(key)
        except Exception as e:
            pytest.fail(f"delete() raised an exception: {type(e).__name__}: {e}")

    @pytest.mark.asyncio
    async def test_set_handles_operation_error(self, mock_backend: MockCacheBackend):
        """Unit test: set() handles CacheOperationError gracefully."""
        mock_backend.set_should_fail(True, CacheOperationError("Operation failed"))
        capability = CacheCapability(plugin_name="test_plugin", user_id="test_user", backend=mock_backend)
        await capability.set("key", "value")

    @pytest.mark.asyncio
    async def test_get_handles_json_decode_error(self, mock_backend: MockCacheBackend):
        """Unit test: get() handles JSON decode errors gracefully."""
        mock_backend._data["tool_cache:test_plugin:test_user:key"] = "not valid json {"
        capability = CacheCapability(plugin_name="test_plugin", user_id="test_user", backend=mock_backend)
        result = await capability.get("key")
        assert result is None

    @pytest.mark.asyncio
    async def test_handles_various_exception_types(self, mock_backend: MockCacheBackend):
        """Unit test: CacheCapability handles various exception types."""
        capability = CacheCapability(plugin_name="test_plugin", user_id="test_user", backend=mock_backend)
        exception_types = [
            RuntimeError("Runtime error"),
            ValueError("Value error"),
            TimeoutError("Timeout"),
            ConnectionError("Connection error"),
            Exception("Generic"),
        ]
        for exc in exception_types:
            mock_backend.set_should_fail(True, exc)
            await capability.set("key", "value")
            result = await capability.get("key")
            assert result is None
            await capability.delete("key")


class TestCacheCapabilityBasicFunctionality:
    """Unit tests for basic CacheCapability functionality."""

    @pytest.mark.asyncio
    async def test_set_and_get_round_trip(self, inmemory_backend: InMemoryCacheBackend):
        """Unit test: set then get returns the same value."""
        capability = CacheCapability(plugin_name="test_plugin", user_id="test_user", backend=inmemory_backend)
        test_value = {"key": "value", "number": 42, "list": [1, 2, 3]}
        await capability.set("test_key", test_value)
        result = await capability.get("test_key")
        assert result == test_value

    @pytest.mark.asyncio
    async def test_get_returns_none_for_missing_key(self, inmemory_backend: InMemoryCacheBackend):
        """Unit test: get returns None for non-existent key."""
        capability = CacheCapability(plugin_name="test_plugin", user_id="test_user", backend=inmemory_backend)
        result = await capability.get("nonexistent_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_removes_key(self, inmemory_backend: InMemoryCacheBackend):
        """Unit test: delete removes the key from cache."""
        capability = CacheCapability(plugin_name="test_plugin", user_id="test_user", backend=inmemory_backend)
        await capability.set("key_to_delete", "value")
        assert await capability.get("key_to_delete") == "value"
        await capability.delete("key_to_delete")
        assert await capability.get("key_to_delete") is None

    @pytest.mark.asyncio
    async def test_ttl_is_passed_to_backend(self, mock_backend: MockCacheBackend):
        """Unit test: TTL is correctly passed to the backend."""
        capability = CacheCapability(plugin_name="test_plugin", user_id="test_user", backend=mock_backend)
        await capability.set("key", "value", ttl_seconds=600)
        last_op = mock_backend.get_last_operation()
        assert last_op[0] == "set"
        assert last_op[3] == 600

    @pytest.mark.asyncio
    async def test_default_ttl_is_300(self, mock_backend: MockCacheBackend):
        """Unit test: Default TTL is 300 seconds."""
        capability = CacheCapability(plugin_name="test_plugin", user_id="test_user", backend=mock_backend)
        await capability.set("key", "value")
        last_op = mock_backend.get_last_operation()
        assert last_op[0] == "set"
        assert last_op[3] == 300

    @pytest.mark.asyncio
    async def test_ttl_minimum_is_one_second(self, mock_backend: MockCacheBackend):
        """Unit test: TTL is clamped to minimum of 1 second."""
        capability = CacheCapability(plugin_name="test_plugin", user_id="test_user", backend=mock_backend)
        await capability.set("key", "value", ttl_seconds=0)
        last_op = mock_backend.get_last_operation()
        assert last_op[0] == "set"
        assert last_op[3] == 1
        mock_backend.clear_operations()
        await capability.set("key", "value", ttl_seconds=-10)
        last_op = mock_backend.get_last_operation()
        assert last_op[3] == 1

    @pytest.mark.asyncio
    async def test_immutability(self, inmemory_backend: InMemoryCacheBackend):
        """Unit test: CacheCapability attributes cannot be modified."""
        capability = CacheCapability(plugin_name="original_plugin", user_id="original_user", backend=inmemory_backend)
        with pytest.raises(AttributeError):
            capability._plugin_name = "hacked_plugin"
        with pytest.raises(AttributeError):
            capability._user_id = "hacked_user"

    @pytest.mark.asyncio
    async def test_various_value_types(self, inmemory_backend: InMemoryCacheBackend):
        """Unit test: CacheCapability handles various JSON-serializable types."""
        capability = CacheCapability(plugin_name="test_plugin", user_id="test_user", backend=inmemory_backend)
        test_cases = [
            ("string", "hello world"),
            ("integer", 42),
            ("float", 3.14),
            ("boolean", True),
            ("null", None),
            ("list", [1, 2, 3, "four"]),
            ("dict", {"nested": {"key": "value"}}),
            ("empty_list", []),
            ("empty_dict", {}),
        ]
        for key, value in test_cases:
            await capability.set(key, value)
            result = await capability.get(key)
            assert result == value, f"Failed for {key}: expected {value!r}, got {result!r}"
