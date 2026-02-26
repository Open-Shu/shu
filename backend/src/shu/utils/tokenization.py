"""Tokenization utilities for LLM context management.

Provides consistent token counting across the codebase for:
- Document profiling input validation
- Context window management
- Input validation before LLM calls

Uses tiktoken with the cl100k_base encoding (GPT-4 tokenizer) as the default.
This provides reasonable estimates for most modern LLMs including:
- OpenAI GPT-3.5/GPT-4
- Anthropic Claude (similar BPE tokenization, ~10-15% variance)
- Other BPE-based models

For production accuracy with specific models, consider using model-specific
tokenizers or the provider's token counting API.
"""

from functools import lru_cache

import structlog

logger = structlog.get_logger(__name__)

# Default encoding for modern LLMs (GPT-4 tokenizer)
DEFAULT_ENCODING = "cl100k_base"

# Cache for tiktoken encoder to avoid repeated initialization
_encoder_cache: dict = {}


@lru_cache(maxsize=4)
def _get_encoder(encoding_name: str = DEFAULT_ENCODING):
    """Get a cached tiktoken encoder.

    Args:
        encoding_name: The tiktoken encoding to use.
            - "cl100k_base": GPT-4, GPT-3.5-turbo, text-embedding-3 (default)
            - "p50k_base": Codex models, text-davinci-002/003
            - "r50k_base": GPT-3 models (davinci, curie, babbage, ada)

    Returns:
        tiktoken.Encoding instance

    """
    try:
        import tiktoken

        return tiktoken.get_encoding(encoding_name)
    except ImportError:
        logger.warning("tiktoken not installed, using word-count heuristic for token estimation")
        return None
    except Exception as e:
        logger.warning(f"Failed to load tiktoken encoding {encoding_name}: {e}")
        return None


def estimate_tokens(text: str, encoding: str | None = None) -> int:
    """Estimate the number of tokens in a text string.

    Uses tiktoken for accurate counting when available, falling back to a
    word-count heuristic (words * 1.3) when tiktoken is not installed.

    Args:
        text: The text to tokenize
        encoding: Optional tiktoken encoding name. Defaults to cl100k_base.

    Returns:
        Estimated token count (integer)

    Example:
        >>> estimate_tokens("Hello, world!")
        4
        >>> estimate_tokens("This is a longer piece of text with more tokens.")
        10

    """
    if not text:
        return 0

    encoding_name = encoding or DEFAULT_ENCODING
    encoder = _get_encoder(encoding_name)

    if encoder is not None:
        try:
            return len(encoder.encode(text))
        except Exception as e:
            logger.warning(f"tiktoken encoding failed, using heuristic: {e}")

    # Fallback: word-count heuristic
    # Average ~1.3 tokens per word for English text
    return int(len(text.split()) * 1.3)


def estimate_tokens_for_chunks(chunks: list[str], encoding: str | None = None) -> int:
    """Estimate total tokens across multiple text chunks.

    Args:
        chunks: List of text strings
        encoding: Optional tiktoken encoding name

    Returns:
        Total estimated token count

    """
    return sum(estimate_tokens(chunk, encoding) for chunk in chunks)


def tokens_to_chars_estimate(tokens: int) -> int:
    """Estimate character count from token count.

    Rough inverse of token estimation. Useful for truncation planning.
    Average ~4 characters per token for English text.

    Args:
        tokens: Number of tokens

    Returns:
        Estimated character count

    """
    return tokens * 4


def chars_to_tokens_estimate(chars: int) -> int:
    """Estimate token count from character count.

    Useful for quick estimates without loading text.
    Average ~4 characters per token for English text.

    Args:
        chars: Number of characters

    Returns:
        Estimated token count

    """
    return chars // 4
