"""
Configuration caching for Shu RAG Backend.

This module provides caching for frequently accessed configurations
like RAG configs and prompt templates.
"""

from functools import lru_cache
from typing import Dict, Any, Optional
import time
import logging

from .config import get_settings_instance
from .logging import get_logger

logger = get_logger(__name__)

class ConfigCache:
    """Cache for frequently accessed configurations."""
    
    def __init__(self):
        self._settings = get_settings_instance()
        self._rag_configs: Dict[str, Any] = {}
        self._prompt_templates: Dict[str, Any] = {}
        self._cache_ttl = 300  # 5 minutes default
        self._last_cleanup = time.time()

    def get_rag_config(self, kb_id: str) -> Optional[Dict[str, Any]]:
        """Get cached RAG configuration (called on every query)."""
        current_time = time.time()
        
        # Clean old entries periodically
        if current_time - self._last_cleanup > 60:  # Cleanup every minute
            self._cleanup_expired_entries(current_time)
        
        if kb_id in self._rag_configs:
            entry = self._rag_configs[kb_id]
            if current_time - entry['timestamp'] < self._cache_ttl:
                logger.debug(f"Cache hit for RAG config: {kb_id}")
                return entry['data']
            else:
                del self._rag_configs[kb_id]
        
        logger.debug(f"Cache miss for RAG config: {kb_id}")
        return None
    
    def set_rag_config(self, kb_id: str, config: Dict[str, Any]):
        """Cache RAG configuration."""
        self._rag_configs[kb_id] = {
            'data': config,
            'timestamp': time.time()
        }
        logger.debug(f"Cached RAG config: {kb_id}")
    
    def get_prompt_template(self, template_name: str) -> Optional[Dict[str, Any]]:
        """Get cached prompt template (called on every LLM request)."""
        current_time = time.time()
        
        if template_name in self._prompt_templates:
            entry = self._prompt_templates[template_name]
            if current_time - entry['timestamp'] < self._cache_ttl:
                logger.debug(f"Cache hit for prompt template: {template_name}")
                return entry['data']
            else:
                del self._prompt_templates[template_name]
        
        logger.debug(f"Cache miss for prompt template: {template_name}")
        return None
    
    def set_prompt_template(self, template_name: str, template: Dict[str, Any]):
        """Cache prompt template."""
        self._prompt_templates[template_name] = {
            'data': template,
            'timestamp': time.time()
        }
        logger.debug(f"Cached prompt template: {template_name}")
    
    def _cleanup_expired_entries(self, current_time: float):
        """Remove expired cache entries."""
        # Clean RAG configs
        expired_keys = [
            key for key, entry in self._rag_configs.items()
            if current_time - entry['timestamp'] >= self._cache_ttl
        ]
        for key in expired_keys:
            del self._rag_configs[key]

        # Clean prompt templates
        expired_keys = [
            key for key, entry in self._prompt_templates.items()
            if current_time - entry['timestamp'] >= self._cache_ttl
        ]
        for key in expired_keys:
            del self._prompt_templates[key]

        self._last_cleanup = current_time
        logger.debug(
            "Cache cleanup completed. RAG: %d, Prompts: %d",
            len(self._rag_configs),
            len(self._prompt_templates),
        )

    def clear_all(self):
        """Clear all cached data."""
        self._rag_configs.clear()
        self._prompt_templates.clear()
        logger.info("All configuration cache cleared")

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            'rag_configs': len(self._rag_configs),
            'prompt_templates': len(self._prompt_templates),
            'cache_ttl': self._cache_ttl,
        }


# Global cache instance
config_cache = ConfigCache()


def get_config_cache() -> ConfigCache:
    """Get the global configuration cache instance."""
    return config_cache 