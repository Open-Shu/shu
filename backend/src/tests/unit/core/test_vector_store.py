"""Unit tests for VectorStore protocol and PgVectorStore implementation.

Tests cover:
- VectorStore protocol conformance
- PgVectorStore search SQL generation and parameter handling
- PgVectorStore store_embeddings behavior
- PgVectorStore delete behavior
- PgVectorStore ensure_index behavior
- DI wiring (get_vector_store, reset, dependency)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.core.vector_store import (
    CollectionConfig,
    DistanceMetric,
    PgVectorStore,
    VectorEntry,
    VectorSearchResult,
    VectorStore,
    get_vector_store,
    get_vector_store_dependency,
    reset_vector_store,
)

# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestVectorStoreProtocol:
    """Verify that PgVectorStore satisfies the VectorStore protocol."""

    def test_pgvector_store_satisfies_protocol(self):
        """PgVectorStore instance should satisfy VectorStore isinstance check."""
        store = PgVectorStore()
        assert isinstance(store, VectorStore)

    def test_custom_implementation_satisfies_protocol(self):
        """Any object with the right methods satisfies @runtime_checkable."""

        class FakeVectorStore:
            async def search(self, collection, query_vector, *, db, **kwargs):
                return []

            async def store_embeddings(self, collection, entries, *, db):
                return 0

            async def delete(self, collection, ids, *, db):
                return 0

            async def ensure_index(self, collection, dimension, *, db, **kwargs):
                return False

        assert isinstance(FakeVectorStore(), VectorStore)


# -- Search ------------------------------------------------------------------


class TestPgVectorStoreSearch:
    """Test search SQL generation and parameter handling."""

    def _make_store(self) -> PgVectorStore:
        return PgVectorStore()

    @pytest.mark.asyncio
    async def test_search_executes_query(self):
        """search() should execute SQL and return VectorSearchResult objects."""
        store = self._make_store()
        mock_db = AsyncMock()

        # Mock the result set
        mock_row = MagicMock()
        mock_row.__getitem__ = MagicMock(side_effect=lambda i: ["chunk-1", 0.95][i])
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [mock_row]
        mock_db.execute = AsyncMock(return_value=mock_result)

        results = await store.search(
            "chunks",
            query_vector=[0.1] * 384,
            db=mock_db,
            limit=10,
            threshold=0.5,
            filters={"knowledge_base_id": "kb-123"},
        )

        assert mock_db.execute.call_count == 1
        assert len(results) == 1
        assert results[0].id == "chunk-1"
        assert results[0].score == 0.95

    @pytest.mark.asyncio
    async def test_search_unknown_collection_raises(self):
        """search() with unknown collection should raise ValueError."""
        store = self._make_store()
        mock_db = AsyncMock()

        with pytest.raises(ValueError, match="Unknown collection 'nonexistent'"):
            await store.search("nonexistent", query_vector=[0.1], db=mock_db)

    @pytest.mark.asyncio
    async def test_search_invalid_filter_column_raises(self):
        """search() with filter on non-filterable column should raise ValueError."""
        store = self._make_store()
        mock_db = AsyncMock()

        with pytest.raises(ValueError, match="Filter column 'bad_col' not allowed"):
            await store.search(
                "chunks",
                query_vector=[0.1],
                db=mock_db,
                filters={"bad_col": "value"},
            )

    @pytest.mark.asyncio
    async def test_search_empty_results(self):
        """search() should return empty list when no matches."""
        store = self._make_store()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        results = await store.search(
            "chunks",
            query_vector=[0.1] * 384,
            db=mock_db,
        )

        assert results == []

    @pytest.mark.asyncio
    async def test_search_sql_contains_filter(self):
        """search() SQL should include filter clauses."""
        store = self._make_store()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        await store.search(
            "chunks",
            query_vector=[0.1],
            db=mock_db,
            filters={"knowledge_base_id": "kb-1"},
        )

        # Verify the SQL text includes the filter
        call_args = mock_db.execute.call_args
        sql_text = str(call_args[0][0])
        assert "knowledge_base_id" in sql_text
        assert "f_knowledge_base_id" in sql_text

    @pytest.mark.asyncio
    async def test_search_sql_contains_extra_where(self):
        """search() SQL should include extra_where clause."""
        store = self._make_store()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        await store.search(
            "chunks",
            query_vector=[0.1],
            db=mock_db,
            extra_where="chunk_metadata->>'chunk_type' != 'title'",
        )

        call_args = mock_db.execute.call_args
        sql_text = str(call_args[0][0])
        assert "chunk_type" in sql_text

    @pytest.mark.asyncio
    async def test_search_uses_cosine_distance_operator(self):
        """search() on chunks collection should use <=> operator."""
        store = self._make_store()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        await store.search("chunks", query_vector=[0.1], db=mock_db)

        call_args = mock_db.execute.call_args
        sql_text = str(call_args[0][0])
        assert "<=>" in sql_text

    @pytest.mark.asyncio
    async def test_search_synopses_collection(self):
        """search() should work with synopses collection."""
        store = self._make_store()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        await store.search("synopses", query_vector=[0.1], db=mock_db)

        call_args = mock_db.execute.call_args
        sql_text = str(call_args[0][0])
        assert "synopsis_embedding" in sql_text
        assert "documents" in sql_text


# -- Store -------------------------------------------------------------------


class TestPgVectorStoreStore:
    """Test store_embeddings behavior."""

    @pytest.mark.asyncio
    async def test_store_embeddings_executes_updates(self):
        """store_embeddings() should execute UPDATE for each entry."""
        store = PgVectorStore()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_db.execute = AsyncMock(return_value=mock_result)

        entries = [
            VectorEntry(id="doc-1", vector=[0.1, 0.2, 0.3]),
            VectorEntry(id="doc-2", vector=[0.4, 0.5, 0.6]),
        ]

        count = await store.store_embeddings("synopses", entries, db=mock_db)

        assert count == 2
        assert mock_db.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_store_embeddings_empty_list(self):
        """store_embeddings() with empty list should return 0 without DB calls."""
        store = PgVectorStore()
        mock_db = AsyncMock()

        count = await store.store_embeddings("synopses", [], db=mock_db)

        assert count == 0
        mock_db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_store_embeddings_unknown_collection_raises(self):
        """store_embeddings() with unknown collection should raise ValueError."""
        store = PgVectorStore()
        mock_db = AsyncMock()

        with pytest.raises(ValueError, match="Unknown collection"):
            await store.store_embeddings(
                "nonexistent",
                [VectorEntry(id="x", vector=[0.1])],
                db=mock_db,
            )

    @pytest.mark.asyncio
    async def test_store_embeddings_sql_targets_correct_table(self):
        """store_embeddings() SQL should target the correct table and column."""
        store = PgVectorStore()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_db.execute = AsyncMock(return_value=mock_result)

        await store.store_embeddings(
            "queries",
            [VectorEntry(id="q-1", vector=[0.1])],
            db=mock_db,
        )

        call_args = mock_db.execute.call_args
        sql_text = str(call_args[0][0])
        assert "document_queries" in sql_text
        assert "query_embedding" in sql_text


# -- Delete ------------------------------------------------------------------


class TestPgVectorStoreDelete:
    """Test delete behavior."""

    @pytest.mark.asyncio
    async def test_delete_nullifies_embeddings(self):
        """delete() should SET embedding = NULL, not DELETE rows."""
        store = PgVectorStore()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 3
        mock_db.execute = AsyncMock(return_value=mock_result)

        count = await store.delete("chunks", ["c-1", "c-2", "c-3"], db=mock_db)

        assert count == 3
        call_args = mock_db.execute.call_args
        sql_text = str(call_args[0][0])
        assert "SET embedding = NULL" in sql_text
        assert "DELETE" not in sql_text

    @pytest.mark.asyncio
    async def test_delete_empty_ids(self):
        """delete() with empty ids should return 0 without DB calls."""
        store = PgVectorStore()
        mock_db = AsyncMock()

        count = await store.delete("chunks", [], db=mock_db)

        assert count == 0
        mock_db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_unknown_collection_raises(self):
        """delete() with unknown collection should raise ValueError."""
        store = PgVectorStore()
        mock_db = AsyncMock()

        with pytest.raises(ValueError, match="Unknown collection"):
            await store.delete("nonexistent", ["id-1"], db=mock_db)


# -- Ensure index ------------------------------------------------------------


class TestPgVectorStoreEnsureIndex:
    """Test ensure_index behavior."""

    @pytest.mark.asyncio
    async def test_ensure_index_skips_existing(self):
        """ensure_index() should return False if index already exists."""
        store = PgVectorStore()
        mock_db = AsyncMock()

        # First execute: index check returns a row (index exists)
        mock_check_result = MagicMock()
        mock_check_result.scalar_one_or_none.return_value = 1
        mock_db.execute = AsyncMock(return_value=mock_check_result)

        created = await store.ensure_index("chunks", 384, db=mock_db)

        assert created is False
        assert mock_db.execute.call_count == 1  # Only the check query

    @pytest.mark.asyncio
    async def test_ensure_index_creates_ivfflat(self):
        """ensure_index() should create IVFFlat index when missing."""
        store = PgVectorStore(index_type="ivfflat", index_lists=100)
        mock_db = AsyncMock()

        # First call: index check (no index found)
        mock_check_result = MagicMock()
        mock_check_result.scalar_one_or_none.return_value = None
        # Second call: row count
        mock_count_result = MagicMock()
        mock_count_result.scalar_one.return_value = 500
        # Third call: CREATE INDEX
        mock_create_result = MagicMock()

        mock_db.execute = AsyncMock(
            side_effect=[mock_check_result, mock_count_result, mock_create_result]
        )

        created = await store.ensure_index("chunks", 384, db=mock_db)

        assert created is True
        assert mock_db.execute.call_count == 3
        # Verify the CREATE INDEX SQL
        create_call = mock_db.execute.call_args_list[2]
        sql_text = str(create_call[0][0])
        assert "ivfflat" in sql_text
        assert "vector_cosine_ops" in sql_text
        assert "idx_document_chunks_embedding" in sql_text

    @pytest.mark.asyncio
    async def test_ensure_index_creates_hnsw(self):
        """ensure_index() should create HNSW index when requested."""
        store = PgVectorStore(index_type="hnsw")
        mock_db = AsyncMock()

        mock_check_result = MagicMock()
        mock_check_result.scalar_one_or_none.return_value = None
        mock_count_result = MagicMock()
        mock_count_result.scalar_one.return_value = 10
        mock_create_result = MagicMock()

        mock_db.execute = AsyncMock(
            side_effect=[mock_check_result, mock_count_result, mock_create_result]
        )

        created = await store.ensure_index("chunks", 384, db=mock_db)

        assert created is True
        create_call = mock_db.execute.call_args_list[2]
        sql_text = str(create_call[0][0])
        assert "hnsw" in sql_text

    @pytest.mark.asyncio
    async def test_ensure_index_unsupported_type_raises(self):
        """ensure_index() with unsupported index type should raise ValueError."""
        store = PgVectorStore()
        mock_db = AsyncMock()

        mock_check_result = MagicMock()
        mock_check_result.scalar_one_or_none.return_value = None
        mock_count_result = MagicMock()
        mock_count_result.scalar_one.return_value = 0
        mock_db.execute = AsyncMock(
            side_effect=[mock_check_result, mock_count_result]
        )

        with pytest.raises(ValueError, match="Unsupported index type"):
            await store.ensure_index("chunks", 384, db=mock_db, index_type="bad_type")

    @pytest.mark.asyncio
    async def test_ensure_index_warns_on_large_table(self):
        """ensure_index() should log warning for tables with >100k rows."""
        store = PgVectorStore()
        mock_db = AsyncMock()

        mock_check_result = MagicMock()
        mock_check_result.scalar_one_or_none.return_value = None
        mock_count_result = MagicMock()
        mock_count_result.scalar_one.return_value = 200_000
        mock_create_result = MagicMock()

        mock_db.execute = AsyncMock(
            side_effect=[mock_check_result, mock_count_result, mock_create_result]
        )

        with patch("shu.core.vector_store.logger") as mock_logger:
            await store.ensure_index("chunks", 384, db=mock_db)
            mock_logger.warning.assert_called_once()
            assert "200000" in mock_logger.warning.call_args[0][0]


# ---------------------------------------------------------------------------
# Collection config
# ---------------------------------------------------------------------------


class TestCollectionConfig:
    """Test collection configuration."""

    def test_default_collections_exist(self):
        """PgVectorStore should have chunks, synopses, and queries collections."""
        store = PgVectorStore()
        for name in ("chunks", "synopses", "queries"):
            config = store._get_collection(name)
            assert config.table_name
            assert config.embedding_column

    def test_chunks_collection_config(self):
        """Chunks collection should map to document_chunks table."""
        store = PgVectorStore()
        config = store._get_collection("chunks")
        assert config.table_name == "document_chunks"
        assert config.embedding_column == "embedding"
        assert "knowledge_base_id" in config.filterable_columns
        assert "document_id" in config.filterable_columns

    def test_synopses_collection_config(self):
        """Synopses collection should map to documents table."""
        store = PgVectorStore()
        config = store._get_collection("synopses")
        assert config.table_name == "documents"
        assert config.embedding_column == "synopsis_embedding"

    def test_queries_collection_config(self):
        """Queries collection should map to document_queries table."""
        store = PgVectorStore()
        config = store._get_collection("queries")
        assert config.table_name == "document_queries"
        assert config.embedding_column == "query_embedding"

    def test_custom_collections(self):
        """PgVectorStore should accept custom collection configs."""
        custom = {
            "test": CollectionConfig(
                table_name="test_table",
                embedding_column="vec",
                filterable_columns=("tenant_id",),
                distance_metric=DistanceMetric.L2,
            ),
        }
        store = PgVectorStore(collections=custom)
        config = store._get_collection("test")
        assert config.table_name == "test_table"
        assert config.distance_metric == DistanceMetric.L2


# ---------------------------------------------------------------------------
# DI wiring
# ---------------------------------------------------------------------------


class TestDIWiring:
    """Test the module-level DI functions."""

    def setup_method(self):
        """Reset singleton before each test."""
        reset_vector_store()

    def teardown_method(self):
        """Clean up after each test."""
        reset_vector_store()

    @pytest.mark.asyncio
    @patch("shu.core.vector_store.get_settings_instance")
    async def test_get_vector_store_returns_singleton(self, mock_settings):
        """Two calls to get_vector_store should return the same instance."""
        settings = MagicMock()
        settings.vector_index_type = "ivfflat"
        settings.vector_index_lists = 100
        mock_settings.return_value = settings

        store1 = await get_vector_store()
        store2 = await get_vector_store()

        assert store1 is store2

    @pytest.mark.asyncio
    async def test_reset_clears_singleton(self):
        """reset_vector_store should clear the cached instance."""
        import shu.core.vector_store as mod

        mod._vector_store = PgVectorStore()
        assert mod._vector_store is not None

        reset_vector_store()
        assert mod._vector_store is None

    @patch("shu.core.vector_store.get_settings_instance")
    def test_get_vector_store_dependency_returns_instance(self, mock_settings):
        """get_vector_store_dependency should return a VectorStore."""
        settings = MagicMock()
        settings.vector_index_type = "ivfflat"
        settings.vector_index_lists = 100
        mock_settings.return_value = settings

        store = get_vector_store_dependency()
        assert isinstance(store, VectorStore)

    @patch("shu.core.vector_store.get_settings_instance")
    def test_get_vector_store_dependency_returns_cached(self, mock_settings):
        """get_vector_store_dependency should return cached singleton if available."""
        import shu.core.vector_store as mod

        cached = PgVectorStore()
        mod._vector_store = cached

        store = get_vector_store_dependency()
        assert store is cached
        mock_settings.assert_not_called()


# ---------------------------------------------------------------------------
# Supporting types
# ---------------------------------------------------------------------------


class TestSupportingTypes:
    """Test dataclass types."""

    def test_vector_entry_is_frozen(self):
        """VectorEntry should be immutable."""
        entry = VectorEntry(id="test", vector=[0.1, 0.2])
        with pytest.raises(AttributeError):
            entry.id = "changed"  # type: ignore[misc]

    def test_vector_search_result_is_frozen(self):
        """VectorSearchResult should be immutable."""
        result = VectorSearchResult(id="test", score=0.95)
        with pytest.raises(AttributeError):
            result.score = 0.5  # type: ignore[misc]

    def test_distance_metric_values(self):
        """DistanceMetric should have cosine, inner_product, and l2."""
        assert DistanceMetric.COSINE.value == "cosine"
        assert DistanceMetric.INNER_PRODUCT.value == "inner_product"
        assert DistanceMetric.L2.value == "l2"
