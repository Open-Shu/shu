"""
Property-based tests for CacheBackend protocol.

These tests verify the correctness properties defined in the design document
for the unified cache interface.

Feature: unified-cache-interface
"""

import asyncio
import time
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from shu.core.cache_backend import CacheBackend, InMemoryCacheBackend, RedisCacheBackend


class MockRedisClient:
    """Mock Redis client for testing RedisCacheBackend.

    This mock implements the same interface as the real Redis client
    to allow testing RedisCacheBackend without a real Redis server.
    """

    def __init__(self):
        self._data: dict[str, Any] = {}
        self._expiry: dict[str, float] = {}

    async def get(self, key: str) -> str | None:
        """Get a value by key."""
        # Check expiration
        if key in self._expiry and time.time() > self._expiry[key]:
            del self._data[key]
            del self._expiry[key]
            return None
        return self._data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        """Set a key-value pair with optional expiration."""
        self._data[key] = value
        if ex:
            self._expiry[key] = time.time() + ex
        elif key in self._expiry:
            del self._expiry[key]
        return True

    async def setex(self, key: str, seconds: int, value: str) -> bool:
        """Set a key-value pair with expiration."""
        self._data[key] = value
        self._expiry[key] = time.time() + seconds
        return True

    async def delete(self, *keys: str) -> int:
        """Delete one or more keys."""
        deleted = 0
        for key in keys:
            if key in self._data:
                del self._data[key]
                deleted += 1
            if key in self._expiry:
                del self._expiry[key]
        return deleted

    async def exists(self, key: str) -> int:
        """Check if a key exists."""
        # Check expiration
        if key in self._expiry and time.time() > self._expiry[key]:
            del self._data[key]
            del self._expiry[key]
            return 0
        return 1 if key in self._data else 0

    async def expire(self, key: str, seconds: int) -> bool:
        """Set expiration on a key."""
        if key not in self._data:
            return False
        # Check if already expired
        if key in self._expiry and time.time() > self._expiry[key]:
            del self._data[key]
            del self._expiry[key]
            return False
        self._expiry[key] = time.time() + seconds
        return True

    async def incr(self, key: str) -> int:
        """Increment a value by 1."""
        return await self.incrby(key, 1)

    async def incrby(self, key: str, amount: int) -> int:
        """Increment a value by amount."""
        # Check expiration first
        if key in self._expiry and time.time() > self._expiry[key]:
            del self._data[key]
            del self._expiry[key]

        current = self._data.get(key, "0")
        if isinstance(current, int):
            current = str(current)
        try:
            new_value = int(current) + amount
        except ValueError:
            raise ValueError("ERR value is not an integer or out of range")
        self._data[key] = str(new_value)
        return new_value

    async def decr(self, key: str) -> int:
        """Decrement a value by 1."""
        return await self.decrby(key, 1)

    async def decrby(self, key: str, amount: int) -> int:
        """Decrement a value by amount."""
        # Check expiration first
        if key in self._expiry and time.time() > self._expiry[key]:
            del self._data[key]
            del self._expiry[key]

        current = self._data.get(key, "0")
        if isinstance(current, int):
            current = str(current)
        try:
            new_value = int(current) - amount
        except ValueError:
            raise ValueError("ERR value is not an integer or out of range")
        self._data[key] = str(new_value)
        return new_value


# Strategy for generating valid cache keys
# Keys should be non-empty strings without null bytes
cache_key_strategy = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs",),  # Exclude surrogate characters
        blacklist_characters=("\x00",),  # Exclude null bytes
    ),
    min_size=1,
    max_size=200,
)


class TestProperty1GetReturnsNoneForMissingKeys:
    """
    Property 1: Get returns None for missing keys

    *For any* cache backend and any key that has not been set,
    calling `get(key)` SHALL return `None`.

    **Validates: Requirements 1.3**

    Feature: unified-cache-interface, Property 1: Get returns None for missing keys
    """

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(key=cache_key_strategy)
    async def test_get_returns_none_for_missing_keys(self, key: str):
        """
        Property test: For any key that has not been set, get() returns None.

        Feature: unified-cache-interface, Property 1: Get returns None for missing keys
        **Validates: Requirements 1.3**
        """
        # Create a fresh backend for each test case
        backend = InMemoryCacheBackend(cleanup_interval_seconds=0)

        # The key has never been set, so get should return None
        result = await backend.get(key)

        assert result is None, f"Expected None for missing key '{key}', got {result!r}"

    @pytest.mark.asyncio
    async def test_get_returns_none_for_empty_backend(self, inmemory_backend: InMemoryCacheBackend):
        """Unit test: get() returns None on an empty backend."""
        result = await inmemory_backend.get("any_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_returns_none_after_delete(self, inmemory_backend: InMemoryCacheBackend):
        """Unit test: get() returns None after a key is deleted."""
        # Set a key
        await inmemory_backend.set("test_key", "test_value")

        # Verify it exists
        assert await inmemory_backend.get("test_key") == "test_value"

        # Delete it
        await inmemory_backend.delete("test_key")

        # Now get should return None
        result = await inmemory_backend.get("test_key")
        assert result is None


# Strategy for generating valid cache values
cache_value_strategy = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs",),  # Exclude surrogate characters
        blacklist_characters=("\x00",),  # Exclude null bytes
    ),
    min_size=0,
    max_size=1000,
)


@pytest.fixture
def inmemory_backend() -> InMemoryCacheBackend:
    """Provide a fresh InMemoryCacheBackend for each test."""
    return InMemoryCacheBackend(cleanup_interval_seconds=0)  # Disable periodic cleanup for tests


@pytest.fixture
def redis_backend() -> RedisCacheBackend:
    """Provide a fresh RedisCacheBackend with mock client for each test."""
    mock_client = MockRedisClient()
    return RedisCacheBackend(mock_client)


@pytest.fixture(params=["inmemory", "redis"])
def cache_backend(request) -> CacheBackend:
    """Parametrized fixture providing both backend implementations.

    This allows running the same tests against both InMemoryCacheBackend
    and RedisCacheBackend to verify backend substitutability.
    """
    if request.param == "inmemory":
        return InMemoryCacheBackend(cleanup_interval_seconds=0)
    mock_client = MockRedisClient()
    return RedisCacheBackend(mock_client)


class TestInMemoryCacheBackendProtocol:
    """Tests for InMemoryCacheBackend protocol compliance."""

    def test_inmemory_cache_backend_implements_protocol(self, inmemory_backend: InMemoryCacheBackend):
        """Verify that InMemoryCacheBackend implements CacheBackend protocol."""
        assert isinstance(inmemory_backend, CacheBackend)


class TestRedisCacheBackendProtocol:
    """Tests for RedisCacheBackend protocol compliance."""

    def test_redis_cache_backend_implements_protocol(self, redis_backend: RedisCacheBackend):
        """Verify that RedisCacheBackend implements CacheBackend protocol."""
        assert isinstance(redis_backend, CacheBackend)


class TestProperty2SetThenGetRoundTrip:
    """
    Property 2: Set-then-get round-trip consistency

    *For any* cache backend, any key, and any string value, if `set(key, value)`
    succeeds, then an immediate `get(key)` SHALL return the same value.

    **Validates: Requirements 9.1**

    Feature: unified-cache-interface, Property 2: Set-then-get round-trip consistency
    """

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(key=cache_key_strategy, value=cache_value_strategy)
    async def test_set_then_get_returns_same_value(self, key: str, value: str):
        """
        Property test: For any key and value, set then get returns the same value.

        Feature: unified-cache-interface, Property 2: Set-then-get round-trip consistency
        **Validates: Requirements 9.1**
        """
        backend = InMemoryCacheBackend(cleanup_interval_seconds=0)

        # Set the value
        result = await backend.set(key, value)
        assert result is True, f"set() should return True, got {result}"

        # Get should return the same value
        retrieved = await backend.get(key)
        assert retrieved == value, f"Expected {value!r}, got {retrieved!r}"

    @pytest.mark.asyncio
    async def test_set_then_get_with_ttl(self, inmemory_backend: InMemoryCacheBackend):
        """Unit test: set with TTL then immediate get returns the value."""
        await inmemory_backend.set("key", "value", ttl_seconds=300)
        result = await inmemory_backend.get("key")
        assert result == "value"

    @pytest.mark.asyncio
    async def test_set_overwrites_existing_value(self, inmemory_backend: InMemoryCacheBackend):
        """Unit test: set overwrites existing value."""
        await inmemory_backend.set("key", "value1")
        await inmemory_backend.set("key", "value2")
        result = await inmemory_backend.get("key")
        assert result == "value2"


class TestProperty3TTLExpiration:
    """
    Property 3: TTL expiration

    *For any* cache backend, any key set with a TTL, after the TTL duration
    has elapsed, `get(key)` SHALL return `None`.

    **Validates: Requirements 3.3, 9.3**

    Feature: unified-cache-interface, Property 3: TTL expiration
    """

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        key=cache_key_strategy,
        value=cache_value_strategy,
    )
    async def test_ttl_expiration(self, key: str, value: str):
        """
        Property test: For any key set with TTL, after TTL expires, get returns None.

        Note: We test the expiration logic by manipulating time internally rather
        than using sleep, to keep tests fast while still validating the property.

        Feature: unified-cache-interface, Property 3: TTL expiration
        **Validates: Requirements 3.3, 9.3**
        """
        import time

        backend = InMemoryCacheBackend(cleanup_interval_seconds=0)

        # Set with a TTL
        await backend.set(key, value, ttl_seconds=10)

        # Immediately after set, value should be retrievable
        result = await backend.get(key)
        assert result == value, f"Expected {value!r} immediately after set, got {result!r}"

        # Manually expire the entry by modifying the internal state
        # This tests the expiration logic without waiting
        with backend._lock:
            if key in backend._data:
                val, _ = backend._data[key]
                # Set expiry to the past
                backend._data[key] = (val, time.time() - 1)

        # After TTL, get should return None
        result = await backend.get(key)
        assert result is None, f"Expected None after TTL expiration, got {result!r}"

    @pytest.mark.asyncio
    async def test_ttl_expiration_with_real_time(self, inmemory_backend: InMemoryCacheBackend):
        """Unit test: TTL expiration works with real time passage."""
        await inmemory_backend.set("key", "value", ttl_seconds=1)

        # Immediately after set, value should be retrievable
        assert await inmemory_backend.get("key") == "value"

        # Wait for TTL to expire
        await asyncio.sleep(1.1)

        # After TTL, get should return None
        assert await inmemory_backend.get("key") is None

    @pytest.mark.asyncio
    async def test_ttl_expiration_exists_returns_false(self, inmemory_backend: InMemoryCacheBackend):
        """Unit test: exists() returns False after TTL expiration."""
        await inmemory_backend.set("key", "value", ttl_seconds=1)
        assert await inmemory_backend.exists("key") is True

        await asyncio.sleep(1.1)

        assert await inmemory_backend.exists("key") is False

    @pytest.mark.asyncio
    async def test_no_ttl_does_not_expire(self, inmemory_backend: InMemoryCacheBackend):
        """Unit test: keys without TTL do not expire."""
        await inmemory_backend.set("key", "value")  # No TTL

        # Should still exist after some time
        await asyncio.sleep(0.1)

        result = await inmemory_backend.get("key")
        assert result == "value"


class TestProperty4IncrDecrOnNonExistentKeys:
    """
    Property 4: Incr/decr on non-existent keys starts from zero

    *For any* cache backend and any key that does not exist, `incr(key)` SHALL
    return 1 and `decr(key)` SHALL return -1.

    **Validates: Requirements 2.6, 3.5**

    Feature: unified-cache-interface, Property 4: Incr/decr on non-existent keys starts from zero
    """

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(key=cache_key_strategy)
    async def test_incr_on_nonexistent_key_returns_one(self, key: str):
        """
        Property test: For any non-existent key, incr() returns 1.

        Feature: unified-cache-interface, Property 4: Incr/decr on non-existent keys starts from zero
        **Validates: Requirements 2.6, 3.5**
        """
        backend = InMemoryCacheBackend(cleanup_interval_seconds=0)

        # Key doesn't exist
        assert await backend.exists(key) is False

        # incr on non-existent key should return 1 (0 + 1)
        result = await backend.incr(key)
        assert result == 1, f"Expected 1 for incr on non-existent key, got {result}"

        # Key should now exist with value "1"
        value = await backend.get(key)
        assert value == "1", f"Expected '1' after incr, got {value!r}"

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(key=cache_key_strategy)
    async def test_decr_on_nonexistent_key_returns_negative_one(self, key: str):
        """
        Property test: For any non-existent key, decr() returns -1.

        Feature: unified-cache-interface, Property 4: Incr/decr on non-existent keys starts from zero
        **Validates: Requirements 2.6, 3.5**
        """
        backend = InMemoryCacheBackend(cleanup_interval_seconds=0)

        # Key doesn't exist
        assert await backend.exists(key) is False

        # decr on non-existent key should return -1 (0 - 1)
        result = await backend.decr(key)
        assert result == -1, f"Expected -1 for decr on non-existent key, got {result}"

        # Key should now exist with value "-1"
        value = await backend.get(key)
        assert value == "-1", f"Expected '-1' after decr, got {value!r}"

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(key=cache_key_strategy, amount=st.integers(min_value=1, max_value=1000))
    async def test_incr_with_custom_amount(self, key: str, amount: int):
        """
        Property test: incr with custom amount on non-existent key returns that amount.

        Feature: unified-cache-interface, Property 4: Incr/decr on non-existent keys starts from zero
        **Validates: Requirements 2.6, 3.5**
        """
        backend = InMemoryCacheBackend(cleanup_interval_seconds=0)

        result = await backend.incr(key, amount=amount)
        assert result == amount, f"Expected {amount} for incr with amount={amount}, got {result}"

    @pytest.mark.asyncio
    async def test_incr_on_existing_value(self, inmemory_backend: InMemoryCacheBackend):
        """Unit test: incr on existing numeric value increments correctly."""
        await inmemory_backend.set("counter", "10")

        result = await inmemory_backend.incr("counter")
        assert result == 11

        result = await inmemory_backend.incr("counter", amount=5)
        assert result == 16

    @pytest.mark.asyncio
    async def test_decr_on_existing_value(self, inmemory_backend: InMemoryCacheBackend):
        """Unit test: decr on existing numeric value decrements correctly."""
        await inmemory_backend.set("counter", "10")

        result = await inmemory_backend.decr("counter")
        assert result == 9

        result = await inmemory_backend.decr("counter", amount=5)
        assert result == 4


class TestProperty9ThreadSafeConcurrentOperations:
    """
    Property 9: Thread-safe concurrent operations

    *For any* InMemoryCacheBackend and any set of concurrent incr operations
    on the same key, the final value SHALL equal the sum of all increments.

    **Validates: Requirements 3.2**

    Feature: unified-cache-interface, Property 9: Thread-safe concurrent operations
    """

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        num_increments=st.integers(min_value=10, max_value=100),
        increment_amount=st.integers(min_value=1, max_value=10),
    )
    async def test_concurrent_incr_operations(self, num_increments: int, increment_amount: int):
        """
        Property test: Concurrent incr operations produce correct final sum.

        Feature: unified-cache-interface, Property 9: Thread-safe concurrent operations
        **Validates: Requirements 3.2**
        """
        import concurrent.futures

        backend = InMemoryCacheBackend(cleanup_interval_seconds=0)
        key = "concurrent_counter"

        # Function to run incr in a thread
        def do_incr():
            # We need to run the async function in a new event loop for each thread
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(backend.incr(key, amount=increment_amount))
            finally:
                loop.close()

        # Run concurrent increments using ThreadPoolExecutor
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(do_incr) for _ in range(num_increments)]
            concurrent.futures.wait(futures)

        # Final value should be num_increments * increment_amount
        expected = num_increments * increment_amount
        result = await backend.get(key)
        assert result == str(expected), f"Expected {expected}, got {result}"

    @pytest.mark.asyncio
    async def test_concurrent_set_and_get_operations(self, inmemory_backend: InMemoryCacheBackend):
        """Unit test: Concurrent set and get operations are thread-safe."""
        import concurrent.futures

        key = "concurrent_key"
        values = [f"value_{i}" for i in range(100)]

        def do_set(value: str):
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(inmemory_backend.set(key, value))
            finally:
                loop.close()

        def do_get():
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(inmemory_backend.get(key))
            finally:
                loop.close()

        # Run concurrent sets and gets
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            set_futures = [executor.submit(do_set, v) for v in values]
            get_futures = [executor.submit(do_get) for _ in range(50)]
            concurrent.futures.wait(set_futures + get_futures)

        # Final value should be one of the set values (last one wins)
        result = await inmemory_backend.get(key)
        assert result in values or result is None  # Could be None if all sets failed

    @pytest.mark.asyncio
    async def test_concurrent_delete_operations(self, inmemory_backend: InMemoryCacheBackend):
        """Unit test: Concurrent delete operations are thread-safe."""
        import concurrent.futures

        # Set up multiple keys
        keys = [f"key_{i}" for i in range(50)]
        for key in keys:
            await inmemory_backend.set(key, "value")

        def do_delete(key: str):
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(inmemory_backend.delete(key))
            finally:
                loop.close()

        # Delete all keys concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(do_delete, k) for k in keys]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # All keys should be deleted
        for key in keys:
            assert await inmemory_backend.exists(key) is False


# =============================================================================
# Parametrized Property Tests for Backend Substitutability
# These tests run against both InMemoryCacheBackend and RedisCacheBackend
# to verify that both backends produce equivalent observable behavior.
# =============================================================================


class TestProperty2RedisCacheBackendRoundTrip:
    """
    Property 2: Set-then-get round-trip consistency for RedisCacheBackend

    *For any* cache backend, any key, and any string value, if `set(key, value)`
    succeeds, then an immediate `get(key)` SHALL return the same value.

    **Validates: Requirements 9.1**

    Feature: unified-cache-interface, Property 2: Set-then-get round-trip consistency
    """

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(key=cache_key_strategy, value=cache_value_strategy)
    async def test_redis_set_then_get_returns_same_value(self, key: str, value: str):
        """
        Property test: For any key and value, set then get returns the same value (Redis).

        Feature: unified-cache-interface, Property 2: Set-then-get round-trip consistency
        **Validates: Requirements 9.1**
        """
        mock_client = MockRedisClient()
        backend = RedisCacheBackend(mock_client)

        # Set the value
        result = await backend.set(key, value)
        assert result is True, f"set() should return True, got {result}"

        # Get should return the same value
        retrieved = await backend.get(key)
        assert retrieved == value, f"Expected {value!r}, got {retrieved!r}"

    @pytest.mark.asyncio
    async def test_redis_set_then_get_with_ttl(self, redis_backend: RedisCacheBackend):
        """Unit test: set with TTL then immediate get returns the value (Redis)."""
        await redis_backend.set("key", "value", ttl_seconds=300)
        result = await redis_backend.get("key")
        assert result == "value"

    @pytest.mark.asyncio
    async def test_redis_set_overwrites_existing_value(self, redis_backend: RedisCacheBackend):
        """Unit test: set overwrites existing value (Redis)."""
        await redis_backend.set("key", "value1")
        await redis_backend.set("key", "value2")
        result = await redis_backend.get("key")
        assert result == "value2"


class TestProperty3RedisCacheBackendTTLExpiration:
    """
    Property 3: TTL expiration for RedisCacheBackend

    *For any* cache backend, any key set with a TTL, after the TTL duration
    has elapsed, `get(key)` SHALL return `None`.

    **Validates: Requirements 2.4, 9.3**

    Feature: unified-cache-interface, Property 3: TTL expiration
    """

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        key=cache_key_strategy,
        value=cache_value_strategy,
    )
    async def test_redis_ttl_expiration(self, key: str, value: str):
        """
        Property test: For any key set with TTL, after TTL expires, get returns None (Redis).

        Note: We test the expiration logic by manipulating time internally rather
        than using sleep, to keep tests fast while still validating the property.

        Feature: unified-cache-interface, Property 3: TTL expiration
        **Validates: Requirements 2.4, 9.3**
        """
        mock_client = MockRedisClient()
        backend = RedisCacheBackend(mock_client)

        # Set with a TTL
        await backend.set(key, value, ttl_seconds=10)

        # Immediately after set, value should be retrievable
        result = await backend.get(key)
        assert result == value, f"Expected {value!r} immediately after set, got {result!r}"

        # Manually expire the entry by modifying the mock's internal state
        if key in mock_client._expiry:
            mock_client._expiry[key] = time.time() - 1

        # After TTL, get should return None
        result = await backend.get(key)
        assert result is None, f"Expected None after TTL expiration, got {result!r}"

    @pytest.mark.asyncio
    async def test_redis_ttl_expiration_with_real_time(self, redis_backend: RedisCacheBackend):
        """Unit test: TTL expiration works with real time passage (Redis)."""
        await redis_backend.set("key", "value", ttl_seconds=1)

        # Immediately after set, value should be retrievable
        assert await redis_backend.get("key") == "value"

        # Wait for TTL to expire
        await asyncio.sleep(1.1)

        # After TTL, get should return None
        assert await redis_backend.get("key") is None

    @pytest.mark.asyncio
    async def test_redis_ttl_expiration_exists_returns_false(self, redis_backend: RedisCacheBackend):
        """Unit test: exists() returns False after TTL expiration (Redis)."""
        await redis_backend.set("key", "value", ttl_seconds=1)
        assert await redis_backend.exists("key") is True

        await asyncio.sleep(1.1)

        assert await redis_backend.exists("key") is False


class TestProperty4RedisCacheBackendIncrDecr:
    """
    Property 4: Incr/decr on non-existent keys starts from zero for RedisCacheBackend

    *For any* cache backend and any key that does not exist, `incr(key)` SHALL
    return 1 and `decr(key)` SHALL return -1.

    **Validates: Requirements 2.6**

    Feature: unified-cache-interface, Property 4: Incr/decr on non-existent keys starts from zero
    """

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(key=cache_key_strategy)
    async def test_redis_incr_on_nonexistent_key_returns_one(self, key: str):
        """
        Property test: For any non-existent key, incr() returns 1 (Redis).

        Feature: unified-cache-interface, Property 4: Incr/decr on non-existent keys starts from zero
        **Validates: Requirements 2.6**
        """
        mock_client = MockRedisClient()
        backend = RedisCacheBackend(mock_client)

        # Key doesn't exist
        assert await backend.exists(key) is False

        # incr on non-existent key should return 1 (0 + 1)
        result = await backend.incr(key)
        assert result == 1, f"Expected 1 for incr on non-existent key, got {result}"

        # Key should now exist with value "1"
        value = await backend.get(key)
        assert value == "1", f"Expected '1' after incr, got {value!r}"

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(key=cache_key_strategy)
    async def test_redis_decr_on_nonexistent_key_returns_negative_one(self, key: str):
        """
        Property test: For any non-existent key, decr() returns -1 (Redis).

        Feature: unified-cache-interface, Property 4: Incr/decr on non-existent keys starts from zero
        **Validates: Requirements 2.6**
        """
        mock_client = MockRedisClient()
        backend = RedisCacheBackend(mock_client)

        # Key doesn't exist
        assert await backend.exists(key) is False

        # decr on non-existent key should return -1 (0 - 1)
        result = await backend.decr(key)
        assert result == -1, f"Expected -1 for decr on non-existent key, got {result}"

        # Key should now exist with value "-1"
        value = await backend.get(key)
        assert value == "-1", f"Expected '-1' after decr, got {value!r}"

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(key=cache_key_strategy, amount=st.integers(min_value=1, max_value=1000))
    async def test_redis_incr_with_custom_amount(self, key: str, amount: int):
        """
        Property test: incr with custom amount on non-existent key returns that amount (Redis).

        Feature: unified-cache-interface, Property 4: Incr/decr on non-existent keys starts from zero
        **Validates: Requirements 2.6**
        """
        mock_client = MockRedisClient()
        backend = RedisCacheBackend(mock_client)

        result = await backend.incr(key, amount=amount)
        assert result == amount, f"Expected {amount} for incr with amount={amount}, got {result}"

    @pytest.mark.asyncio
    async def test_redis_incr_on_existing_value(self, redis_backend: RedisCacheBackend):
        """Unit test: incr on existing numeric value increments correctly (Redis)."""
        await redis_backend.set("counter", "10")

        result = await redis_backend.incr("counter")
        assert result == 11

        result = await redis_backend.incr("counter", amount=5)
        assert result == 16

    @pytest.mark.asyncio
    async def test_redis_decr_on_existing_value(self, redis_backend: RedisCacheBackend):
        """Unit test: decr on existing numeric value decrements correctly (Redis)."""
        await redis_backend.set("counter", "10")

        result = await redis_backend.decr("counter")
        assert result == 9

        result = await redis_backend.decr("counter", amount=5)
        assert result == 4


# =============================================================================
# Parametrized Tests for Backend Substitutability (Property 9.1)
# These tests verify that both backends produce equivalent observable behavior.
# =============================================================================


class TestBackendSubstitutability:
    """
    Tests for backend substitutability.

    These tests verify that both InMemoryCacheBackend and RedisCacheBackend
    produce equivalent observable behavior for all operations.

    **Validates: Requirements 9.1, 9.2**

    Feature: unified-cache-interface, Backend Substitutability
    """

    @pytest.mark.asyncio
    async def test_get_returns_none_for_missing_key(self, cache_backend: CacheBackend):
        """Both backends return None for missing keys."""
        result = await cache_backend.get("nonexistent_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_then_get_round_trip(self, cache_backend: CacheBackend):
        """Both backends support set-then-get round trip."""
        await cache_backend.set("key", "value")
        result = await cache_backend.get("key")
        assert result == "value"

    @pytest.mark.asyncio
    async def test_delete_removes_key(self, cache_backend: CacheBackend):
        """Both backends properly delete keys."""
        await cache_backend.set("key", "value")
        assert await cache_backend.exists("key") is True

        deleted = await cache_backend.delete("key")
        assert deleted is True
        assert await cache_backend.exists("key") is False

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_false(self, cache_backend: CacheBackend):
        """Both backends return False when deleting nonexistent key."""
        result = await cache_backend.delete("nonexistent_key")
        assert result is False

    @pytest.mark.asyncio
    async def test_exists_returns_correct_value(self, cache_backend: CacheBackend):
        """Both backends correctly report key existence."""
        assert await cache_backend.exists("key") is False

        await cache_backend.set("key", "value")
        assert await cache_backend.exists("key") is True

    @pytest.mark.asyncio
    async def test_expire_sets_ttl(self, cache_backend: CacheBackend):
        """Both backends support setting TTL on existing keys."""
        await cache_backend.set("key", "value")

        result = await cache_backend.expire("key", 300)
        assert result is True

    @pytest.mark.asyncio
    async def test_expire_nonexistent_returns_false(self, cache_backend: CacheBackend):
        """Both backends return False when setting TTL on nonexistent key."""
        result = await cache_backend.expire("nonexistent_key", 300)
        assert result is False

    @pytest.mark.asyncio
    async def test_incr_on_nonexistent_key(self, cache_backend: CacheBackend):
        """Both backends treat nonexistent keys as 0 for incr."""
        result = await cache_backend.incr("counter")
        assert result == 1

    @pytest.mark.asyncio
    async def test_decr_on_nonexistent_key(self, cache_backend: CacheBackend):
        """Both backends treat nonexistent keys as 0 for decr."""
        result = await cache_backend.decr("counter")
        assert result == -1

    @pytest.mark.asyncio
    async def test_incr_with_amount(self, cache_backend: CacheBackend):
        """Both backends support incr with custom amount."""
        result = await cache_backend.incr("counter", amount=5)
        assert result == 5

        result = await cache_backend.incr("counter", amount=3)
        assert result == 8

    @pytest.mark.asyncio
    async def test_decr_with_amount(self, cache_backend: CacheBackend):
        """Both backends support decr with custom amount."""
        await cache_backend.set("counter", "10")

        result = await cache_backend.decr("counter", amount=3)
        assert result == 7

    @pytest.mark.asyncio
    async def test_set_with_zero_ttl_deletes_key(self, cache_backend: CacheBackend):
        """Both backends delete key when TTL is 0 or negative."""
        await cache_backend.set("key", "value")
        assert await cache_backend.exists("key") is True

        await cache_backend.set("key", "new_value", ttl_seconds=0)
        assert await cache_backend.exists("key") is False

    @pytest.mark.asyncio
    async def test_set_with_negative_ttl_deletes_key(self, cache_backend: CacheBackend):
        """Both backends delete key when TTL is negative."""
        await cache_backend.set("key", "value")
        assert await cache_backend.exists("key") is True

        await cache_backend.set("key", "new_value", ttl_seconds=-1)
        assert await cache_backend.exists("key") is False


# =============================================================================
# Factory and Dependency Injection Tests
# =============================================================================


class TestProperty6FactorySingleton:
    """
    Property 6: Factory returns singleton

    *For any* number of calls to `get_cache_backend()`, the same instance
    SHALL be returned.

    **Validates: Requirements 4.5**

    Feature: unified-cache-interface, Property 6: Factory returns singleton
    """

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(num_calls=st.integers(min_value=2, max_value=10))
    async def test_factory_returns_singleton(self, num_calls: int):
        """
        Property test: Multiple calls to get_cache_backend() return the same instance.

        Feature: unified-cache-interface, Property 6: Factory returns singleton
        **Validates: Requirements 4.5**
        """
        from shu.core.cache_backend import get_cache_backend, reset_cache_backend

        # Reset to ensure clean state for each test case
        reset_cache_backend()

        # Get the backend multiple times
        backends = []
        for _ in range(num_calls):
            backend = await get_cache_backend()
            backends.append(backend)

        # All instances should be the same object
        first_backend = backends[0]
        for i, backend in enumerate(backends[1:], start=2):
            assert backend is first_backend, f"Call {i} returned different instance than call 1"

        # Clean up
        reset_cache_backend()

    @pytest.mark.asyncio
    async def test_factory_returns_same_instance_across_calls(self):
        """Unit test: get_cache_backend() returns the same instance."""
        from shu.core.cache_backend import get_cache_backend, reset_cache_backend

        reset_cache_backend()

        backend1 = await get_cache_backend()
        backend2 = await get_cache_backend()
        backend3 = await get_cache_backend()

        assert backend1 is backend2
        assert backend2 is backend3

        reset_cache_backend()

    @pytest.mark.asyncio
    async def test_reset_clears_singleton(self):
        """Unit test: reset_cache_backend() clears the singleton."""
        from shu.core.cache_backend import get_cache_backend, reset_cache_backend

        reset_cache_backend()

        backend1 = await get_cache_backend()
        reset_cache_backend()
        backend2 = await get_cache_backend()

        # After reset, a new instance should be created
        # Note: They may be equal in value but should be different objects
        # unless the same backend type is created
        assert backend1 is not backend2

        reset_cache_backend()


class TestBackendSelectionLogic:
    """
    Tests for backend selection logic in the factory.

    **Validates: Requirements 4.1, 4.2, 4.3**

    Feature: unified-cache-interface, Backend Selection
    """

    @pytest.mark.asyncio
    async def test_returns_inmemory_when_no_redis_url(self):
        """Unit test: Factory returns InMemoryCacheBackend when no Redis URL configured."""
        from unittest.mock import MagicMock, patch

        from shu.core.cache_backend import (
            InMemoryCacheBackend,
            get_cache_backend,
            reset_cache_backend,
        )

        reset_cache_backend()

        # Mock settings to have no Redis URL and redis_required=False
        mock_settings = MagicMock()
        mock_settings.redis_url = ""
        mock_settings.redis_required = False
        mock_settings.redis_fallback_enabled = True

        with patch("shu.core.config.get_settings_instance", return_value=mock_settings):
            backend = await get_cache_backend()
            assert isinstance(backend, InMemoryCacheBackend)

        reset_cache_backend()

    @pytest.mark.asyncio
    async def test_returns_inmemory_when_redis_unreachable_with_fallback(self):
        """Unit test: Factory returns InMemoryCacheBackend when Redis unreachable and fallback enabled."""
        from unittest.mock import MagicMock, patch

        from shu.core.cache_backend import (
            CacheConnectionError,
            InMemoryCacheBackend,
            get_cache_backend,
            reset_cache_backend,
        )

        reset_cache_backend()

        # Mock settings with Redis URL but fallback enabled
        mock_settings = MagicMock()
        mock_settings.redis_url = "redis://localhost:6379"
        mock_settings.redis_required = False
        mock_settings.redis_fallback_enabled = True
        mock_settings.redis_socket_timeout = 5
        mock_settings.redis_connection_timeout = 5

        # Mock _get_redis_client to raise CacheConnectionError
        async def mock_get_redis_client_error():
            raise CacheConnectionError("Connection refused")

        with patch("shu.core.config.get_settings_instance", return_value=mock_settings):
            with patch("shu.core.cache_backend._get_redis_client", mock_get_redis_client_error):
                backend = await get_cache_backend()
                assert isinstance(backend, InMemoryCacheBackend)

        reset_cache_backend()

    @pytest.mark.asyncio
    async def test_raises_error_when_redis_required_but_unreachable(self):
        """Unit test: Factory raises error when Redis required but unreachable."""
        from unittest.mock import MagicMock, patch

        from shu.core.cache_backend import (
            CacheConnectionError,
            get_cache_backend,
            reset_cache_backend,
        )

        reset_cache_backend()

        # Mock settings with Redis required
        mock_settings = MagicMock()
        mock_settings.redis_url = "redis://localhost:6379"
        mock_settings.redis_required = True
        mock_settings.redis_fallback_enabled = False
        mock_settings.redis_socket_timeout = 5
        mock_settings.redis_connection_timeout = 5

        # Mock _get_redis_client to raise CacheConnectionError
        async def mock_get_redis_client_error():
            raise CacheConnectionError("Connection refused")

        with patch("shu.core.config.get_settings_instance", return_value=mock_settings):
            with patch("shu.core.cache_backend._get_redis_client", mock_get_redis_client_error):
                with pytest.raises(CacheConnectionError):
                    await get_cache_backend()

        reset_cache_backend()

    @pytest.mark.asyncio
    async def test_raises_error_when_fallback_disabled_and_redis_unreachable(self):
        """Unit test: Factory raises error when fallback disabled and Redis unreachable."""
        from unittest.mock import MagicMock, patch

        from shu.core.cache_backend import (
            CacheConnectionError,
            get_cache_backend,
            reset_cache_backend,
        )

        reset_cache_backend()

        # Mock settings with fallback disabled and a non-default Redis URL
        mock_settings = MagicMock()
        mock_settings.redis_url = "redis://custom-redis:6379"  # Non-default URL
        mock_settings.redis_required = False
        mock_settings.redis_fallback_enabled = False
        mock_settings.redis_socket_timeout = 5
        mock_settings.redis_connection_timeout = 5

        # Mock _get_redis_client to raise CacheConnectionError
        async def mock_get_redis_client_error():
            raise CacheConnectionError("Connection refused")

        with patch("shu.core.config.get_settings_instance", return_value=mock_settings):
            with patch("shu.core.cache_backend._get_redis_client", mock_get_redis_client_error):
                with pytest.raises(CacheConnectionError):
                    await get_cache_backend()

        reset_cache_backend()

    @pytest.mark.asyncio
    async def test_returns_redis_backend_when_redis_available(self):
        """Unit test: Factory returns RedisCacheBackend when Redis is available."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from shu.core.cache_backend import RedisCacheBackend, get_cache_backend, reset_cache_backend

        reset_cache_backend()

        # Mock settings with a non-default Redis URL
        mock_settings = MagicMock()
        mock_settings.redis_url = "redis://custom-redis:6379"  # Non-default URL
        mock_settings.redis_required = False
        mock_settings.redis_fallback_enabled = True
        mock_settings.redis_socket_timeout = 5
        mock_settings.redis_connection_timeout = 5

        # Mock Redis client
        mock_redis_client = MagicMock()
        mock_redis_client.ping = AsyncMock(return_value=True)

        async def mock_get_redis_client_success():
            return mock_redis_client

        with patch("shu.core.config.get_settings_instance", return_value=mock_settings):
            with patch("shu.core.cache_backend._get_redis_client", mock_get_redis_client_success):
                backend = await get_cache_backend()
                assert isinstance(backend, RedisCacheBackend)

        reset_cache_backend()


class TestDependencyInjection:
    """
    Tests for dependency injection support.

    **Validates: Requirements 4.4**

    Feature: unified-cache-interface, Dependency Injection
    """

    def test_dependency_returns_cached_backend_if_available(self):
        """Unit test: get_cache_backend_dependency returns cached backend."""
        import shu.core.cache_backend as cache_module
        from shu.core.cache_backend import (
            InMemoryCacheBackend,
            get_cache_backend_dependency,
            reset_cache_backend,
        )

        reset_cache_backend()

        # Set up a cached backend
        cached_backend = InMemoryCacheBackend()
        cache_module._cache_backend = cached_backend

        # Dependency should return the cached backend
        result = get_cache_backend_dependency()
        assert result is cached_backend

        reset_cache_backend()

    def test_dependency_returns_inmemory_if_no_cached_backend(self):
        """Unit test: get_cache_backend_dependency returns InMemoryCacheBackend if no cached backend."""
        from shu.core.cache_backend import (
            InMemoryCacheBackend,
            get_cache_backend_dependency,
            reset_cache_backend,
        )

        reset_cache_backend()

        # No cached backend, should return InMemoryCacheBackend
        result = get_cache_backend_dependency()
        assert isinstance(result, InMemoryCacheBackend)

        reset_cache_backend()

    def test_dependency_is_synchronous(self):
        """Unit test: get_cache_backend_dependency is synchronous (not async)."""
        import asyncio

        from shu.core.cache_backend import get_cache_backend_dependency

        # Should not be a coroutine
        result = get_cache_backend_dependency()
        assert not asyncio.iscoroutine(result)
