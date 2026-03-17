"""VectorStore protocol and PgVectorStore implementation.

Centralizes all vector storage and search operations behind a protocol
interface. PgVectorStore wraps pgvector for PostgreSQL-backed vector search.

DI wiring:
    - get_vector_store()                — async singleton factory
    - get_vector_store_dependency()     — sync DI helper for FastAPI Depends()
    - initialize_vector_store()         — app startup initializer
    - reset_vector_store()              — test teardown
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings_instance
from .logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Supporting types
# ---------------------------------------------------------------------------


class DistanceMetric(str, Enum):
    """Distance metric for vector similarity search."""

    COSINE = "cosine"
    INNER_PRODUCT = "inner_product"
    L2 = "l2"


@dataclass(frozen=True)
class VectorEntry:
    """A vector embedding to store or update."""

    id: str
    vector: list[float]


@dataclass(frozen=True)
class VectorSearchResult:
    """A single vector search result."""

    id: str
    score: float  # Similarity score (0.0-1.0 for cosine)


@dataclass(frozen=True)
class CollectionConfig:
    """Maps a logical collection name to physical storage."""

    table_name: str
    embedding_column: str
    id_column: str = "id"
    filterable_columns: tuple[str, ...] = ()
    distance_metric: DistanceMetric = DistanceMetric.COSINE


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class VectorStore(Protocol):
    """Protocol for vector storage and search operations.

    Implementations provide vector similarity search, embedding storage,
    deletion, and index management. Each method receives an AsyncSession
    from the caller so operations participate in the caller's transaction.
    """

    async def search(
        self,
        collection: str,
        query_vector: list[float],
        *,
        db: AsyncSession,
        limit: int = 10,
        threshold: float = 0.0,
        filters: dict[str, Any] | None = None,
        extra_where: str | None = None,
        offset: int = 0,
    ) -> list[VectorSearchResult]:
        """Similarity search against a vector collection.

        Args:
            collection: Logical collection name (e.g., "chunks", "synopses", "queries").
            query_vector: The query embedding vector.
            db: Async database session from the caller.
            limit: Maximum number of results.
            threshold: Minimum similarity score (0.0-1.0).
            filters: Equality filters on allowed columns (e.g., {"knowledge_base_id": "..."}).
            extra_where: Raw SQL WHERE clause fragment for collection-specific filtering.
            offset: Number of results to skip (for pagination).

        Returns:
            Results sorted by score descending.

        """
        ...

    async def store_embeddings(
        self,
        collection: str,
        entries: list[VectorEntry],
        *,
        db: AsyncSession,
    ) -> int:
        """Store or update vector embeddings on existing rows.

        Args:
            collection: Logical collection name.
            entries: Vectors to store, keyed by row ID.
            db: Async database session from the caller.

        Returns:
            Number of rows updated.

        """
        ...

    async def delete(
        self,
        collection: str,
        ids: list[str],
        *,
        db: AsyncSession,
    ) -> int:
        """Nullify vector embeddings by row ID.

        Does not delete the row — only clears the embedding column.

        Args:
            collection: Logical collection name.
            ids: Row IDs whose embeddings should be nullified.
            db: Async database session from the caller.

        Returns:
            Number of rows affected.

        """
        ...

    async def ensure_index(
        self,
        collection: str,
        dimension: int,
        *,
        db: AsyncSession,
        index_type: str = "ivfflat",
        lists: int = 100,
    ) -> bool:
        """Ensure an appropriate vector index exists on the collection.

        Args:
            collection: Logical collection name.
            dimension: Vector dimensionality for the index.
            db: Async database session from the caller.
            index_type: Index type ("ivfflat" or "hnsw").
            lists: Number of lists for IVFFlat indexes.

        Returns:
            True if an index was created, False if one already existed.

        """
        ...


# ---------------------------------------------------------------------------
# PgVectorStore
# ---------------------------------------------------------------------------

# Distance operator mapping for pgvector
_DISTANCE_OPERATORS: dict[DistanceMetric, str] = {
    DistanceMetric.COSINE: "<=>",
    DistanceMetric.INNER_PRODUCT: "<#>",
    DistanceMetric.L2: "<->",
}

# pgvector operator class names for index creation
_OPS_CLASSES: dict[DistanceMetric, str] = {
    DistanceMetric.COSINE: "vector_cosine_ops",
    DistanceMetric.INNER_PRODUCT: "vector_ip_ops",
    DistanceMetric.L2: "vector_l2_ops",
}

# Default collection configuration
_DEFAULT_COLLECTIONS: dict[str, CollectionConfig] = {
    "chunks": CollectionConfig(
        table_name="document_chunks",
        embedding_column="embedding",
        filterable_columns=("knowledge_base_id", "document_id"),
    ),
    "synopses": CollectionConfig(
        table_name="documents",
        embedding_column="synopsis_embedding",
        filterable_columns=("knowledge_base_id",),
    ),
    "queries": CollectionConfig(
        table_name="document_queries",
        embedding_column="query_embedding",
        filterable_columns=("knowledge_base_id", "document_id"),
    ),
    "chunk_summaries": CollectionConfig(
        table_name="document_chunks",
        embedding_column="summary_embedding",
        filterable_columns=("knowledge_base_id", "document_id"),
    ),
}


def _index_name(collection: str, dimension: int, index_type: str = "hnsw") -> str:
    """Generate a dimension-scoped index name.

    Pattern: ix_{table}_{column}_{type}_{dim}
    """
    config = _DEFAULT_COLLECTIONS[collection]
    return f"ix_{config.table_name}_{config.embedding_column}_{index_type}_{dimension}"


class PgVectorStore:
    """PostgreSQL + pgvector implementation of VectorStore.

    Holds configuration only — no database connection. Each method
    receives an AsyncSession from the caller so operations participate
    in the caller's transaction.
    """

    def __init__(
        self,
        index_type: str = "hnsw",
        index_lists: int = 100,
        collections: dict[str, CollectionConfig] | None = None,
    ) -> None:
        self._index_type = index_type
        self._index_lists = index_lists
        self._collections = collections or _DEFAULT_COLLECTIONS

    def _get_collection(self, collection: str) -> CollectionConfig:
        """Look up collection config, raising ValueError for unknown names."""
        if collection not in self._collections:
            valid = ", ".join(sorted(self._collections.keys()))
            raise ValueError(f"Unknown collection '{collection}'. Valid collections: {valid}")
        return self._collections[collection]

    # -- search -------------------------------------------------------------

    async def search(
        self,
        collection: str,
        query_vector: list[float],
        *,
        db: AsyncSession,
        limit: int = 10,
        threshold: float = 0.0,
        filters: dict[str, Any] | None = None,
        extra_where: str | None = None,
        offset: int = 0,
    ) -> list[VectorSearchResult]:
        """Similarity search using pgvector distance operators."""
        from pgvector.sqlalchemy import Vector as PgVector

        config = self._get_collection(collection)
        op = _DISTANCE_OPERATORS[config.distance_metric]
        tbl = config.table_name
        emb = config.embedding_column
        id_col = config.id_column

        # Validate filters
        params: dict[str, Any] = {}
        dimension = len(query_vector)
        where_clauses: list[str] = [f"{emb} IS NOT NULL", f"vector_dims({emb}) = :dimension"]

        if filters:
            for col, val in filters.items():
                if col not in config.filterable_columns:
                    valid = ", ".join(config.filterable_columns)
                    raise ValueError(
                        f"Filter column '{col}' not allowed for collection '{collection}'. " f"Allowed: {valid}"
                    )
                param_name = f"f_{col}"
                where_clauses.append(f"{col} = :{param_name}")
                params[param_name] = val

        if extra_where:
            where_clauses.append(f"({extra_where})")

        # Score conversion: cosine distance → similarity
        # pgvector cosine distance: 0 = identical, 2 = opposite
        # Similarity: GREATEST(0, 1 - distance)
        score_expr = f"GREATEST(0, 1 - ({emb} {op} :query_vector))"
        threshold_clause = f"1 - ({emb} {op} :query_vector) >= :threshold"
        order_expr = f"{emb} {op} :query_vector"

        where_clauses.append(threshold_clause)
        where_sql = " AND ".join(where_clauses)

        # Table/column names come from hardcoded CollectionConfig, not user input
        sql = f"""
            SELECT {id_col}, {score_expr} AS score
            FROM {tbl}
            WHERE {where_sql}
            ORDER BY {order_expr}
            LIMIT :limit OFFSET :offset
        """  # noqa: S608  # nosec B608

        from sqlalchemy import bindparam

        query = text(sql).bindparams(bindparam("query_vector", type_=PgVector()))
        params["query_vector"] = query_vector
        params["threshold"] = threshold
        params["limit"] = limit
        params["offset"] = offset
        params["dimension"] = dimension

        result = await db.execute(query, params)
        rows = result.fetchall()

        return [VectorSearchResult(id=str(row[0]), score=float(row[1])) for row in rows]

    # -- store_embeddings ---------------------------------------------------

    async def store_embeddings(
        self,
        collection: str,
        entries: list[VectorEntry],
        *,
        db: AsyncSession,
    ) -> int:
        """Update embedding columns on existing rows via raw SQL."""
        if not entries:
            return 0

        from pgvector.sqlalchemy import Vector as PgVector
        from sqlalchemy import bindparam

        config = self._get_collection(collection)
        tbl = config.table_name
        emb = config.embedding_column
        id_col = config.id_column

        sql = text(
            f"UPDATE {tbl} SET {emb} = :vector WHERE {id_col} = :row_id"  # noqa: S608  # nosec B608
        ).bindparams(bindparam("vector", type_=PgVector()))

        params = [{"vector": entry.vector, "row_id": entry.id} for entry in entries]
        cursor_result = await db.execute(sql, params)
        return cursor_result.rowcount  # type: ignore[union-attr]

    # -- delete -------------------------------------------------------------

    async def delete(
        self,
        collection: str,
        ids: list[str],
        *,
        db: AsyncSession,
    ) -> int:
        """Nullify embedding columns (does not delete rows)."""
        if not ids:
            return 0

        config = self._get_collection(collection)
        tbl = config.table_name
        emb = config.embedding_column
        id_col = config.id_column

        sql = text(f"UPDATE {tbl} SET {emb} = NULL WHERE {id_col} = ANY(:ids)")  # noqa: S608  # nosec B608
        result = await db.execute(sql, {"ids": ids})
        return result.rowcount

    # -- ensure_index -------------------------------------------------------

    async def ensure_index(
        self,
        collection: str,
        dimension: int,
        *,
        db: AsyncSession,
        index_type: str | None = None,
        lists: int | None = None,
    ) -> bool:
        """Ensure a dimension-scoped HNSW vector index exists.

        Creates a partial index scoped to vectors of the given dimension using
        a cast expression and WHERE clause. This allows multiple embedding
        dimensions to coexist in the same dimensionless vector column.

        Uses non-CONCURRENTLY since we're in a session context. For
        production index creation on large tables, use a management
        command with CONCURRENTLY and autocommit.
        """
        config = self._get_collection(collection)
        idx_type = index_type or self._index_type
        idx_lists = lists or self._index_lists
        index_name = _index_name(collection, dimension, idx_type)
        ops_class = _OPS_CLASSES[config.distance_metric]
        emb = config.embedding_column

        # Check if index already exists
        check_sql = text("SELECT 1 FROM pg_indexes WHERE tablename = :table_name AND indexname = :index_name")
        result = await db.execute(check_sql, {"table_name": config.table_name, "index_name": index_name})
        if result.scalar_one_or_none():
            logger.debug(f"Index {index_name} already exists on {config.table_name}")
            return False

        # Warn on large tables
        count_result = await db.execute(text(f"SELECT COUNT(*) FROM {config.table_name}"))  # noqa: S608  # nosec B608
        row_count = count_result.scalar_one()
        if row_count > 100_000:
            logger.warning(f"Creating index on {config.table_name} with {row_count} rows — this may be slow")

        # Build CREATE INDEX statement with dimension-scoped partial index
        if idx_type == "hnsw":
            create_sql = (
                f"CREATE INDEX IF NOT EXISTS {index_name} "
                f"ON {config.table_name} USING hnsw "
                f"(({emb}::vector({dimension})) {ops_class}) "
                f"WHERE vector_dims({emb}) = {dimension}"
            )
        elif idx_type == "ivfflat":
            create_sql = (
                f"CREATE INDEX IF NOT EXISTS {index_name} "
                f"ON {config.table_name} USING ivfflat "
                f"(({emb}::vector({dimension})) {ops_class}) "
                f"WITH (lists = {idx_lists}) "
                f"WHERE vector_dims({emb}) = {dimension}"
            )
        else:
            raise ValueError(f"Unsupported index type: {idx_type}")

        await db.execute(text(create_sql))
        logger.info(f"Created {idx_type} index: {index_name} on {config.table_name} (dim={dimension})")
        return True


# ---------------------------------------------------------------------------
# Index maintenance
# ---------------------------------------------------------------------------


async def drop_orphaned_indexes(db: AsyncSession, current_dimension: int) -> list[str]:
    """Drop HNSW indexes that target a dimension other than *current_dimension*.

    After an embedding model change the old-dimension indexes are useless
    (vector search is blocked on stale KBs) and waste storage / write overhead.
    This function queries ``pg_indexes`` for our naming pattern and drops any
    index whose dimension suffix doesn't match the currently configured model.

    Args:
        db: Database session (DDL auto-commits in PostgreSQL).
        current_dimension: The dimension of the active embedding model.

    Returns:
        List of index names that were dropped.

    """
    result = await db.execute(
        text(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'public' "
            "AND indexname ~ '^ix_(document_chunks|documents|document_queries)_[a-z_]+_(hnsw|ivfflat)_[0-9]+$'"
        )
    )

    dropped: list[str] = []
    for (index_name,) in result.fetchall():
        dim_str = index_name.rsplit("_", 1)[1]
        try:
            dim = int(dim_str)
        except ValueError:
            logger.warning(f"Could not parse dimension from index name: {index_name}")
            continue

        if dim != current_dimension:
            # Identifier is safe (regex constrains to [a-z_0-9]), but quote for defense-in-depth
            await db.execute(text(f'DROP INDEX IF EXISTS "{index_name}"'))
            dropped.append(index_name)
            logger.info(f"Dropped orphaned index {index_name} (dim={dim}, current={current_dimension})")

    return dropped


# ---------------------------------------------------------------------------
# DI wiring
# ---------------------------------------------------------------------------

_vector_store: VectorStore | None = None


async def get_vector_store() -> VectorStore:
    """Get the configured vector store (singleton).

    Creates a PgVectorStore using settings for index type and lists.
    Suitable for use in background tasks, workers, and services.
    For FastAPI endpoints, prefer get_vector_store_dependency().

    Returns:
        The configured VectorStore instance.

    """
    global _vector_store  # noqa: PLW0603

    if _vector_store is not None:
        return _vector_store

    settings = get_settings_instance()

    _vector_store = PgVectorStore(
        index_type=settings.vector_index_type,
        index_lists=settings.vector_index_lists,
    )

    return _vector_store


def get_vector_store_dependency() -> VectorStore:
    """Dependency injection function for VectorStore.

    Use in FastAPI endpoints with Depends(). Returns the cached singleton
    if available, otherwise creates one synchronously.

    Returns:
        A VectorStore instance.

    """
    global _vector_store  # noqa: PLW0603

    if _vector_store is not None:
        return _vector_store

    settings = get_settings_instance()
    _vector_store = PgVectorStore(
        index_type=settings.vector_index_type,
        index_lists=settings.vector_index_lists,
    )
    return _vector_store


async def initialize_vector_store() -> VectorStore:
    """Initialize the vector store during application startup.

    Returns:
        The initialized VectorStore instance.

    """
    return await get_vector_store()


def reset_vector_store() -> None:
    """Reset the vector store singleton (for testing only)."""
    global _vector_store  # noqa: PLW0603
    _vector_store = None
