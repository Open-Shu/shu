"""
Unified Cache Backend Interface for Shu.

This module defines the CacheBackend protocol that provides a unified interface
for key-value caching operations. It supports two interchangeable implementations:
- RedisCacheBackend: For scaled multi-node deployments
- InMemoryCacheBackend: For single-node/development deployments

Backend selection is automatic based on the SHU_REDIS_URL configuration.

Example usage:
    # In FastAPI endpoints (preferred - dependency injection):
    from shu.core.cache_backend import get_cache_backend_dependency, CacheBackend
    
    async def my_endpoint(
        cache: CacheBackend = Depends(get_cache_backend_dependency)
    ):
        await cache.set("my_key", "my_value", ttl_seconds=300)
        value = await cache.get("my_key")
    
    # In background tasks or non-FastAPI code:
    from shu.core.cache_backend import get_cache_backend
    
    backend = await get_cache_backend()
    await backend.set("my_key", "my_value", ttl_seconds=300)
"""

import logging
import threading
import time
from typing import Protocol, Optional, Any, Dict, Tuple, runtime_checkable

import redis.asyncio as redis

logger = logging.getLogger(__name__)

# Global cache backend instance (singleton)
_cache_backend: Optional["CacheBackend"] = None

# Global Redis client instance (internal use only)
_redis_client: Optional[Any] = None


class CacheError(Exception):
    """Base exception for cache operations.
    
    All cache-related exceptions inherit from this class, allowing
    consumers to catch all cache errors with a single except clause.
    
    Attributes:
        message: Human-readable error description.
        details: Optional dictionary with additional error context.
    """
    
    def __init__(
        self,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)


class CacheConnectionError(CacheError):
    """Raised when the cache backend is unreachable.
    
    This exception indicates a connectivity issue with the underlying
    cache storage (e.g., Redis server is down, network timeout).
    
    Example:
        try:
            await backend.get("key")
        except CacheConnectionError as e:
            logger.warning(f"Cache unavailable: {e.message}")
            # Fall back to database or default value
    """
    pass


class CacheOperationError(CacheError):
    """Raised when a cache operation fails.
    
    This exception indicates that the cache backend was reachable but
    the operation itself failed (e.g., serialization error, invalid key).
    
    Example:
        try:
            await backend.incr("non_numeric_key")
        except CacheOperationError as e:
            logger.error(f"Cache operation failed: {e.message}")
    """
    pass


class CacheKeyError(CacheError):
    """Raised when there's an issue with the cache key.
    
    This exception indicates that the provided key is invalid
    (e.g., empty string, contains invalid characters, too long).
    """
    pass


class CacheTypeError(CacheError):
    """Raised when there's a type mismatch in cache operations.
    
    This exception indicates that an operation was attempted on a key
    with an incompatible value type (e.g., incr on a non-numeric value).
    """
    pass


@runtime_checkable
class CacheBackend(Protocol):
    """Protocol defining the cache backend interface.
    
    All implementations must provide these async methods for key-value
    caching with optional TTL (time-to-live) support.
    
    This protocol uses structural typing via @runtime_checkable, allowing
    any class that implements these methods to be used as a CacheBackend
    without explicit inheritance.
    
    Thread Safety:
        All implementations must be thread-safe for concurrent access.
    
    Key Format:
        Keys should be strings. Implementations may impose length limits
        or character restrictions. Consumers should use namespaced keys
        to avoid collisions (e.g., "plugin:gmail:user123:last_sync").
    
    Value Format:
        Values must be strings. Consumers are responsible for serializing
        complex objects (e.g., using JSON) before storing.
    
    Example:
        class MyCache:
            async def get(self, key: str) -> Optional[str]: ...
            async def set(self, key: str, value: str, ttl_seconds: Optional[int] = None) -> bool: ...
            # ... other methods
        
        cache: CacheBackend = MyCache()  # Type checks pass
    """
    
    async def get(self, key: str) -> Optional[str]:
        """Retrieve a value by key.
        
        Args:
            key: The cache key to retrieve. Must be a non-empty string.
        
        Returns:
            The cached value as a string, or None if the key does not exist
            or has expired.
        
        Raises:
            CacheConnectionError: If the cache backend is unreachable.
            CacheKeyError: If the key is invalid (empty, too long, etc.).
        
        Example:
            value = await backend.get("user:123:profile")
            if value is not None:
                profile = json.loads(value)
        """
        ...
    
    async def set(
        self,
        key: str,
        value: str,
        ttl_seconds: Optional[int] = None,
    ) -> bool:
        """Store a value with optional TTL.
        
        Args:
            key: The cache key. Must be a non-empty string.
            value: The value to store. Must be a string.
            ttl_seconds: Optional time-to-live in seconds. If None, the key
                will not expire automatically. If 0 or negative, the key
                will be deleted immediately.
        
        Returns:
            True if the operation succeeded, False otherwise.
        
        Raises:
            CacheConnectionError: If the cache backend is unreachable.
            CacheKeyError: If the key is invalid.
            CacheOperationError: If the operation fails for other reasons.
        
        Example:
            # Store with 5-minute TTL
            await backend.set("session:abc123", session_data, ttl_seconds=300)
            
            # Store without expiration
            await backend.set("config:app_version", "1.0.0")
        """
        ...
    
    async def delete(self, key: str) -> bool:
        """Delete a key from the cache.
        
        Args:
            key: The cache key to delete. Must be a non-empty string.
        
        Returns:
            True if the key was deleted, False if it didn't exist.
        
        Raises:
            CacheConnectionError: If the cache backend is unreachable.
            CacheKeyError: If the key is invalid.
        
        Example:
            deleted = await backend.delete("user:123:session")
            if deleted:
                logger.info("Session invalidated")
        """
        ...
    
    async def exists(self, key: str) -> bool:
        """Check if a key exists in the cache.
        
        Args:
            key: The cache key to check. Must be a non-empty string.
        
        Returns:
            True if the key exists and is not expired, False otherwise.
        
        Raises:
            CacheConnectionError: If the cache backend is unreachable.
            CacheKeyError: If the key is invalid.
        
        Example:
            if await backend.exists("rate_limit:user:123"):
                raise RateLimitExceeded()
        """
        ...
    
    async def expire(self, key: str, ttl_seconds: int) -> bool:
        """Set or update the TTL for an existing key.
        
        Args:
            key: The cache key. Must be a non-empty string.
            ttl_seconds: New TTL in seconds. Must be positive.
        
        Returns:
            True if the TTL was set, False if the key doesn't exist.
        
        Raises:
            CacheConnectionError: If the cache backend is unreachable.
            CacheKeyError: If the key is invalid.
            ValueError: If ttl_seconds is not positive.
        
        Example:
            # Extend session expiration
            await backend.expire("session:abc123", ttl_seconds=3600)
        """
        ...
    
    async def incr(self, key: str, amount: int = 1) -> int:
        """Increment a numeric value.
        
        If the key doesn't exist, it's treated as 0 before incrementing.
        The value is stored as a string representation of the integer.
        
        Args:
            key: The cache key. Must be a non-empty string.
            amount: Amount to increment by. Default is 1. Can be negative
                to effectively decrement.
        
        Returns:
            The new value after incrementing.
        
        Raises:
            CacheConnectionError: If the cache backend is unreachable.
            CacheKeyError: If the key is invalid.
            CacheTypeError: If the existing value is not a valid integer.
        
        Example:
            # Increment page view counter
            views = await backend.incr("page:home:views")
            
            # Increment by custom amount
            score = await backend.incr("user:123:score", amount=10)
        """
        ...
    
    async def decr(self, key: str, amount: int = 1) -> int:
        """Decrement a numeric value.
        
        If the key doesn't exist, it's treated as 0 before decrementing.
        The value is stored as a string representation of the integer.
        
        Args:
            key: The cache key. Must be a non-empty string.
            amount: Amount to decrement by. Default is 1. Can be negative
                to effectively increment.
        
        Returns:
            The new value after decrementing.
        
        Raises:
            CacheConnectionError: If the cache backend is unreachable.
            CacheKeyError: If the key is invalid.
            CacheTypeError: If the existing value is not a valid integer.
        
        Example:
            # Decrement remaining quota
            remaining = await backend.decr("quota:user:123:api_calls")
            if remaining < 0:
                raise QuotaExceeded()
        """
        ...


class InMemoryCacheBackend:
    """In-memory cache implementation with TTL support.
    
    Thread-safe implementation suitable for single-process deployments.
    Uses threading.RLock for thread safety and supports TTL expiration
    with lazy cleanup on access plus periodic cleanup to prevent memory leaks.
    
    Limitations:
        - Data is not shared across processes
        - Data is lost on process restart
        - Not suitable for multi-node deployments
        - TTL precision is in seconds (not milliseconds like Redis)
    
    Example:
        backend = InMemoryCacheBackend()
        await backend.set("key", "value", ttl_seconds=300)
        value = await backend.get("key")  # Returns "value"
        
        # After 300 seconds...
        value = await backend.get("key")  # Returns None (expired)
    
    Thread Safety:
        All operations are protected by a reentrant lock (RLock), making
        this implementation safe for concurrent access from multiple threads.
        However, compound operations (read-modify-write) should still be
        performed atomically using the provided methods (incr, decr).
    """
    
    def __init__(self, cleanup_interval_seconds: int = 60):
        """Initialize the in-memory cache.
        
        Args:
            cleanup_interval_seconds: Interval for periodic cleanup of
                expired entries. Default is 60 seconds. Set to 0 to
                disable periodic cleanup (only lazy cleanup on access).
        """
        # Storage: key -> (value, expiry_timestamp or None for no expiry)
        self._data: Dict[str, Tuple[str, Optional[float]]] = {}
        self._lock = threading.RLock()
        self._cleanup_interval = cleanup_interval_seconds
        self._last_cleanup = time.time()
    
    def _is_expired(self, expiry: Optional[float]) -> bool:
        """Check if an entry has expired.
        
        Args:
            expiry: The expiry timestamp, or None if no expiry.
            
        Returns:
            True if the entry has expired, False otherwise.
        """
        if expiry is None:
            return False
        return time.time() > expiry
    
    def _maybe_cleanup(self) -> None:
        """Perform periodic cleanup of expired entries if interval has passed.
        
        This method should be called while holding the lock.
        """
        if self._cleanup_interval <= 0:
            return
            
        current_time = time.time()
        if current_time - self._last_cleanup < self._cleanup_interval:
            return
        
        self._last_cleanup = current_time
        
        # Find and remove expired keys
        expired_keys = [
            key for key, (_, expiry) in self._data.items()
            if self._is_expired(expiry)
        ]
        for key in expired_keys:
            del self._data[key]
    
    async def get(self, key: str) -> Optional[str]:
        """Retrieve a value by key.
        
        Performs lazy expiration check - if the key exists but has expired,
        it will be deleted and None will be returned.
        
        Args:
            key: The cache key to retrieve. Must be a non-empty string.
        
        Returns:
            The cached value as a string, or None if the key does not exist
            or has expired.
        
        Raises:
            CacheKeyError: If the key is empty.
        """
        if not key:
            raise CacheKeyError("Cache key cannot be empty")
        
        with self._lock:
            self._maybe_cleanup()
            
            if key not in self._data:
                return None
            
            value, expiry = self._data[key]
            
            # Lazy expiration check
            if self._is_expired(expiry):
                del self._data[key]
                return None
            
            return value
    
    async def set(
        self,
        key: str,
        value: str,
        ttl_seconds: Optional[int] = None,
    ) -> bool:
        """Store a value with optional TTL.
        
        Args:
            key: The cache key. Must be a non-empty string.
            value: The value to store. Must be a string.
            ttl_seconds: Optional time-to-live in seconds. If None, the key
                will not expire automatically. If 0 or negative, the key
                will be deleted immediately.
        
        Returns:
            True if the operation succeeded.
        
        Raises:
            CacheKeyError: If the key is empty.
        """
        if not key:
            raise CacheKeyError("Cache key cannot be empty")
        
        with self._lock:
            self._maybe_cleanup()
            
            # Handle immediate deletion for non-positive TTL
            if ttl_seconds is not None and ttl_seconds <= 0:
                if key in self._data:
                    del self._data[key]
                return True
            
            # Calculate expiry timestamp
            expiry: Optional[float] = None
            if ttl_seconds is not None:
                expiry = time.time() + ttl_seconds
            
            self._data[key] = (value, expiry)
            return True
    
    async def delete(self, key: str) -> bool:
        """Delete a key from the cache.
        
        Args:
            key: The cache key to delete. Must be a non-empty string.
        
        Returns:
            True if the key was deleted, False if it didn't exist.
        
        Raises:
            CacheKeyError: If the key is empty.
        """
        if not key:
            raise CacheKeyError("Cache key cannot be empty")
        
        with self._lock:
            self._maybe_cleanup()
            
            if key not in self._data:
                return False
            
            # Check if already expired (lazy expiration)
            _, expiry = self._data[key]
            if self._is_expired(expiry):
                del self._data[key]
                return False
            
            del self._data[key]
            return True
    
    async def exists(self, key: str) -> bool:
        """Check if a key exists in the cache.
        
        Performs lazy expiration check - if the key exists but has expired,
        it will be deleted and False will be returned.
        
        Args:
            key: The cache key to check. Must be a non-empty string.
        
        Returns:
            True if the key exists and is not expired, False otherwise.
        
        Raises:
            CacheKeyError: If the key is empty.
        """
        if not key:
            raise CacheKeyError("Cache key cannot be empty")
        
        with self._lock:
            self._maybe_cleanup()
            
            if key not in self._data:
                return False
            
            _, expiry = self._data[key]
            
            # Lazy expiration check
            if self._is_expired(expiry):
                del self._data[key]
                return False
            
            return True
    
    async def expire(self, key: str, ttl_seconds: int) -> bool:
        """Set or update the TTL for an existing key.
        
        Args:
            key: The cache key. Must be a non-empty string.
            ttl_seconds: New TTL in seconds. Must be positive.
        
        Returns:
            True if the TTL was set, False if the key doesn't exist.
        
        Raises:
            CacheKeyError: If the key is empty.
            ValueError: If ttl_seconds is not positive.
        """
        if not key:
            raise CacheKeyError("Cache key cannot be empty")
        
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        
        with self._lock:
            self._maybe_cleanup()
            
            if key not in self._data:
                return False
            
            value, old_expiry = self._data[key]
            
            # Check if already expired
            if self._is_expired(old_expiry):
                del self._data[key]
                return False
            
            # Update expiry
            new_expiry = time.time() + ttl_seconds
            self._data[key] = (value, new_expiry)
            return True
    
    async def incr(self, key: str, amount: int = 1) -> int:
        """Increment a numeric value.
        
        If the key doesn't exist, it's treated as 0 before incrementing.
        The value is stored as a string representation of the integer.
        
        Args:
            key: The cache key. Must be a non-empty string.
            amount: Amount to increment by. Default is 1.
        
        Returns:
            The new value after incrementing.
        
        Raises:
            CacheKeyError: If the key is empty.
            CacheTypeError: If the existing value is not a valid integer.
        """
        if not key:
            raise CacheKeyError("Cache key cannot be empty")
        
        with self._lock:
            self._maybe_cleanup()
            
            current_value = 0
            current_expiry: Optional[float] = None
            
            if key in self._data:
                value_str, expiry = self._data[key]
                
                # Check if expired
                if self._is_expired(expiry):
                    del self._data[key]
                else:
                    try:
                        current_value = int(value_str)
                        current_expiry = expiry
                    except ValueError as e:
                        raise CacheTypeError(
                            f"Value for key '{key}' is not a valid integer: {value_str!r}"
                        ) from e
            
            new_value = current_value + amount
            self._data[key] = (str(new_value), current_expiry)
            return new_value
    
    async def decr(self, key: str, amount: int = 1) -> int:
        """Decrement a numeric value.
        
        If the key doesn't exist, it's treated as 0 before decrementing.
        The value is stored as a string representation of the integer.
        
        Args:
            key: The cache key. Must be a non-empty string.
            amount: Amount to decrement by. Default is 1.
        
        Returns:
            The new value after decrementing.
        
        Raises:
            CacheKeyError: If the key is empty.
            CacheTypeError: If the existing value is not a valid integer.
        """
        if not key:
            raise CacheKeyError("Cache key cannot be empty")
        
        with self._lock:
            self._maybe_cleanup()
            
            current_value = 0
            current_expiry: Optional[float] = None
            
            if key in self._data:
                value_str, expiry = self._data[key]
                
                # Check if expired
                if self._is_expired(expiry):
                    del self._data[key]
                else:
                    try:
                        current_value = int(value_str)
                        current_expiry = expiry
                    except ValueError as e:
                        raise CacheTypeError(
                            f"Value for key '{key}' is not a valid integer: {value_str!r}"
                        ) from e
            
            new_value = current_value - amount
            self._data[key] = (str(new_value), current_expiry)
            return new_value


class RedisCacheBackend:
    """Redis-backed cache implementation.
    
    Uses the Redis client configured via SHU_REDIS_URL.
    Suitable for multi-node deployments where cache must be shared.
    
    This implementation wraps an async Redis client and provides the
    CacheBackend interface for all cache operations.
    
    Features:
        - Native Redis TTL support with millisecond precision
        - Automatic connection error handling with logging
        - Thread-safe operations (Redis handles concurrency)
        - Atomic incr/decr operations
    
    Example:
        # Preferred: Use the factory function
        from shu.core.cache_backend import get_cache_backend
        
        backend = await get_cache_backend()
        await backend.set("key", "value", ttl_seconds=300)
        value = await backend.get("key")
        
        # Or with dependency injection in FastAPI:
        from shu.core.cache_backend import get_cache_backend_dependency, CacheBackend
        
        async def my_endpoint(
            cache: CacheBackend = Depends(get_cache_backend_dependency)
        ):
            await cache.set("key", "value", ttl_seconds=300)
    
    Note:
        The Redis client is managed internally by this module. Use
        `get_cache_backend()` to obtain a properly configured backend
        instance rather than constructing RedisCacheBackend directly.
    """
    
    def __init__(self, redis_client: Any):
        """Initialize with an existing Redis client.
        
        Args:
            redis_client: An async Redis client instance. This is typically
                created internally by `get_cache_backend()`. External code
                should use the factory function instead of constructing
                this class directly.
        """
        self._client = redis_client
    
    async def get(self, key: str) -> Optional[str]:
        """Retrieve a value by key.
        
        Args:
            key: The cache key to retrieve. Must be a non-empty string.
        
        Returns:
            The cached value as a string, or None if the key does not exist
            or has expired.
        
        Raises:
            CacheKeyError: If the key is empty.
            CacheConnectionError: If the Redis server is unreachable.
        """
        if not key:
            raise CacheKeyError("Cache key cannot be empty")
        
        try:
            result = await self._client.get(key)
            return result
        except Exception as e:
            logger.error(f"Redis GET failed for key '{key}': {e}")
            raise CacheConnectionError(
                f"Failed to get key '{key}' from Redis",
                details={"key": key, "error": str(e)}
            ) from e
    
    async def set(
        self,
        key: str,
        value: str,
        ttl_seconds: Optional[int] = None,
    ) -> bool:
        """Store a value with optional TTL.
        
        Args:
            key: The cache key. Must be a non-empty string.
            value: The value to store. Must be a string.
            ttl_seconds: Optional time-to-live in seconds. If None, the key
                will not expire automatically. If 0 or negative, the key
                will be deleted immediately.
        
        Returns:
            True if the operation succeeded.
        
        Raises:
            CacheKeyError: If the key is empty.
            CacheConnectionError: If the Redis server is unreachable.
        """
        if not key:
            raise CacheKeyError("Cache key cannot be empty")
        
        try:
            # Handle immediate deletion for non-positive TTL
            if ttl_seconds is not None and ttl_seconds <= 0:
                await self._client.delete(key)
                return True
            
            if ttl_seconds is not None:
                # Use setex for atomic set with expiration
                await self._client.setex(key, ttl_seconds, value)
            else:
                await self._client.set(key, value)
            
            return True
        except Exception as e:
            logger.error(f"Redis SET failed for key '{key}': {e}")
            raise CacheConnectionError(
                f"Failed to set key '{key}' in Redis",
                details={"key": key, "error": str(e)}
            ) from e
    
    async def delete(self, key: str) -> bool:
        """Delete a key from the cache.
        
        Args:
            key: The cache key to delete. Must be a non-empty string.
        
        Returns:
            True if the key was deleted, False if it didn't exist.
        
        Raises:
            CacheKeyError: If the key is empty.
            CacheConnectionError: If the Redis server is unreachable.
        """
        if not key:
            raise CacheKeyError("Cache key cannot be empty")
        
        try:
            result = await self._client.delete(key)
            # Redis delete returns the number of keys deleted
            return result > 0
        except Exception as e:
            logger.error(f"Redis DELETE failed for key '{key}': {e}")
            raise CacheConnectionError(
                f"Failed to delete key '{key}' from Redis",
                details={"key": key, "error": str(e)}
            ) from e
    
    async def exists(self, key: str) -> bool:
        """Check if a key exists in the cache.
        
        Args:
            key: The cache key to check. Must be a non-empty string.
        
        Returns:
            True if the key exists and is not expired, False otherwise.
        
        Raises:
            CacheKeyError: If the key is empty.
            CacheConnectionError: If the Redis server is unreachable.
        """
        if not key:
            raise CacheKeyError("Cache key cannot be empty")
        
        try:
            # Redis exists returns the count of existing keys
            result = await self._client.exists(key)
            return result > 0
        except AttributeError as e:
            # Fallback for clients that don't have exists method
            try:
                result = await self._client.get(key)
                return result is not None
            except Exception as inner_e:
                logger.error(f"Redis EXISTS fallback failed for key '{key}': {inner_e}")
                raise CacheConnectionError(
                    f"Failed to check existence of key '{key}' in Redis",
                    details={"key": key, "error": str(inner_e)}
                ) from inner_e
        except Exception as e:
            logger.error(f"Redis EXISTS failed for key '{key}': {e}")
            raise CacheConnectionError(
                f"Failed to check existence of key '{key}' in Redis",
                details={"key": key, "error": str(e)}
            ) from e
    
    async def expire(self, key: str, ttl_seconds: int) -> bool:
        """Set or update the TTL for an existing key.
        
        Args:
            key: The cache key. Must be a non-empty string.
            ttl_seconds: New TTL in seconds. Must be positive.
        
        Returns:
            True if the TTL was set, False if the key doesn't exist.
        
        Raises:
            CacheKeyError: If the key is empty.
            ValueError: If ttl_seconds is not positive.
            CacheConnectionError: If the Redis server is unreachable.
        """
        if not key:
            raise CacheKeyError("Cache key cannot be empty")
        
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        
        try:
            result = await self._client.expire(key, ttl_seconds)
            # Redis expire returns True if the timeout was set, False if key doesn't exist
            return bool(result)
        except Exception as e:
            logger.error(f"Redis EXPIRE failed for key '{key}': {e}")
            raise CacheConnectionError(
                f"Failed to set expiration for key '{key}' in Redis",
                details={"key": key, "ttl_seconds": ttl_seconds, "error": str(e)}
            ) from e
    
    async def incr(self, key: str, amount: int = 1) -> int:
        """Increment a numeric value.
        
        If the key doesn't exist, it's treated as 0 before incrementing.
        The value is stored as a string representation of the integer.
        
        Args:
            key: The cache key. Must be a non-empty string.
            amount: Amount to increment by. Default is 1.
        
        Returns:
            The new value after incrementing.
        
        Raises:
            CacheKeyError: If the key is empty.
            CacheTypeError: If the existing value is not a valid integer.
            CacheConnectionError: If the Redis server is unreachable.
        """
        if not key:
            raise CacheKeyError("Cache key cannot be empty")
        
        try:
            if amount == 1:
                result = await self._client.incr(key)
            else:
                result = await self._client.incrby(key, amount)
            return int(result)
        except ValueError as e:
            # Redis returns an error if the value is not an integer
            raise CacheTypeError(
                f"Value for key '{key}' is not a valid integer",
                details={"key": key, "error": str(e)}
            ) from e
        except Exception as e:
            error_str = str(e).lower()
            if "not an integer" in error_str or "wrongtype" in error_str:
                raise CacheTypeError(
                    f"Value for key '{key}' is not a valid integer",
                    details={"key": key, "error": str(e)}
                ) from e
            logger.error(f"Redis INCR failed for key '{key}': {e}")
            raise CacheConnectionError(
                f"Failed to increment key '{key}' in Redis",
                details={"key": key, "amount": amount, "error": str(e)}
            ) from e
    
    async def decr(self, key: str, amount: int = 1) -> int:
        """Decrement a numeric value.
        
        If the key doesn't exist, it's treated as 0 before decrementing.
        The value is stored as a string representation of the integer.
        
        Args:
            key: The cache key. Must be a non-empty string.
            amount: Amount to decrement by. Default is 1.
        
        Returns:
            The new value after decrementing.
        
        Raises:
            CacheKeyError: If the key is empty.
            CacheTypeError: If the existing value is not a valid integer.
            CacheConnectionError: If the Redis server is unreachable.
        """
        if not key:
            raise CacheKeyError("Cache key cannot be empty")
        
        try:
            if amount == 1:
                result = await self._client.decr(key)
            else:
                result = await self._client.decrby(key, amount)
            return int(result)
        except ValueError as e:
            # Redis returns an error if the value is not an integer
            raise CacheTypeError(
                f"Value for key '{key}' is not a valid integer",
                details={"key": key, "error": str(e)}
            ) from e
        except Exception as e:
            error_str = str(e).lower()
            if "not an integer" in error_str or "wrongtype" in error_str:
                raise CacheTypeError(
                    f"Value for key '{key}' is not a valid integer",
                    details={"key": key, "error": str(e)}
                ) from e
            logger.error(f"Redis DECR failed for key '{key}': {e}")
            raise CacheConnectionError(
                f"Failed to decrement key '{key}' in Redis",
                details={"key": key, "amount": amount, "error": str(e)}
            ) from e


# =============================================================================
# Redis Client Management (Internal)
# =============================================================================


async def _create_redis_client() -> Any:
    """Create and test a Redis client connection.
    
    This is an internal function used by the cache backend factory.
    External code should use get_cache_backend() instead.
    
    Returns:
        An async Redis client instance.
        
    Raises:
        CacheConnectionError: If Redis connection fails.
    """
    from .config import get_settings_instance
    
    settings = get_settings_instance()
    
    try:
        client = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=settings.redis_socket_timeout,
            socket_connect_timeout=settings.redis_connection_timeout
        )
        
        # Test connection
        await client.ping()
        logger.info("Redis client initialized successfully", extra={
            "redis_url": settings.redis_url,
            "connection_timeout": settings.redis_connection_timeout,
            "socket_timeout": settings.redis_socket_timeout
        })
        
        return client
        
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}")
        raise CacheConnectionError(
            f"Redis connection failed: {e}",
            details={"redis_url": settings.redis_url, "error": str(e)}
        ) from e


async def _get_redis_client() -> Any:
    """Get or create the Redis client (internal singleton).
    
    This is an internal function. External code should use get_cache_backend().
    
    Returns:
        An async Redis client instance.
        
    Raises:
        CacheConnectionError: If Redis connection fails.
    """
    global _redis_client
    
    if _redis_client is None:
        _redis_client = await _create_redis_client()
    
    return _redis_client


# =============================================================================
# Cache Backend Factory
# =============================================================================


async def get_cache_backend() -> CacheBackend:
    """Get the configured cache backend (singleton).
    
    Selection logic:
    1. If SHU_REDIS_URL is set and Redis is reachable -> RedisCacheBackend
    2. If SHU_REDIS_URL is set but unreachable and fallback enabled -> InMemoryCacheBackend (with warning)
    3. If SHU_REDIS_URL is not set -> InMemoryCacheBackend
    
    This function is suitable for use in background tasks, schedulers, and
    other non-FastAPI code. For FastAPI endpoints, prefer using
    get_cache_backend_dependency() with Depends().
    
    Returns:
        The configured CacheBackend instance.
        
    Raises:
        CacheConnectionError: If Redis is required but unavailable.
    
    Example:
        backend = await get_cache_backend()
        await backend.set("key", "value", ttl_seconds=300)
    """
    global _cache_backend
    
    if _cache_backend is not None:
        return _cache_backend
    
    from .config import get_settings_instance
    
    settings = get_settings_instance()
    
    # Check if Redis URL is configured
    redis_url = settings.redis_url
    if not redis_url or redis_url == "redis://localhost:6379":
        # Check if this is a default/unconfigured value
        # If redis_required is False and no explicit URL, use in-memory
        if not settings.redis_required:
            logger.info("No Redis URL configured, using InMemoryCacheBackend")
            _cache_backend = InMemoryCacheBackend()
            return _cache_backend
    
    # Try to connect to Redis
    try:
        redis_client = await _get_redis_client()
        _cache_backend = RedisCacheBackend(redis_client)
        logger.info("Using RedisCacheBackend")
        return _cache_backend
        
    except CacheConnectionError as e:
        if settings.redis_required:
            logger.error("Redis is required but connection failed", extra={
                "redis_url": settings.redis_url,
                "error": str(e)
            })
            raise CacheConnectionError(
                f"Redis is required but connection failed: {e}. "
                f"Please ensure Redis is running and accessible at {settings.redis_url}"
            ) from e
        
        if not settings.redis_fallback_enabled:
            logger.error("Redis fallback is disabled and Redis connection failed", extra={
                "redis_url": settings.redis_url,
                "error": str(e)
            })
            raise CacheConnectionError(
                f"Redis connection failed and fallback is disabled: {e}. "
                f"Please enable Redis fallback or ensure Redis is running at {settings.redis_url}"
            ) from e
        
        # Fall back to in-memory
        logger.warning(
            "Redis connection failed, falling back to InMemoryCacheBackend",
            extra={"redis_url": settings.redis_url, "error": str(e)}
        )
        _cache_backend = InMemoryCacheBackend()
        return _cache_backend


def get_cache_backend_dependency() -> CacheBackend:
    """Dependency injection function for CacheBackend.
    
    Use this in FastAPI endpoints for better testability and loose coupling.
    This follows the same pattern as get_config_manager_dependency().
    
    Note: This returns a new InMemoryCacheBackend instance for each call
    when Redis is not available. For production use with Redis, the
    RedisCacheBackend wraps a shared Redis client.
    
    Example:
        from fastapi import Depends
        from shu.core.cache_backend import get_cache_backend_dependency, CacheBackend
        
        async def my_endpoint(
            cache: CacheBackend = Depends(get_cache_backend_dependency)
        ):
            await cache.set("key", "value", ttl_seconds=300)
            value = await cache.get("key")
    
    Returns:
        A CacheBackend instance.
    """
    # For dependency injection, we return a fresh instance
    # This allows for easier testing and follows DEVELOPMENT_STANDARDS.md
    # The actual backend selection happens based on settings
    from .config import get_settings_instance
    
    settings = get_settings_instance()
    
    # For synchronous dependency injection, we can't await Redis connection
    # So we check if we already have a cached backend
    global _cache_backend
    
    if _cache_backend is not None:
        return _cache_backend
    
    # If no cached backend, return InMemoryCacheBackend
    # The async get_cache_backend() should be called during app startup
    # to initialize the proper backend
    logger.debug("get_cache_backend_dependency called before async initialization, using InMemoryCacheBackend")
    return InMemoryCacheBackend()


async def initialize_cache_backend() -> CacheBackend:
    """Initialize the cache backend during application startup.
    
    This should be called during FastAPI application startup to ensure
    the cache backend is properly initialized before handling requests.
    
    Example:
        @app.on_event("startup")
        async def startup():
            await initialize_cache_backend()
    
    Returns:
        The initialized CacheBackend instance.
    """
    return await get_cache_backend()


def reset_cache_backend() -> None:
    """Reset the cache backend singleton (for testing only).
    
    This function is intended for use in tests to reset the global state
    between test cases.
    """
    global _cache_backend, _redis_client
    _cache_backend = None
    _redis_client = None
