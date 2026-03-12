"""Unit tests for shu.core.text."""

import pytest

from shu.core.text import slugify


class TestSlugify:
    """Tests for the slugify function."""

    def test_basic_conversion(self) -> None:
        assert slugify("Morning Briefing") == "morning-briefing"

    def test_strips_non_alphanumeric(self) -> None:
        assert slugify("Inbox Triage (v2)") == "inbox-triage-v2"

    def test_unicode_normalization(self) -> None:
        assert slugify("cafe\u0301") == "cafe"

    def test_collapses_consecutive_hyphens(self) -> None:
        assert slugify("a -- b") == "a-b"

    def test_strips_leading_and_trailing_hyphens(self) -> None:
        assert slugify("--hello--") == "hello"

    def test_short_input_unchanged(self) -> None:
        short = "my-slug"
        assert slugify(short) == short

    def test_default_max_length_is_100(self) -> None:
        long_name = "a" * 200
        result = slugify(long_name)
        assert len(result) <= 100

    def test_truncates_to_default_max_length(self) -> None:
        long_name = "a" * 150
        result = slugify(long_name)
        assert len(result) == 100
        assert result == "a" * 100

    def test_custom_max_length(self) -> None:
        result = slugify("abcdefghij", max_length=5)
        assert result == "abcde"

    def test_truncation_strips_trailing_hyphens(self) -> None:
        result = slugify("hello world stuff", max_length=6)
        assert result == "hello"
        assert not result.endswith("-")

    def test_truncation_mid_word(self) -> None:
        result = slugify("abcdefghij", max_length=7)
        assert result == "abcdefg"

    def test_truncation_at_hyphen_boundary(self) -> None:
        result = slugify("abc-def-ghi", max_length=4)
        assert result == "abc"

    def test_max_length_exact_fit(self) -> None:
        result = slugify("abc", max_length=3)
        assert result == "abc"

    def test_max_length_larger_than_input(self) -> None:
        result = slugify("abc", max_length=200)
        assert result == "abc"

    @pytest.mark.parametrize(
        "name, max_length, expected",
        [
            ("My Knowledge Base", 10, "my-knowled"),
            ("alpha-bravo-charlie", 12, "alpha-bravo"),
            ("test", 100, "test"),
        ],
    )
    def test_parametrized_truncation(
        self, name: str, max_length: int, expected: str
    ) -> None:
        assert slugify(name, max_length=max_length) == expected
