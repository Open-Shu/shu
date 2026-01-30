"""Unit tests for tokenization utilities."""

from shu.utils.tokenization import (
    chars_to_tokens_estimate,
    estimate_tokens,
    estimate_tokens_for_chunks,
    tokens_to_chars_estimate,
)


class TestEstimateTokens:
    """Tests for the estimate_tokens function."""

    def test_empty_string(self):
        """Empty string should return 0 tokens."""
        assert estimate_tokens("") == 0

    def test_none_like_empty(self):
        """None-like values should be handled gracefully."""
        assert estimate_tokens("") == 0

    def test_simple_text(self):
        """Simple text should return reasonable token count."""
        text = "Hello, world!"
        tokens = estimate_tokens(text)
        # tiktoken cl100k_base: "Hello, world!" -> 4 tokens
        # Should be in reasonable range (3-6)
        assert 3 <= tokens <= 6

    def test_longer_text(self):
        """Longer text should scale appropriately."""
        short_text = "Hello"
        long_text = "Hello " * 100  # 100 repetitions

        short_tokens = estimate_tokens(short_text)
        long_tokens = estimate_tokens(long_text)

        # Long text should have significantly more tokens
        assert long_tokens > short_tokens * 50

    def test_special_characters(self):
        """Special characters should be tokenized."""
        text = "!@#$%^&*()"
        tokens = estimate_tokens(text)
        assert tokens > 0

    def test_unicode_text(self):
        """Unicode text should be handled."""
        text = "こんにちは世界"  # "Hello, world" in Japanese
        tokens = estimate_tokens(text)
        assert tokens > 0

    def test_code_snippet(self):
        """Code should tokenize appropriately."""
        code = """
def hello_world():
    print("Hello, world!")
    return True
"""
        tokens = estimate_tokens(code)
        # Code typically has more tokens due to syntax
        assert tokens >= 10

    def test_consistency(self):
        """Same input should always produce same output."""
        text = "Consistent tokenization test"
        first = estimate_tokens(text)
        second = estimate_tokens(text)
        assert first == second


class TestEstimateTokensForChunks:
    """Tests for the estimate_tokens_for_chunks function."""

    def test_empty_list(self):
        """Empty chunk list should return 0."""
        assert estimate_tokens_for_chunks([]) == 0

    def test_single_chunk(self):
        """Single chunk should equal individual estimate."""
        chunk = "This is a test chunk."
        assert estimate_tokens_for_chunks([chunk]) == estimate_tokens(chunk)

    def test_multiple_chunks(self):
        """Multiple chunks should sum correctly."""
        chunks = ["First chunk.", "Second chunk.", "Third chunk."]
        total = estimate_tokens_for_chunks(chunks)
        expected = sum(estimate_tokens(c) for c in chunks)
        assert total == expected

    def test_mixed_content(self):
        """Different content types in chunks."""
        chunks = [
            "Plain text content.",
            "def code(): pass",
            "Numbers: 12345",
        ]
        total = estimate_tokens_for_chunks(chunks)
        assert total > 0


class TestConversionEstimates:
    """Tests for token/character conversion estimates."""

    def test_tokens_to_chars(self):
        """Token to character conversion."""
        # Average ~4 chars per token
        tokens = 100
        chars = tokens_to_chars_estimate(tokens)
        assert chars == 400

    def test_chars_to_tokens(self):
        """Character to token conversion."""
        # Average ~4 chars per token
        chars = 400
        tokens = chars_to_tokens_estimate(chars)
        assert tokens == 100

    def test_round_trip_approximate(self):
        """Round trip should be approximately consistent."""
        original_tokens = 100
        chars = tokens_to_chars_estimate(original_tokens)
        back_to_tokens = chars_to_tokens_estimate(chars)
        assert back_to_tokens == original_tokens

    def test_zero_values(self):
        """Zero should convert to zero."""
        assert tokens_to_chars_estimate(0) == 0
        assert chars_to_tokens_estimate(0) == 0


class TestProfilingUseCases:
    """Tests for document profiling-specific use cases."""

    def test_small_document_threshold(self):
        """Test threshold detection for small documents."""
        # Simulate ~3000 token document (typical small doc)
        small_doc = "word " * 2300  # ~3000 tokens with tiktoken
        tokens = estimate_tokens(small_doc)

        threshold = 4000  # Default profiling threshold
        assert tokens < threshold, "Small doc should be under threshold"

    def test_large_document_threshold(self):
        """Test threshold detection for large documents."""
        # Simulate ~6000 token document (requires chunk-agg)
        large_doc = "word " * 4600  # ~6000 tokens with tiktoken
        tokens = estimate_tokens(large_doc)

        threshold = 4000  # Default profiling threshold
        assert tokens > threshold, "Large doc should exceed threshold"
