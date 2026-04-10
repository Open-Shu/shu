"""Unit tests for EmbeddingService protocol and DI wiring.

Tests cover:
- EmbeddingService protocol conformance
- Protocol extraction (embedding_protocol.py) backward compatibility
- _EmbeddingServiceManager caching and eviction logic
- DI wiring (get_embedding_service, reset, dependency)
- Service resolution logic (local vs external vs error)

Model-loading tests are excluded from this file — they require downloading
sentence-transformer models and are covered by integration tests.
"""

import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.core.embedding_service import (
    EmbeddingService,
    LocalEmbeddingService,
    _EmbeddingServiceManager,
    clear_embedding_service_cache,
    get_embedding_service,
    get_embedding_service_stats,
    reset_embedding_service,
)
from shu.core.external_model_resolver import ResolvedExternalModel

# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestEmbeddingServiceProtocol:
    """Verify that LocalEmbeddingService satisfies the EmbeddingService protocol."""

    def test_local_embedding_service_is_protocol_conformant(self):
        """A LocalEmbeddingService instance should satisfy EmbeddingService isinstance check."""
        # issubclass() doesn't work with protocols that have non-method members (properties),
        # so we test via isinstance on a mock instance with the right attributes.
        mock = _make_mock_service()
        mock.embed_texts = AsyncMock(return_value=[[0.0]])
        mock.embed_query = AsyncMock(return_value=[0.0])
        assert isinstance(mock, EmbeddingService)

    def test_mock_implementation_passes_isinstance_check(self):
        """Any object with the right attributes satisfies @runtime_checkable."""

        class FakeEmbeddingService:
            @property
            def dimension(self) -> int:
                return 384

            @property
            def model_name(self) -> str:
                return "fake-model"

            async def embed_texts(self, texts: list[str]) -> list[list[float]]:
                return [[0.0] * 384 for _ in texts]

            async def embed_query(self, text: str) -> list[float]:
                return [0.0] * 384

            async def embed_queries(self, texts: list[str]) -> list[list[float]]:
                return [[0.0] * 384 for _ in texts]

        assert isinstance(FakeEmbeddingService(), EmbeddingService)


# ---------------------------------------------------------------------------
# _EmbeddingServiceManager
# ---------------------------------------------------------------------------


def _make_mock_service(model_name: str = "test-model", dimension: int = 384) -> MagicMock:
    """Create a mock LocalEmbeddingService for manager tests."""
    svc = MagicMock(spec=LocalEmbeddingService)
    svc._model_name = model_name
    svc._dimension = dimension
    svc._model = MagicMock()
    svc.model_name = model_name
    svc.dimension = dimension
    return svc


class TestEmbeddingServiceManager:
    """Test the _EmbeddingServiceManager caching and eviction logic."""

    def _make_manager(self) -> _EmbeddingServiceManager:
        return _EmbeddingServiceManager()

    @patch("shu.core.embedding_service.LocalEmbeddingService")
    @patch("shu.core.embedding_service.get_settings_instance")
    def test_instance_caching(self, mock_settings, mock_cls):
        """Same model+device should return the same instance."""
        mock_settings.return_value.embedding_threads = 4
        mock_instance = _make_mock_service()
        mock_cls.return_value = mock_instance

        mgr = self._make_manager()
        svc1 = mgr.get_service("model-a", "cpu", batch_size=32)
        svc2 = mgr.get_service("model-a", "cpu", batch_size=32)

        assert svc1 is svc2
        assert mock_cls.call_count == 1  # Only created once

    @patch("shu.core.embedding_service.LocalEmbeddingService")
    @patch("shu.core.embedding_service.get_settings_instance")
    def test_different_models_different_instances(self, mock_settings, mock_cls):
        """Different models should produce different instances."""
        mock_settings.return_value.embedding_threads = 4
        mock_cls.side_effect = [_make_mock_service("model-a"), _make_mock_service("model-b")]

        mgr = self._make_manager()
        svc_a = mgr.get_service("model-a", "cpu", batch_size=32)
        svc_b = mgr.get_service("model-b", "cpu", batch_size=32)

        assert svc_a is not svc_b
        assert mgr.get_stats()["active_instances"] == 2

    @patch("shu.core.embedding_service.LocalEmbeddingService")
    @patch("shu.core.embedding_service.get_settings_instance")
    def test_ttl_eviction(self, mock_settings, mock_cls):
        """Expired instances should be evicted on next get_service call."""
        mock_settings.return_value.embedding_threads = 4
        mock_cls.return_value = _make_mock_service()

        mgr = self._make_manager()
        mgr._cache_ttl = 10  # 10 second TTL for test

        mgr.get_service("model-a", "cpu", batch_size=32)
        assert mgr.get_stats()["active_instances"] == 1

        # Simulate time passing beyond TTL
        for entry in mgr._instances.values():
            entry["last_used"] = time.time() - 20  # 20 seconds ago

        # Next call triggers cleanup
        mock_cls.return_value = _make_mock_service("model-b")
        mgr.get_service("model-b", "cpu", batch_size=32)

        # model-a should have been evicted, model-b is new
        assert mgr.get_stats()["active_instances"] == 1

    @patch("shu.core.embedding_service.LocalEmbeddingService")
    @patch("shu.core.embedding_service.get_settings_instance")
    def test_max_instance_eviction(self, mock_settings, mock_cls):
        """When max instances reached, oldest should be evicted."""
        mock_settings.return_value.embedding_threads = 4

        mgr = self._make_manager()
        mgr._max_instances = 2

        mock_cls.return_value = _make_mock_service("model-1")
        mgr.get_service("model-1", "cpu", batch_size=32)

        mock_cls.return_value = _make_mock_service("model-2")
        mgr.get_service("model-2", "cpu", batch_size=32)

        # Force model-1 to be "oldest" by back-dating last_used
        mgr._instances["model-1:cpu:float32"]["last_used"] = time.time() - 100

        mock_cls.return_value = _make_mock_service("model-3")
        mgr.get_service("model-3", "cpu", batch_size=32)

        assert mgr.get_stats()["active_instances"] == 2
        assert "model-1:cpu:float32" not in mgr._instances
        assert "model-2:cpu:float32" in mgr._instances
        assert "model-3:cpu:float32" in mgr._instances

    @patch("shu.core.embedding_service.LocalEmbeddingService")
    @patch("shu.core.embedding_service.get_settings_instance")
    def test_clear_all(self, mock_settings, mock_cls):
        """clear_all should remove all instances."""
        mock_settings.return_value.embedding_threads = 4
        mock_cls.return_value = _make_mock_service()

        mgr = self._make_manager()
        mgr.get_service("model-a", "cpu", batch_size=32)
        mgr.get_service("model-b", "cpu", batch_size=32)

        mgr.clear_all()

        assert mgr.get_stats()["active_instances"] == 0

    def test_get_stats_empty(self):
        """Stats on empty manager should report zero instances."""
        mgr = self._make_manager()
        stats = mgr.get_stats()
        assert stats["active_instances"] == 0
        assert stats["max_instances"] == 5
        assert stats["instances"] == {}


# ---------------------------------------------------------------------------
# DI wiring
# ---------------------------------------------------------------------------


class TestDIWiring:
    """Test the module-level DI functions."""

    def setup_method(self):
        """Reset singleton before each test."""
        reset_embedding_service()

    def teardown_method(self):
        """Clean up after each test."""
        reset_embedding_service()

    @pytest.mark.asyncio
    @patch("shu.core.embedding_service._service_manager")
    @patch("shu.core.embedding_service.get_settings_instance")
    async def test_get_embedding_service_returns_singleton(self, mock_settings, mock_manager):
        """Two calls to get_embedding_service should return the same instance."""
        settings = MagicMock()
        settings.default_embedding_model = "test-model"
        settings.embedding_device = "cpu"
        settings.embedding_batch_size = 32
        settings.embedding_dtype = "float32"
        settings.local_embedding_enabled = True
        mock_settings.return_value = settings

        mock_service = _make_mock_service()
        mock_manager.get_service.return_value = mock_service

        svc1 = await get_embedding_service()
        svc2 = await get_embedding_service()

        assert svc1 is svc2
        assert mock_manager.get_service.call_count == 1

    @pytest.mark.asyncio
    async def test_reset_clears_singleton(self):
        """reset_embedding_service should clear the cached instance."""
        import shu.core.embedding_service as mod

        # Set a fake cached value
        mod._embedding_service = _make_mock_service()
        assert mod._embedding_service is not None

        reset_embedding_service()
        assert mod._embedding_service is None

    def test_get_stats_returns_dict(self):
        """get_embedding_service_stats should return a dict with expected keys."""
        stats = get_embedding_service_stats()
        assert "active_instances" in stats
        assert "max_instances" in stats
        assert "instances" in stats

    def test_clear_cache_resets_singleton(self):
        """clear_embedding_service_cache should reset the module singleton."""
        import shu.core.embedding_service as mod

        mod._embedding_service = _make_mock_service()
        clear_embedding_service_cache()
        assert mod._embedding_service is None


# ---------------------------------------------------------------------------
# Service resolution (local vs external)
# ---------------------------------------------------------------------------


def _make_resolved_model(**overrides) -> ResolvedExternalModel:
    """Build a ResolvedExternalModel with sensible defaults."""
    defaults = {
        "model_id": "model-123",
        "model_name": "qwen/qwen3-embedding-8b",
        "provider_id": "provider-456",
        "provider_name": "OpenRouter",
        "api_base_url": "https://openrouter.ai/api/v1",
        "api_key": "test-key",
        "config": {"dimension": 1024},
    }
    defaults.update(overrides)
    return ResolvedExternalModel(**defaults)


class TestServiceResolution:
    """Test the resolution logic in get_embedding_service()."""

    def setup_method(self):
        reset_embedding_service()

    def teardown_method(self):
        reset_embedding_service()

    @pytest.mark.asyncio
    @patch("shu.core.embedding_service._service_manager")
    @patch("shu.core.embedding_service.get_settings_instance")
    async def test_local_enabled_uses_local(self, mock_settings, mock_manager):
        """When SHU_LOCAL_EMBEDDING_ENABLED=true, local service is used regardless of external models."""
        settings = MagicMock()
        settings.local_embedding_enabled = True
        settings.default_embedding_model = "test-model"
        settings.embedding_device = "cpu"
        settings.embedding_batch_size = 32
        settings.embedding_dtype = "float32"
        mock_settings.return_value = settings

        mock_service = _make_mock_service()
        mock_manager.get_service.return_value = mock_service

        svc = await get_embedding_service()

        assert svc is mock_service
        mock_manager.get_service.assert_called_once()

    @pytest.mark.asyncio
    @patch("shu.core.embedding_service.resolve_external_model")
    @patch("shu.core.embedding_service.get_settings_instance")
    async def test_local_disabled_with_external_model_uses_external(self, mock_settings, mock_resolve):
        """When local is disabled and an external embedding model is configured, use ExternalEmbeddingService."""
        settings = MagicMock()
        settings.local_embedding_enabled = False
        mock_settings.return_value = settings

        mock_resolve.return_value = _make_resolved_model()

        svc = await get_embedding_service()

        from shu.services.external_embedding_service import ExternalEmbeddingService

        assert isinstance(svc, ExternalEmbeddingService)
        assert svc.model_name == "qwen/qwen3-embedding-8b"
        assert svc.dimension == 1024
        mock_resolve.assert_called_once_with("embedding")

    @pytest.mark.asyncio
    @patch("shu.core.embedding_service.resolve_external_model")
    @patch("shu.core.embedding_service.get_settings_instance")
    async def test_local_disabled_no_external_model_raises(self, mock_settings, mock_resolve):
        """When local is disabled and no external model exists, raise LLMConfigurationError."""
        settings = MagicMock()
        settings.local_embedding_enabled = False
        mock_settings.return_value = settings

        mock_resolve.return_value = None

        from shu.core.exceptions import LLMConfigurationError

        with pytest.raises(LLMConfigurationError, match="no external embedding model"):
            await get_embedding_service()

    @pytest.mark.asyncio
    @patch("shu.core.embedding_service.resolve_external_model")
    @patch("shu.core.embedding_service.get_settings_instance")
    async def test_local_disabled_external_model_missing_dimension_raises(self, mock_settings, mock_resolve):
        """When the external model has no dimension in config, raise LLMConfigurationError."""
        settings = MagicMock()
        settings.local_embedding_enabled = False
        mock_settings.return_value = settings

        mock_resolve.return_value = _make_resolved_model(config={})

        from shu.core.exceptions import LLMConfigurationError

        with pytest.raises(LLMConfigurationError, match="missing 'dimension'"):
            await get_embedding_service()

    @pytest.mark.asyncio
    @patch("shu.core.embedding_service.resolve_external_model")
    @patch("shu.core.embedding_service.get_settings_instance")
    async def test_external_singleton_returns_same_instance(self, mock_settings, mock_resolve):
        """External service should be cached as a singleton across calls."""
        settings = MagicMock()
        settings.local_embedding_enabled = False
        mock_settings.return_value = settings

        mock_resolve.return_value = _make_resolved_model()

        svc1 = await get_embedding_service()
        svc2 = await get_embedding_service()

        assert svc1 is svc2
        # resolve_external_model should only be called once (singleton caches)
        mock_resolve.assert_called_once()


# ---------------------------------------------------------------------------
# Protocol extraction (embedding_protocol.py)
# ---------------------------------------------------------------------------


class TestProtocolExtraction:
    """Verify that the protocol extraction to embedding_protocol.py works correctly."""

    def test_import_from_embedding_protocol(self):
        """EmbeddingService should be importable from embedding_protocol directly."""
        from shu.core.embedding_protocol import EmbeddingService as ProtoService

        assert ProtoService is not None

    def test_reexport_is_same_class(self):
        """EmbeddingService imported from embedding_service should be the same class as from embedding_protocol."""
        from shu.core.embedding_protocol import EmbeddingService as ProtoService

        assert EmbeddingService is ProtoService

    def test_protocol_import_does_not_load_sentence_transformers(self):
        """Importing embedding_protocol should NOT load sentence_transformers into sys.modules.

        This is the core purpose of the extraction — external embedding
        backends can import the protocol without triggering ~2GB of model
        loading from sentence-transformers.
        """
        # sentence_transformers may already be loaded from other test
        # imports in this process, so we check whether importing the
        # protocol module itself triggers a NEW load. We verify by
        # checking the module's own imports have no dependency on it.
        import importlib

        import shu.core.embedding_protocol as proto_mod

        # Re-import from scratch to verify the module source has no
        # sentence_transformers reference
        source = importlib.util.find_spec("shu.core.embedding_protocol")
        assert source is not None
        with open(source.origin) as f:
            source_code = f.read()
        assert "sentence_transformers" not in source_code, (
            "embedding_protocol.py must not reference sentence_transformers"
        )
        assert "import sentence" not in source_code

    def test_protocol_conformance_via_embedding_protocol(self):
        """A conforming class should pass isinstance check with the protocol from embedding_protocol."""
        from shu.core.embedding_protocol import EmbeddingService as ProtoService

        class ConformingService:
            @property
            def dimension(self) -> int:
                return 768

            @property
            def model_name(self) -> str:
                return "test"

            async def embed_texts(self, texts: list[str]) -> list[list[float]]:
                return []

            async def embed_query(self, text: str) -> list[float]:
                return []

            async def embed_queries(self, texts: list[str]) -> list[list[float]]:
                return []

        assert isinstance(ConformingService(), ProtoService)
