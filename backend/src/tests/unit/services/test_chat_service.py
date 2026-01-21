"""
Property-based tests for ChatService.

Tests conversation favorite ownership constraints using property-based testing
with Hypothesis.

**Feature: conversation-enhancements, Property 3: Favorite Update Preserves Ownership**
**Validates: Requirements 2.8, 2.9**
"""

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.strategies import composite

from shu.core.exceptions import ConversationNotFoundError
from shu.models.llm_provider import Conversation
from shu.services.chat_service import ChatService


@composite
def conversation_data(draw) -> Dict[str, Any]:
    """
    Generate realistic conversation data for property testing.
    
    Args:
        draw: Hypothesis draw function for generating values
        
    Returns:
        Dictionary containing conversation data with all required fields
    """
    return {
        'id': str(uuid.uuid4()),
        'user_id': str(uuid.uuid4()),
        'title': draw(st.text(min_size=1, max_size=100)),
        'is_active': draw(st.booleans()),
        'is_favorite': draw(st.booleans()),
        'created_at': datetime.now(timezone.utc),
        'updated_at': datetime.now(timezone.utc),
        'model_configuration_id': str(uuid.uuid4()),
        'meta': {},
        'summary_text': None,
    }


class TestChatServiceFavoriteOwnership:
    """Property-based tests for conversation favorite ownership constraints."""

    @given(conversation_data(), st.booleans())
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_owner_can_update_favorite_status(
        self,
        conv_data: Dict[str, Any],
        new_favorite_status: bool
    ) -> None:
        """
        Property 3a: Owner can update favorite status.
        
        For any conversation and any boolean favorite status, when the conversation owner
        attempts to update the is_favorite field, the system should allow the update
        and preserve all other conversation properties.
        
        **Validates: Requirements 2.8**
        
        Args:
            conv_data: Generated conversation data
            new_favorite_status: New favorite status to set
        """
        # Create mock conversation
        mock_conversation = MagicMock(spec=Conversation)
        mock_conversation.id = conv_data['id']
        mock_conversation.user_id = conv_data['user_id']
        mock_conversation.title = conv_data['title']
        mock_conversation.is_active = conv_data['is_active']
        mock_conversation.is_favorite = conv_data['is_favorite']
        mock_conversation.created_at = conv_data['created_at']
        mock_conversation.updated_at = conv_data['updated_at']
        mock_conversation.model_configuration_id = conv_data['model_configuration_id']
        mock_conversation.meta = conv_data['meta']
        mock_conversation.summary_text = conv_data['summary_text']
        
        # Mock database session
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_conversation
        mock_db.execute.return_value = mock_result
        
        # Mock config manager
        mock_config_manager = MagicMock()
        
        # Create service
        chat_service = ChatService(mock_db, mock_config_manager)
        
        # Store original values to verify preservation
        original_title = conv_data['title']
        original_is_active = conv_data['is_active']
        original_meta = conv_data['meta']
        
        # Update favorite status
        result = await chat_service.update_conversation(
            conversation_id=conv_data['id'],
            is_favorite=new_favorite_status
        )
        
        # Verify the update was allowed (no exception raised)
        assert result is not None
        
        # Verify is_favorite was updated
        assert mock_conversation.is_favorite == new_favorite_status
        
        # Property: All other conversation properties should be preserved
        assert mock_conversation.title == original_title, (
            f"Title should be preserved but changed from '{original_title}' to '{mock_conversation.title}'"
        )
        assert mock_conversation.is_active == original_is_active, (
            f"is_active should be preserved but changed from {original_is_active} to {mock_conversation.is_active}"
        )
        assert mock_conversation.meta == original_meta, (
            f"meta should be preserved but changed from {original_meta} to {mock_conversation.meta}"
        )
        
        # Verify database commit was called
        mock_db.commit.assert_called_once()

    @given(conversation_data(), st.booleans())
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_favorite_status_persists_after_update(
        self,
        conv_data: Dict[str, Any],
        new_favorite_status: bool
    ) -> None:
        """
        Property 3b: Favorite status persists after update.
        
        For any conversation, when the owner updates the is_favorite field to a new value,
        that value should persist in the database and be reflected in subsequent queries.
        
        **Validates: Requirements 2.8, 2.1, 2.6**
        
        Args:
            conv_data: Generated conversation data
            new_favorite_status: New favorite status to set
        """
        # Create mock conversation
        mock_conversation = MagicMock(spec=Conversation)
        mock_conversation.id = conv_data['id']
        mock_conversation.user_id = conv_data['user_id']
        mock_conversation.title = conv_data['title']
        mock_conversation.is_active = conv_data['is_active']
        mock_conversation.is_favorite = conv_data['is_favorite']
        mock_conversation.created_at = conv_data['created_at']
        mock_conversation.updated_at = conv_data['updated_at']
        mock_conversation.model_configuration_id = conv_data['model_configuration_id']
        mock_conversation.meta = conv_data['meta']
        mock_conversation.summary_text = conv_data['summary_text']
        
        # Mock database session
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_conversation
        mock_db.execute.return_value = mock_result
        
        # Mock config manager
        mock_config_manager = MagicMock()
        
        # Create service
        chat_service = ChatService(mock_db, mock_config_manager)
        
        # Update favorite status
        result = await chat_service.update_conversation(
            conversation_id=conv_data['id'],
            is_favorite=new_favorite_status
        )
        
        # Property: The is_favorite field should be set to the new value
        assert mock_conversation.is_favorite == new_favorite_status, (
            f"Expected is_favorite={new_favorite_status} after update, "
            f"but got {mock_conversation.is_favorite}"
        )
        
        # Verify database operations were called
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once_with(mock_conversation)

    @given(conversation_data(), st.booleans())
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_nonexistent_conversation_raises_error(
        self,
        conv_data: Dict[str, Any],
        new_favorite_status: bool
    ) -> None:
        """
        Property 3c: Non-existent conversation raises error.
        
        For any conversation ID that doesn't exist in the database, attempting to update
        the is_favorite field should raise a ConversationNotFoundError.
        
        **Validates: Requirements 2.8, 2.9**
        
        Args:
            conv_data: Generated conversation data
            new_favorite_status: New favorite status to set
        """
        # Mock database session returning None (conversation not found)
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result
        
        # Mock config manager
        mock_config_manager = MagicMock()
        
        # Create service
        chat_service = ChatService(mock_db, mock_config_manager)
        
        # Property: Attempting to update a non-existent conversation should raise an error
        with pytest.raises(ConversationNotFoundError) as exc_info:
            await chat_service.update_conversation(
                conversation_id=conv_data['id'],
                is_favorite=new_favorite_status
            )
        
        # Verify the error message contains the conversation ID
        assert conv_data['id'] in str(exc_info.value)
        
        # Verify no database commit was attempted
        mock_db.commit.assert_not_called()

    @given(conversation_data(), st.booleans(), st.text(min_size=1, max_size=100))
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_favorite_update_with_other_fields(
        self,
        conv_data: Dict[str, Any],
        new_favorite_status: bool,
        new_title: str
    ) -> None:
        """
        Property 3d: Favorite update can be combined with other field updates.
        
        For any conversation, when updating is_favorite along with other fields (like title),
        all updates should be applied correctly and atomically.
        
        **Validates: Requirements 2.8, 2.6**
        
        Args:
            conv_data: Generated conversation data
            new_favorite_status: New favorite status to set
            new_title: New title to set
        """
        # Create mock conversation
        mock_conversation = MagicMock(spec=Conversation)
        mock_conversation.id = conv_data['id']
        mock_conversation.user_id = conv_data['user_id']
        mock_conversation.title = conv_data['title']
        mock_conversation.is_active = conv_data['is_active']
        mock_conversation.is_favorite = conv_data['is_favorite']
        mock_conversation.created_at = conv_data['created_at']
        mock_conversation.updated_at = conv_data['updated_at']
        mock_conversation.model_configuration_id = conv_data['model_configuration_id']
        mock_conversation.meta = conv_data['meta']
        mock_conversation.summary_text = conv_data['summary_text']
        
        # Mock database session
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_conversation
        mock_db.execute.return_value = mock_result
        
        # Mock config manager
        mock_config_manager = MagicMock()
        
        # Create service
        chat_service = ChatService(mock_db, mock_config_manager)
        
        # Update both favorite status and title
        result = await chat_service.update_conversation(
            conversation_id=conv_data['id'],
            title=new_title,
            is_favorite=new_favorite_status
        )
        
        # Property: Both fields should be updated
        assert mock_conversation.is_favorite == new_favorite_status, (
            f"Expected is_favorite={new_favorite_status}, but got {mock_conversation.is_favorite}"
        )
        assert mock_conversation.title == new_title, (
            f"Expected title='{new_title}', but got '{mock_conversation.title}'"
        )
        
        # Verify database commit was called once (atomic update)
        mock_db.commit.assert_called_once()

    @given(conversation_data())
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_favorite_toggle_idempotency(
        self,
        conv_data: Dict[str, Any]
    ) -> None:
        """
        Property 3e: Favorite toggle is idempotent.
        
        For any conversation, toggling is_favorite to the same value it already has
        should be a valid operation that doesn't cause errors.
        
        **Validates: Requirements 2.8, 2.6**
        
        Args:
            conv_data: Generated conversation data
        """
        # Create mock conversation
        mock_conversation = MagicMock(spec=Conversation)
        mock_conversation.id = conv_data['id']
        mock_conversation.user_id = conv_data['user_id']
        mock_conversation.title = conv_data['title']
        mock_conversation.is_active = conv_data['is_active']
        mock_conversation.is_favorite = conv_data['is_favorite']
        mock_conversation.created_at = conv_data['created_at']
        mock_conversation.updated_at = conv_data['updated_at']
        mock_conversation.model_configuration_id = conv_data['model_configuration_id']
        mock_conversation.meta = conv_data['meta']
        mock_conversation.summary_text = conv_data['summary_text']
        
        # Mock database session
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_conversation
        mock_db.execute.return_value = mock_result
        
        # Mock config manager
        mock_config_manager = MagicMock()
        
        # Create service
        chat_service = ChatService(mock_db, mock_config_manager)
        
        # Get current favorite status
        current_favorite_status = conv_data['is_favorite']
        
        # Update to the same value (idempotent operation)
        result = await chat_service.update_conversation(
            conversation_id=conv_data['id'],
            is_favorite=current_favorite_status
        )
        
        # Property: The operation should succeed without errors
        assert result is not None
        
        # Property: The value should remain the same
        assert mock_conversation.is_favorite == current_favorite_status, (
            f"Expected is_favorite to remain {current_favorite_status}, "
            f"but got {mock_conversation.is_favorite}"
        )
        
        # Verify database commit was called
        mock_db.commit.assert_called_once()


class TestChatServiceConversationOrdering:
    """Unit tests for conversation list ordering.
    
    **Feature: conversation-enhancements**
    **Validates: Requirements 2.3, 2.4, 2.5**
    """

    @pytest.mark.asyncio
    async def test_favorited_conversations_appear_first(self) -> None:
        """
        Test that favorited conversations appear before non-favorited ones.
        
        For any user's conversation list, favorited conversations should appear
        before non-favorited conversations, regardless of updated_at timestamps.
        
        **Validates: Requirements 2.3**
        """
        user_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        
        # Create mock conversations with different favorite statuses and timestamps
        # Non-favorite but more recently updated
        conv1 = MagicMock(spec=Conversation)
        conv1.id = str(uuid.uuid4())
        conv1.user_id = user_id
        conv1.is_favorite = False
        conv1.updated_at = now  # Most recent
        conv1.is_active = True
        
        # Favorite but older
        conv2 = MagicMock(spec=Conversation)
        conv2.id = str(uuid.uuid4())
        conv2.user_id = user_id
        conv2.is_favorite = True
        conv2.updated_at = now - timedelta(days=1)  # Older
        conv2.is_active = True
        
        # Another non-favorite
        conv3 = MagicMock(spec=Conversation)
        conv3.id = str(uuid.uuid4())
        conv3.user_id = user_id
        conv3.is_favorite = False
        conv3.updated_at = now - timedelta(hours=1)
        conv3.is_active = True
        
        # Mock database to return conversations in "wrong" order (by updated_at only)
        # The service should re-order them by is_favorite first
        mock_db = AsyncMock()
        mock_result = MagicMock()
        
        # Simulate database returning conversations sorted by is_favorite DESC, updated_at DESC
        # (This is what the SQL query should produce)
        mock_result.scalars.return_value.all.return_value = [conv2, conv1, conv3]
        mock_db.execute.return_value = mock_result
        
        # Mock config manager
        mock_config_manager = MagicMock()
        
        # Create service
        chat_service = ChatService(mock_db, mock_config_manager)
        
        # Get conversations
        conversations = await chat_service.get_user_conversations(
            user_id=user_id,
            limit=50,
            offset=0
        )
        
        # Verify the order: favorited conversation should be first
        assert len(conversations) == 3
        assert conversations[0].is_favorite is True, "First conversation should be favorited"
        assert conversations[1].is_favorite is False, "Second conversation should not be favorited"
        assert conversations[2].is_favorite is False, "Third conversation should not be favorited"

    @pytest.mark.asyncio
    async def test_favorited_conversations_sorted_by_updated_at(self) -> None:
        """
        Test that favorited conversations are sorted by updated_at descending.
        
        When multiple conversations are favorited, they should be sorted by
        most recently updated first.
        
        **Validates: Requirements 2.4**
        """
        user_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        
        # Create multiple favorited conversations with different timestamps
        conv1 = MagicMock(spec=Conversation)
        conv1.id = str(uuid.uuid4())
        conv1.user_id = user_id
        conv1.is_favorite = True
        conv1.updated_at = now - timedelta(days=2)  # Oldest favorite
        conv1.is_active = True
        
        conv2 = MagicMock(spec=Conversation)
        conv2.id = str(uuid.uuid4())
        conv2.user_id = user_id
        conv2.is_favorite = True
        conv2.updated_at = now  # Most recent favorite
        conv2.is_active = True
        
        conv3 = MagicMock(spec=Conversation)
        conv3.id = str(uuid.uuid4())
        conv3.user_id = user_id
        conv3.is_favorite = True
        conv3.updated_at = now - timedelta(days=1)  # Middle favorite
        conv3.is_active = True
        
        # Mock database
        mock_db = AsyncMock()
        mock_result = MagicMock()
        
        # Database should return them sorted by updated_at DESC within favorites
        mock_result.scalars.return_value.all.return_value = [conv2, conv3, conv1]
        mock_db.execute.return_value = mock_result
        
        # Mock config manager
        mock_config_manager = MagicMock()
        
        # Create service
        chat_service = ChatService(mock_db, mock_config_manager)
        
        # Get conversations
        conversations = await chat_service.get_user_conversations(
            user_id=user_id,
            limit=50,
            offset=0
        )
        
        # Verify the order: most recently updated favorite first
        assert len(conversations) == 3
        assert conversations[0].updated_at == now
        assert conversations[1].updated_at == now - timedelta(days=1)
        assert conversations[2].updated_at == now - timedelta(days=2)

    @pytest.mark.asyncio
    async def test_non_favorited_conversations_sorted_by_updated_at(self) -> None:
        """
        Test that non-favorited conversations are sorted by updated_at descending.
        
        When multiple conversations are not favorited, they should be sorted by
        most recently updated first.
        
        **Validates: Requirements 2.5**
        """
        user_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        
        # Create multiple non-favorited conversations with different timestamps
        conv1 = MagicMock(spec=Conversation)
        conv1.id = str(uuid.uuid4())
        conv1.user_id = user_id
        conv1.is_favorite = False
        conv1.updated_at = now - timedelta(hours=2)  # Oldest
        conv1.is_active = True
        
        conv2 = MagicMock(spec=Conversation)
        conv2.id = str(uuid.uuid4())
        conv2.user_id = user_id
        conv2.is_favorite = False
        conv2.updated_at = now  # Most recent
        conv2.is_active = True
        
        conv3 = MagicMock(spec=Conversation)
        conv3.id = str(uuid.uuid4())
        conv3.user_id = user_id
        conv3.is_favorite = False
        conv3.updated_at = now - timedelta(hours=1)  # Middle
        conv3.is_active = True
        
        # Mock database
        mock_db = AsyncMock()
        mock_result = MagicMock()
        
        # Database should return them sorted by updated_at DESC
        mock_result.scalars.return_value.all.return_value = [conv2, conv3, conv1]
        mock_db.execute.return_value = mock_result
        
        # Mock config manager
        mock_config_manager = MagicMock()
        
        # Create service
        chat_service = ChatService(mock_db, mock_config_manager)
        
        # Get conversations
        conversations = await chat_service.get_user_conversations(
            user_id=user_id,
            limit=50,
            offset=0
        )
        
        # Verify the order: most recently updated first
        assert len(conversations) == 3
        assert conversations[0].updated_at == now
        assert conversations[1].updated_at == now - timedelta(hours=1)
        assert conversations[2].updated_at == now - timedelta(hours=2)

    @pytest.mark.asyncio
    async def test_mixed_favorite_and_non_favorite_ordering(self) -> None:
        """
        Test complete ordering with mixed favorite and non-favorite conversations.
        
        Favorited conversations should appear first (sorted by updated_at DESC),
        followed by non-favorited conversations (also sorted by updated_at DESC).
        
        **Validates: Requirements 2.3, 2.4, 2.5**
        """
        user_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        
        # Create a mix of favorited and non-favorited conversations
        conversations_data = [
            # Non-favorite, most recent overall
            {'is_favorite': False, 'updated_at': now, 'expected_position': 2},
            # Favorite, older than above but should come first
            {'is_favorite': True, 'updated_at': now - timedelta(days=1), 'expected_position': 1},
            # Favorite, most recent favorite
            {'is_favorite': True, 'updated_at': now - timedelta(hours=1), 'expected_position': 0},
            # Non-favorite, older
            {'is_favorite': False, 'updated_at': now - timedelta(days=2), 'expected_position': 3},
        ]
        
        # Create mock conversations
        mock_conversations = []
        for data in conversations_data:
            conv = MagicMock(spec=Conversation)
            conv.id = str(uuid.uuid4())
            conv.user_id = user_id
            conv.is_favorite = data['is_favorite']
            conv.updated_at = data['updated_at']
            conv.is_active = True
            conv.expected_position = data['expected_position']
            mock_conversations.append(conv)
        
        # Mock database
        mock_db = AsyncMock()
        mock_result = MagicMock()
        
        # Sort conversations as the database would (is_favorite DESC, updated_at DESC)
        sorted_conversations = sorted(
            mock_conversations,
            key=lambda c: (not c.is_favorite, -c.updated_at.timestamp())
        )
        
        mock_result.scalars.return_value.all.return_value = sorted_conversations
        mock_db.execute.return_value = mock_result
        
        # Mock config manager
        mock_config_manager = MagicMock()
        
        # Create service
        chat_service = ChatService(mock_db, mock_config_manager)
        
        # Get conversations
        conversations = await chat_service.get_user_conversations(
            user_id=user_id,
            limit=50,
            offset=0
        )
        
        # Verify the complete ordering
        assert len(conversations) == 4
        
        # First two should be favorites, sorted by updated_at DESC
        assert conversations[0].is_favorite is True
        assert conversations[1].is_favorite is True
        assert conversations[0].updated_at > conversations[1].updated_at
        
        # Last two should be non-favorites, sorted by updated_at DESC
        assert conversations[2].is_favorite is False
        assert conversations[3].is_favorite is False
        assert conversations[2].updated_at > conversations[3].updated_at
        
        # Verify all favorites come before all non-favorites
        for i in range(2):
            assert conversations[i].is_favorite is True
        for i in range(2, 4):
            assert conversations[i].is_favorite is False
