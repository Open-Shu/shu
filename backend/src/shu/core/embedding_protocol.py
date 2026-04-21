"""EmbeddingService protocol definition.

Separated from embedding_service.py to allow importing the protocol
without loading sentence-transformers (~2GB). This enables external
embedding backends to implement the protocol without triggering
local model initialization.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingService(Protocol):
    """Protocol for embedding generation services.

    Implementations must provide async methods for generating embeddings
    from text. Supports both batch and single-query paths.
    """

    @property
    def dimension(self) -> int:
        """Dimensionality of the embedding vectors produced by this service."""
        ...

    @property
    def model_name(self) -> str:
        """Name of the underlying embedding model."""
        ...

    async def embed_texts(self, texts: list[str], *, user_id: str | None = None) -> list[list[float]]:
        """Generate embeddings for a batch of texts.

        Args:
            texts: List of text strings to embed. Empty list returns [].
            user_id: Optional user attribution for llm_usage rows written by
                billable external providers. Local services ignore it.

        Returns:
            List of embedding vectors, one per input text.

        """
        ...

    async def embed_query(self, text: str, *, user_id: str | None = None) -> list[float]:
        """Generate an embedding for a single query text.

        Args:
            text: The query text to embed.
            user_id: Optional user attribution for llm_usage rows written by
                billable external providers. Local services ignore it.

        Returns:
            A single embedding vector.

        """
        ...

    async def embed_queries(self, texts: list[str], *, user_id: str | None = None) -> list[list[float]]:
        """Generate embeddings for a batch of query texts.

        Like embed_texts(), but applies the query prompt for asymmetric
        models (e.g., Snowflake arctic-embed). Use this when embedding
        synthesized queries or any text that will be matched against
        user search queries.

        Args:
            texts: List of query strings to embed. Empty list returns [].
            user_id: Optional user attribution for llm_usage rows written by
                billable external providers. Local services ignore it.

        Returns:
            List of embedding vectors, one per input text.

        """
        ...
