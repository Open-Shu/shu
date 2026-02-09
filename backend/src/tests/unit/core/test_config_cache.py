"""
Unit tests for ConfigCache migration to CacheBackend.

Tests verify that ConfigCache correctly uses CacheBackend for storage
and maintains its existing public API.
"""

import json
from unittest.mock import AsyncMock

import pytest

from shu.core.cache import ConfigCache, get_config_cache, get_config_cache_dependency
from shu.core.cache_backend import CacheBackend, InMemoryCacheBackend


class TestConfigCache:
    """Test ConfigCache functionality with CacheBackend."""

    @pytest.fixture
    def mock_backend(self):
        """Create a mock CacheBackend for testing."""
        backend = AsyncMock(spec=CacheBackend)
        return backend

    @pytest.fixture
    def config_cache_with_mock(self, mock_backend):
        """Create ConfigCache with mock backend."""
        return ConfigCache(cache_backend=mock_backend)

    @pytest.fixture
    def config_cache_with_inmemory(self):
        """Create ConfigCache with InMemoryCacheBackend for integration tests."""
        backend = InMemoryCacheBackend()
        return ConfigCache(cache_backend=backend)

    @pytest.mark.asyncio
    async def test_get_rag_config_cache_hit(self, config_cache_with_mock, mock_backend):
        """Test get_rag_config returns cached data when available."""
        # Arrange
        kb_id = "test_kb_123"
        expected_config = {"model": "gpt-4", "temperature": 0.7}
        mock_backend.get.return_value = json.dumps(expected_config)

        # Act
        result = await config_cache_with_mock.get_rag_config(kb_id)

        # Assert
        assert result == expected_config
        mock_backend.get.assert_called_once_with("config:rag:test_kb_123")

    @pytest.mark.asyncio
    async def test_get_rag_config_cache_miss(self, config_cache_with_mock, mock_backend):
        """Test get_rag_config returns None when cache miss."""
        # Arrange
        kb_id = "test_kb_123"
        mock_backend.get.return_value = None

        # Act
        result = await config_cache_with_mock.get_rag_config(kb_id)

        # Assert
        assert result is None
        mock_backend.get.assert_called_once_with("config:rag:test_kb_123")

    @pytest.mark.asyncio
    async def test_set_rag_config(self, config_cache_with_mock, mock_backend):
        """Test set_rag_config stores data in cache with TTL."""
        # Arrange
        kb_id = "test_kb_123"
        config = {"model": "gpt-4", "temperature": 0.7}

        # Act
        await config_cache_with_mock.set_rag_config(kb_id, config)

        # Assert
        mock_backend.set.assert_called_once_with("config:rag:test_kb_123", json.dumps(config), ttl_seconds=300)

    @pytest.mark.asyncio
    async def test_get_prompt_template_cache_hit(self, config_cache_with_mock, mock_backend):
        """Test get_prompt_template returns cached data when available."""
        # Arrange
        template_name = "summarize"
        expected_template = {"prompt": "Summarize: {text}", "max_tokens": 150}
        mock_backend.get.return_value = json.dumps(expected_template)

        # Act
        result = await config_cache_with_mock.get_prompt_template(template_name)

        # Assert
        assert result == expected_template
        mock_backend.get.assert_called_once_with("config:prompt:summarize")

    @pytest.mark.asyncio
    async def test_get_prompt_template_cache_miss(self, config_cache_with_mock, mock_backend):
        """Test get_prompt_template returns None when cache miss."""
        # Arrange
        template_name = "summarize"
        mock_backend.get.return_value = None

        # Act
        result = await config_cache_with_mock.get_prompt_template(template_name)

        # Assert
        assert result is None
        mock_backend.get.assert_called_once_with("config:prompt:summarize")

    @pytest.mark.asyncio
    async def test_set_prompt_template(self, config_cache_with_mock, mock_backend):
        """Test set_prompt_template stores data in cache with TTL."""
        # Arrange
        template_name = "summarize"
        template = {"prompt": "Summarize: {text}", "max_tokens": 150}

        # Act
        await config_cache_with_mock.set_prompt_template(template_name, template)

        # Assert
        mock_backend.set.assert_called_once_with("config:prompt:summarize", json.dumps(template), ttl_seconds=300)

    def test_namespace_key_formatting(self, config_cache_with_mock):
        """Test that keys are properly namespaced."""
        cache = config_cache_with_mock

        # Test RAG key formatting
        rag_key = cache._make_rag_key("kb_123")
        assert rag_key == "config:rag:kb_123"

        # Test prompt key formatting
        prompt_key = cache._make_prompt_key("template_name")
        assert prompt_key == "config:prompt:template_name"

    @pytest.mark.asyncio
    async def test_error_handling_get_rag_config(self, config_cache_with_mock, mock_backend):
        """Test graceful error handling in get_rag_config."""
        # Arrange
        kb_id = "test_kb_123"
        mock_backend.get.side_effect = Exception("Cache error")

        # Act
        result = await config_cache_with_mock.get_rag_config(kb_id)

        # Assert
        assert result is None  # Should return None on error, not raise

    @pytest.mark.asyncio
    async def test_error_handling_set_rag_config(self, config_cache_with_mock, mock_backend):
        """Test graceful error handling in set_rag_config."""
        # Arrange
        kb_id = "test_kb_123"
        config = {"model": "gpt-4"}
        mock_backend.set.side_effect = Exception("Cache error")

        # Act & Assert - should not raise exception
        await config_cache_with_mock.set_rag_config(kb_id, config)

    @pytest.mark.asyncio
    async def test_get_stats(self, config_cache_with_mock):
        """Test get_stats returns basic cache information."""
        # Act
        stats = await config_cache_with_mock.get_stats()

        # Assert
        assert "cache_ttl" in stats
        assert "backend_type" in stats
        assert stats["cache_ttl"] == 300


class TestConfigCacheIntegration:
    """Integration tests with real InMemoryCacheBackend."""

    @pytest.fixture
    def config_cache(self):
        """Create ConfigCache with InMemoryCacheBackend."""
        backend = InMemoryCacheBackend()
        return ConfigCache(cache_backend=backend)

    @pytest.mark.asyncio
    async def test_rag_config_round_trip(self, config_cache):
        """Test storing and retrieving RAG config works end-to-end."""
        # Arrange
        kb_id = "test_kb_456"
        config = {
            "model": "gpt-4",
            "temperature": 0.7,
            "max_tokens": 1000,
            "system_prompt": "You are a helpful assistant.",
        }

        # Act
        await config_cache.set_rag_config(kb_id, config)
        result = await config_cache.get_rag_config(kb_id)

        # Assert
        assert result == config

    @pytest.mark.asyncio
    async def test_prompt_template_round_trip(self, config_cache):
        """Test storing and retrieving prompt template works end-to-end."""
        # Arrange
        template_name = "qa_template"
        template = {
            "prompt": "Answer the question: {question}\nContext: {context}",
            "max_tokens": 500,
            "temperature": 0.1,
        }

        # Act
        await config_cache.set_prompt_template(template_name, template)
        result = await config_cache.get_prompt_template(template_name)

        # Assert
        assert result == template

    @pytest.mark.asyncio
    async def test_ttl_expiration_behavior(self, config_cache):
        """Test TTL expiration behavior with InMemoryCacheBackend."""
        # Arrange
        kb_id = "test_kb_ttl"
        config = {"model": "gpt-3.5-turbo"}

        # Override TTL for faster testing
        config_cache._cache_ttl = 1  # 1 second

        # Act
        await config_cache.set_rag_config(kb_id, config)

        # Should be available immediately
        result1 = await config_cache.get_rag_config(kb_id)
        assert result1 == config

        # Wait for expiration (simulate with direct backend access)
        backend = await config_cache._get_backend()
        key = config_cache._make_rag_key(kb_id)

        # Manually delete the key to simulate expiration for testing
        await backend.delete(key)

        # Should be None after deletion (simulating expiration)
        result2 = await config_cache.get_rag_config(kb_id)
        assert result2 is None

    @pytest.mark.asyncio
    async def test_different_namespaces_isolated(self, config_cache):
        """Test that RAG configs and prompt templates use different namespaces."""
        # Arrange
        same_id = "test_123"
        rag_config = {"type": "rag", "model": "gpt-4"}
        prompt_template = {"type": "prompt", "template": "Hello {name}"}

        # Act
        await config_cache.set_rag_config(same_id, rag_config)
        await config_cache.set_prompt_template(same_id, prompt_template)

        # Assert - both should be retrievable independently
        retrieved_rag = await config_cache.get_rag_config(same_id)
        retrieved_prompt = await config_cache.get_prompt_template(same_id)

        assert retrieved_rag == rag_config
        assert retrieved_prompt == prompt_template
        assert retrieved_rag != retrieved_prompt


class TestConfigCacheFactoryFunctions:
    """Test factory functions for ConfigCache."""

    def test_get_config_cache_returns_singleton(self):
        """Test that get_config_cache returns the same instance."""
        cache1 = get_config_cache()
        cache2 = get_config_cache()
        assert cache1 is cache2

    def test_get_config_cache_dependency_returns_new_instance(self):
        """Test that get_config_cache_dependency returns new instances."""
        cache1 = get_config_cache_dependency()
        cache2 = get_config_cache_dependency()
        assert cache1 is not cache2
        assert isinstance(cache1, ConfigCache)
        assert isinstance(cache2, ConfigCache)
