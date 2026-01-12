"""
Configuration caching for Shu RAG Backend.

This module provides caching for frequently accessed configurations
like RAG configs and prompt templates using the unified CacheBackend interface.
"""

import json
from typing import Dict, Any, Optional
import logging
import asyncio

from .config import get_settings_instance
from .logging import get_logger
from .cache_backend import get_cache_backend, CacheBackend

logger = get_logger(__name__)

class ConfigCache:
    """Cache for frequently accessed configurations using CacheBackend.
    
    This class provides a domain-specific interface for caching RAG configurations
    and prompt templates while delegating storage to the unified CacheBackend.
    
    Key namespaces:
    - RAG configs: config:rag:{kb_id}
    - Prompt templates: config:prompt:{template_name}
    """
    
    def __init__(self, cache_backend: Optional[CacheBackend] = None):
        """Initialize ConfigCache with optional CacheBackend dependency injection.
        
        Args:
            cache_backend: Optional CacheBackend instance for dependency injection.
                If None, will use get_cache_backend() to obtain the backend.
        """
        self._settings = get_settings_instance()
        self._cache_backend = cache_backend
        self._cache_ttl = 300  # 5 minutes default

    async def _get_backend(self) -> CacheBackend:
        """Get the cache backend instance."""
        if self._cache_backend is not None:
            return self._cache_backend
        return await get_cache_backend()

    def _make_rag_key(self, kb_id: str) -> str:
        """Create namespaced key for RAG config."""
        return f"config:rag:{kb_id}"

    def _make_prompt_key(self, template_name: str) -> str:
        """Create namespaced key for prompt template."""
        return f"config:prompt:{template_name}"

    async def get_rag_config(self, kb_id: str) -> Optional[Dict[str, Any]]:
        """Get cached RAG configuration (called on every query).
        
        Args:
            kb_id: Knowledge base identifier.
            
        Returns:
            Cached RAG configuration or None if not found/expired.
        """
        try:
            backend = await self._get_backend()
            key = self._make_rag_key(kb_id)
            cached_value = await backend.get(key)
            
            if cached_value is not None:
                logger.debug(f"Cache hit for RAG config: {kb_id}")
                return json.loads(cached_value)
            
            logger.debug(f"Cache miss for RAG config: {kb_id}")
            return None
            
        except Exception as e:
            logger.warning(f"Failed to get RAG config from cache for {kb_id}: {e}")
            return None
    
    async def set_rag_config(self, kb_id: str, config: Dict[str, Any]):
        """Cache RAG configuration.
        
        Args:
            kb_id: Knowledge base identifier.
            config: RAG configuration to cache.
        """
        try:
            backend = await self._get_backend()
            key = self._make_rag_key(kb_id)
            value = json.dumps(config)
            
            await backend.set(key, value, ttl_seconds=self._cache_ttl)
            logger.debug(f"Cached RAG config: {kb_id}")
            
        except Exception as e:
            logger.warning(f"Failed to cache RAG config for {kb_id}: {e}")
    
    async def get_prompt_template(self, template_name: str) -> Optional[Dict[str, Any]]:
        """Get cached prompt template (called on every LLM request).
        
        Args:
            template_name: Name of the prompt template.
            
        Returns:
            Cached prompt template or None if not found/expired.
        """
        try:
            backend = await self._get_backend()
            key = self._make_prompt_key(template_name)
            cached_value = await backend.get(key)
            
            if cached_value is not None:
                logger.debug(f"Cache hit for prompt template: {template_name}")
                return json.loads(cached_value)
            
            logger.debug(f"Cache miss for prompt template: {template_name}")
            return None
            
        except Exception as e:
            logger.warning(f"Failed to get prompt template from cache for {template_name}: {e}")
            return None
    
    async def set_prompt_template(self, template_name: str, template: Dict[str, Any]):
        """Cache prompt template.
        
        Args:
            template_name: Name of the prompt template.
            template: Prompt template to cache.
        """
        try:
            backend = await self._get_backend()
            key = self._make_prompt_key(template_name)
            value = json.dumps(template)
            
            await backend.set(key, value, ttl_seconds=self._cache_ttl)
            logger.debug(f"Cached prompt template: {template_name}")
            
        except Exception as e:
            logger.warning(f"Failed to cache prompt template for {template_name}: {e}")

    async def clear_all(self):
        """Clear all cached data.
        
        Note: This only clears data with our namespace prefixes.
        Other cache data is not affected.
        """
        try:
            backend = await self._get_backend()
            
            # Since CacheBackend doesn't have a pattern-based delete,
            # we'll need to track keys or implement a different approach
            # For now, we'll log that this operation is not fully supported
            logger.warning("clear_all() is not fully supported with CacheBackend - "
                         "individual keys must be deleted explicitly")
            
        except Exception as e:
            logger.warning(f"Failed to clear cache: {e}")

    async def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics.
        
        Note: With CacheBackend, we can't easily count keys by namespace,
        so this returns basic information.
        
        Returns:
            Dict containing cache_ttl, backend_type, and optionally an error field
            if the backend could not be acquired.
        """
        try:
            backend = await self._get_backend()
            return {
                'cache_ttl': self._cache_ttl,
                'backend_type': type(backend).__name__,
            }
        except Exception as e:
            logger.warning(f"Failed to get cache backend for stats: {e}")
            return {
                'cache_ttl': self._cache_ttl,
                'backend_type': None,
                'error': str(e),
            }


# Global cache instance
config_cache = ConfigCache()


def get_config_cache() -> ConfigCache:
    """Get the global configuration cache instance.
    
    Note: The returned ConfigCache instance has async methods.
    Callers must use await when calling get_rag_config, set_rag_config, etc.
    
    Example:
        cache = get_config_cache()
        config = await cache.get_rag_config(kb_id)
        await cache.set_rag_config(kb_id, config)
    """
    return config_cache


def get_config_cache_dependency() -> ConfigCache:
    """Dependency injection function for ConfigCache.
    
    Use this in FastAPI endpoints for better testability and loose coupling.
    
    Example:
        from fastapi import Depends
        from shu.core.cache import get_config_cache_dependency, ConfigCache
        
        async def my_endpoint(
            cache: ConfigCache = Depends(get_config_cache_dependency)
        ):
            config = await cache.get_rag_config(kb_id)
    
    Returns:
        A ConfigCache instance.
    """
    return ConfigCache() 