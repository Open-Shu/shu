"""Portable encoding/decoding of float32 embedding vectors.

Used by both the Document models (serialize_for_export / build_import_record)
and the KB import service to convert between Python float lists and compact
base64-encoded strings suitable for JSONL archives.
"""

import base64

import numpy as np


def encode_embedding(embedding: list[float] | None) -> str | None:
    """Base64-encode a float32 embedding vector.

    Args:
        embedding: List of floats, or None.

    Returns:
        Base64-encoded string, or None if input is None.

    """
    if embedding is None:
        return None
    return base64.b64encode(np.array(embedding, dtype=np.float32).tobytes()).decode("ascii")


def decode_embedding(data: str | None) -> list[float] | None:
    """Decode a base64-encoded float32 embedding vector.

    Args:
        data: Base64-encoded string, or None.

    Returns:
        List of floats, or None if input is None.

    """
    if data is None:
        return None
    return np.frombuffer(base64.b64decode(data), dtype=np.float32).tolist()
