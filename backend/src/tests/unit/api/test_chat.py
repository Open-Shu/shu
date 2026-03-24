"""
Unit tests for chat API schemas.

Tests cover:
- SendMessageRequest accepts knowledge_base_ids as valid list, empty list, or null
- SendMessageRequest rejects the old singular knowledge_base_id field (extra="forbid")
"""

import pytest
from pydantic import ValidationError

from shu.api.chat import SendMessageRequest


class TestSendMessageRequestKBIds:
    """Validate knowledge_base_ids field on SendMessageRequest."""

    def test_valid_list(self):
        """A list of KB IDs is accepted."""
        req = SendMessageRequest(message="hi", knowledge_base_ids=["kb-1", "kb-2"])
        assert req.knowledge_base_ids == ["kb-1", "kb-2"]

    def test_empty_list(self):
        """An empty list is accepted and stored as-is."""
        req = SendMessageRequest(message="hi", knowledge_base_ids=[])
        assert req.knowledge_base_ids == []

    def test_null(self):
        """Omitting the field defaults to None."""
        req = SendMessageRequest(message="hi")
        assert req.knowledge_base_ids is None

    def test_explicit_none(self):
        """Explicitly passing None is accepted."""
        req = SendMessageRequest(message="hi", knowledge_base_ids=None)
        assert req.knowledge_base_ids is None

    def test_singular_field_rejected(self):
        """The old knowledge_base_id (singular) is rejected by extra='forbid'."""
        with pytest.raises(ValidationError, match="knowledge_base_id"):
            SendMessageRequest(message="hi", knowledge_base_id="kb-1")
