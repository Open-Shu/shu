"""
In-Memory Redis Client for Progress Tracking Fallback

This module provides an in-memory Redis client that implements the same interface
as the real Redis client, allowing the system to work without Redis.
"""

import time
import json
import asyncio
from typing import Dict, Any, Optional, List, Set
from collections import defaultdict
from .logging import get_logger

logger = get_logger(__name__)


class InMemoryRedisClient:
    """In-memory Redis client for progress tracking fallback."""
    
    def __init__(self):
        self._data: Dict[str, Any] = {}
        self._expiry: Dict[str, float] = {}
        self._sets: Dict[str, Set[str]] = defaultdict(set)
        self._pubsub_channels: Dict[str, List[asyncio.Queue]] = defaultdict(list)
        
    async def ping(self) -> str:
        """Ping the in-memory Redis client."""
        return "PONG"
    
    async def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        """Set a key-value pair with optional expiration."""
        self._data[key] = value
        if ex:
            self._expiry[key] = time.time() + ex
        return True
    
    async def setex(self, key: str, ex: int, value: str) -> bool:
        """Set a key-value pair with expiration."""
        return await self.set(key, value, ex)
    
    async def get(self, key: str) -> Optional[str]:
        """Get a value by key."""
        # Check expiration
        if key in self._expiry and time.time() > self._expiry[key]:
            del self._data[key]
            del self._expiry[key]
            return None
        
        return self._data.get(key)
    
    async def hset(self, key: str, mapping: Dict[str, str]) -> int:
        """Set multiple hash fields."""
        if key not in self._data:
            self._data[key] = {}
        
        if not isinstance(self._data[key], dict):
            self._data[key] = {}
        
        self._data[key].update(mapping)
        return len(mapping)
    
    async def hget(self, key: str, field: str) -> Optional[str]:
        """Get a hash field value."""
        if key not in self._data or not isinstance(self._data[key], dict):
            return None
        
        return self._data[key].get(field)
    
    async def expire(self, key: str, seconds: int) -> bool:
        """
        Set a time-to-live for a key.
        
        Records an expiration timestamp seconds from now for the given key; this does not check whether the key currently exists.
        
        Parameters:
            key (str): The key to expire.
            seconds (int): Number of seconds from now when the key should expire.
        
        Returns:
            bool: `True` if the expiration was set.
        """
        self._expiry[key] = time.time() + seconds
        return True

    async def incr(self, key: str) -> int:
        """
        Increase the integer value stored at key by one.
        
        If the key does not exist or has expired it is treated as 0. Numeric string values are coerced to int before incrementing. The new value is stored and returned.
        Returns:
            int: New integer value stored at the key after increment.
        """
        return await self.incrby(key, 1)

    async def incrby(self, key: str, amount: int) -> int:
        """
        Increment the numeric value stored at key by the given amount.
        
        If the key has an expiration and is expired, the key is removed before the increment. If the existing value is a string it will be converted to an integer. The resulting value is stored back at the key.
        
        Parameters:
            key (str): The key whose value will be incremented.
            amount (int): The amount to add to the key's current value.
        
        Returns:
            int: The new value after applying the increment.
        """
        # Check expiration first
        if key in self._expiry and time.time() > self._expiry[key]:
            del self._data[key]
            del self._expiry[key]

        current = self._data.get(key, 0)
        if isinstance(current, str):
            try:
                current = int(current)
            except ValueError as err:
                raise ValueError("ERR value is not an integer or out of range") from err
        new_value = current + amount
        self._data[key] = new_value
        return new_value
    
    async def delete(self, *keys: str) -> int:
        """
        Remove one or more keys from the in-memory store.
        
        Parameters:
        	keys (str): One or more key names to delete.
        
        Returns:
        	deleted (int): Number of keys that were removed. Expiration entries associated with removed keys are also cleared.
        """
        deleted = 0
        for key in keys:
            if key in self._data:
                del self._data[key]
                deleted += 1
            if key in self._expiry:
                del self._expiry[key]
        return deleted
    
    async def sadd(self, key: str, *members: str) -> int:
        """Add members to a set."""
        added = 0
        for member in members:
            if member not in self._sets[key]:
                self._sets[key].add(member)
                added += 1
        return added
    
    async def srem(self, key: str, *members: str) -> int:
        """Remove members from a set."""
        removed = 0
        for member in members:
            if member in self._sets[key]:
                self._sets[key].discard(member)
                removed += 1
        return removed
    
    async def smembers(self, key: str) -> Set[str]:
        """Get all members of a set."""
        return self._sets[key].copy()
    
    async def keys(self, pattern: str) -> List[str]:
        """Get keys matching a pattern."""
        # Simple pattern matching for common cases
        if pattern == "*":
            return list(self._data.keys())
        elif pattern.endswith("*"):
            prefix = pattern[:-1]
            return [key for key in self._data.keys() if key.startswith(prefix)]
        else:
            return [key for key in self._data.keys() if key == pattern]
    
    async def publish(self, channel: str, message: str) -> int:
        """Publish a message to a channel."""
        if channel in self._pubsub_channels:
            for queue in self._pubsub_channels[channel]:
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    # Remove full queues
                    self._pubsub_channels[channel].remove(queue)
        return len(self._pubsub_channels.get(channel, []))
    
    def pubsub(self):
        """Get a pubsub object."""
        return InMemoryPubSub(self._pubsub_channels)
    
    async def close(self):
        """Close the in-memory Redis client."""
        # Clear all data
        self._data.clear()
        self._expiry.clear()
        self._sets.clear()
        self._pubsub_channels.clear()
    
    def __str__(self) -> str:
        return f"InMemoryRedisClient(data_size={len(self._data)}, sets={len(self._sets)})"


class InMemoryPubSub:
    """In-memory pubsub object."""
    
    def __init__(self, channels: Dict[str, List[asyncio.Queue]]):
        self._channels = channels
        self._subscribed_channels: Set[str] = set()
    
    async def subscribe(self, *channels: str):
        """Subscribe to channels."""
        for channel in channels:
            if channel not in self._channels:
                self._channels[channel] = []
            self._subscribed_channels.add(channel)
    
    async def listen(self):
        """Listen for messages."""
        if not self._subscribed_channels:
            return
        
        # Create a queue for this listener
        queue = asyncio.Queue()
        for channel in self._subscribed_channels:
            self._channels[channel].append(queue)
        
        try:
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield {
                        'type': 'message',
                        'channel': 'unknown',  # We don't track which channel
                        'data': message
                    }
                except asyncio.TimeoutError:
                    # Yield a keepalive message
                    yield {
                        'type': 'keepalive',
                        'channel': None,
                        'data': None
                    }
        finally:
            # Remove this queue from all channels
            for channel in self._subscribed_channels:
                if channel in self._channels and queue in self._channels[channel]:
                    self._channels[channel].remove(queue)
    
    async def unsubscribe(self, *channels: str):
        """Unsubscribe from channels."""
        for channel in channels:
            self._subscribed_channels.discard(channel)
    
    async def close(self):
        """Close the pubsub object."""
        self._subscribed_channels.clear() 