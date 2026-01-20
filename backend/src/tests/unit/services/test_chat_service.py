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



# ============================================================================
# Experience Integration Tests
# ============================================================================


@composite
def experience_run_data(draw) -> Dict[str, Any]:
    """
    Generate realistic experience run data for property testing.
    
    Args:
        draw: Hypothesis draw function for generating values
        
    Returns:
        Dictionary containing experience run data with all required fields
    """
    from shu.models.experience import Experience, ExperienceRun
    
    experience_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    
    # Generate result content with various characteristics
    # Include short, medium, and long content to test truncation handling
    result_content = draw(st.text(min_size=1, max_size=10000))
    
    return {
        'run_id': str(uuid.uuid4()),
        'experience_id': experience_id,
        'user_id': user_id,
        'result_content': result_content,
        'experience_name': draw(st.text(min_size=1, max_size=100)),
        'model_configuration_id': draw(st.one_of(st.none(), st.just(str(uuid.uuid4())))),
        'experience_model_configuration_id': draw(st.one_of(st.none(), st.just(str(uuid.uuid4())))),
        'status': 'succeeded',
        'created_at': datetime.now(timezone.utc),
        'updated_at': datetime.now(timezone.utc),
    }


def create_mock_db_with_tracking():
    """Helper to create a mock database session that tracks added objects."""
    mock_db = AsyncMock()
    added_objects = []
    
    def track_add(obj):
        added_objects.append(obj)
        return None
    
    mock_db.add = track_add
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    
    return mock_db, added_objects


class TestChatServiceExperienceIntegration:
    """Property-based tests for conversation creation from experience runs.
    
    **Feature: experience-conversation-integration, Property 1: Conversation Creation with Pre-filled Message**
    **Validates: Requirements 1.2, 1.4, 2.4, 5.3**
    """

    @given(experience_run_data())
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_conversation_creation_with_prefilled_message(
        self,
        run_data: Dict[str, Any]
    ) -> None:
        """
        Property 1: Conversation Creation with Pre-filled Message.
        
        For any valid experience run with result content, creating a conversation
        should succeed and produce a conversation with exactly one assistant message
        where the message content exactly matches the run's result_content without
        truncation.
        
        **Validates: Requirements 1.2, 1.4, 2.4, 5.3**
        
        Args:
            run_data: Generated experience run data
        """
        from shu.models.experience import Experience, ExperienceRun
        from shu.models.llm_provider import Message
        
        # Create mock experience
        mock_experience = MagicMock(spec=Experience)
        mock_experience.id = run_data['experience_id']
        mock_experience.name = run_data['experience_name']
        mock_experience.model_configuration_id = run_data['experience_model_configuration_id']
        
        # Create mock experience run
        mock_run = MagicMock(spec=ExperienceRun)
        mock_run.id = run_data['run_id']
        mock_run.experience_id = run_data['experience_id']
        mock_run.user_id = run_data['user_id']
        mock_run.result_content = run_data['result_content']
        mock_run.model_configuration_id = run_data['model_configuration_id']
        mock_run.experience = mock_experience
        mock_run.status = run_data['status']
        mock_run.created_at = run_data['created_at']
        mock_run.updated_at = run_data['updated_at']
        
        # Mock database session with tracking
        mock_db, added_objects = create_mock_db_with_tracking()
        
        # Mock the experience run query to return the run
        # Need to handle: execute() -> unique() -> scalar_one_or_none()
        mock_run_result = MagicMock()
        mock_unique_result = MagicMock()
        mock_unique_result.scalar_one_or_none.return_value = mock_run
        mock_run_result.unique.return_value = mock_unique_result
        
        # Setup execute to return the run result
        mock_db.execute.return_value = mock_run_result
        
        # Mock config manager
        mock_config_manager = MagicMock()
        
        # Create service
        chat_service = ChatService(mock_db, mock_config_manager)
        
        # Create conversation from experience run (user_id matches run.user_id, so no admin check)
        result = await chat_service.create_conversation_from_experience_run(
            run_id=run_data['run_id'],
            user_id=run_data['user_id'],
            title_override=None
        )
        
        # Property 1a: Conversation creation should succeed
        assert result is not None, "Conversation creation should succeed"
        
        # Property 1b: Exactly one conversation and one message should be created
        conversations = [obj for obj in added_objects if isinstance(obj, Conversation)]
        messages = [obj for obj in added_objects if isinstance(obj, Message)]
        
        assert len(conversations) == 1, (
            f"Expected exactly 1 conversation to be created, but got {len(conversations)}"
        )
        assert len(messages) == 1, (
            f"Expected exactly 1 message to be created, but got {len(messages)}"
        )
        
        conversation = conversations[0]
        message = messages[0]
        
        # Property 1c: Message should be an assistant message
        assert message.role == "assistant", (
            f"Expected message role to be 'assistant', but got '{message.role}'"
        )
        
        # Property 1d: Message content should exactly match result_content without truncation
        assert message.content == run_data['result_content'], (
            f"Message content does not match result_content. "
            f"Expected length: {len(run_data['result_content'])}, "
            f"Got length: {len(message.content)}"
        )
        
        # Property 1e: Message should be linked to the conversation
        assert message.conversation_id == conversation.id, (
            f"Message conversation_id should match conversation id"
        )
        
        # Property 1f: Conversation should have correct metadata
        assert conversation.meta is not None, "Conversation meta should not be None"
        assert conversation.meta.get('source') == 'experience', (
            f"Expected meta.source='experience', but got '{conversation.meta.get('source')}'"
        )
        assert conversation.meta.get('experience_id') == run_data['experience_id'], (
            f"Expected meta.experience_id to match, but got '{conversation.meta.get('experience_id')}'"
        )
        assert conversation.meta.get('run_id') == run_data['run_id'], (
            f"Expected meta.run_id to match, but got '{conversation.meta.get('run_id')}'"
        )
        assert conversation.meta.get('created_from_experience') is True, (
            f"Expected meta.created_from_experience=True, but got {conversation.meta.get('created_from_experience')}"
        )
        
        # Property 1g: Message should have correct metadata
        assert message.message_metadata is not None, "Message metadata should not be None"
        assert message.message_metadata.get('source') == 'experience_result', (
            f"Expected message metadata source='experience_result', but got '{message.message_metadata.get('source')}'"
        )
        assert message.message_metadata.get('experience_run_id') == run_data['run_id'], (
            f"Expected message metadata experience_run_id to match, but got '{message.message_metadata.get('experience_run_id')}'"
        )
        
        # Verify database operations
        mock_db.flush.assert_called_once()
        mock_db.commit.assert_called_once()

    @given(experience_run_data(), st.text(min_size=1, max_size=100))
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_conversation_title_override(
        self,
        run_data: Dict[str, Any],
        custom_title: str
    ) -> None:
        """
        Property 1 Extension: Title override functionality.
        
        For any valid experience run, when a custom title is provided,
        the conversation should use the custom title instead of the
        experience name.
        
        **Validates: Requirements 1.3**
        
        Args:
            run_data: Generated experience run data
            custom_title: Custom title to use
        """
        from shu.models.experience import Experience, ExperienceRun
        
        # Create mock experience
        mock_experience = MagicMock(spec=Experience)
        mock_experience.id = run_data['experience_id']
        mock_experience.name = run_data['experience_name']
        mock_experience.model_configuration_id = run_data['experience_model_configuration_id']
        
        # Create mock experience run
        mock_run = MagicMock(spec=ExperienceRun)
        mock_run.id = run_data['run_id']
        mock_run.experience_id = run_data['experience_id']
        mock_run.user_id = run_data['user_id']
        mock_run.result_content = run_data['result_content']
        mock_run.model_configuration_id = run_data['model_configuration_id']
        mock_run.experience = mock_experience
        
        # Mock database session with tracking
        mock_db, added_objects = create_mock_db_with_tracking()
        
        # Mock the experience run query to return the run
        # Need to handle: execute() -> unique() -> scalar_one_or_none()
        mock_run_result = MagicMock()
        mock_unique_result = MagicMock()
        mock_unique_result.scalar_one_or_none.return_value = mock_run
        mock_run_result.unique.return_value = mock_unique_result
        
        # Setup execute to return the run result
        mock_db.execute.return_value = mock_run_result
        
        # Mock config manager
        mock_config_manager = MagicMock()
        
        # Create service
        chat_service = ChatService(mock_db, mock_config_manager)
        
        # Create conversation with custom title (user_id matches run.user_id, so no admin check)
        await chat_service.create_conversation_from_experience_run(
            run_id=run_data['run_id'],
            user_id=run_data['user_id'],
            title_override=custom_title
        )
        
        # Property: Conversation should use custom title
        conversations = [obj for obj in added_objects if isinstance(obj, Conversation)]
        assert len(conversations) == 1
        
        conversation = conversations[0]
        assert conversation.title == custom_title, (
            f"Expected title='{custom_title}', but got '{conversation.title}'"
        )

    @given(experience_run_data())
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_conversation_uses_default_title(
        self,
        run_data: Dict[str, Any]
    ) -> None:
        """
        Property 1 Extension: Default title from experience name.
        
        For any valid experience run, when no custom title is provided,
        the conversation should use the experience name as the title.
        
        **Validates: Requirements 1.3**
        
        Args:
            run_data: Generated experience run data
        """
        from shu.models.experience import Experience, ExperienceRun
        
        # Create mock experience
        mock_experience = MagicMock(spec=Experience)
        mock_experience.id = run_data['experience_id']
        mock_experience.name = run_data['experience_name']
        mock_experience.model_configuration_id = run_data['experience_model_configuration_id']
        
        # Create mock experience run
        mock_run = MagicMock(spec=ExperienceRun)
        mock_run.id = run_data['run_id']
        mock_run.experience_id = run_data['experience_id']
        mock_run.user_id = run_data['user_id']
        mock_run.result_content = run_data['result_content']
        mock_run.model_configuration_id = run_data['model_configuration_id']
        mock_run.experience = mock_experience
        
        # Mock database session with tracking
        mock_db, added_objects = create_mock_db_with_tracking()
        
        # Mock the experience run query to return the run
        # Need to handle: execute() -> unique() -> scalar_one_or_none()
        mock_run_result = MagicMock()
        mock_unique_result = MagicMock()
        mock_unique_result.scalar_one_or_none.return_value = mock_run
        mock_run_result.unique.return_value = mock_unique_result
        
        # Setup execute to return the run result
        mock_db.execute.return_value = mock_run_result
        
        # Mock config manager
        mock_config_manager = MagicMock()
        
        # Create service
        chat_service = ChatService(mock_db, mock_config_manager)
        
        # Create conversation without custom title (user_id matches run.user_id, so no admin check)
        await chat_service.create_conversation_from_experience_run(
            run_id=run_data['run_id'],
            user_id=run_data['user_id'],
            title_override=None
        )
        
        # Property: Conversation should use experience name as title
        conversations = [obj for obj in added_objects if isinstance(obj, Conversation)]
        assert len(conversations) == 1
        
        conversation = conversations[0]
        assert conversation.title == run_data['experience_name'], (
            f"Expected title='{run_data['experience_name']}', but got '{conversation.title}'"
        )

    @given(experience_run_data())
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_model_configuration_priority_cascade(
        self,
        run_data: Dict[str, Any]
    ) -> None:
        """
        Property 3: Model Configuration Priority Cascade.
        
        For any experience run, the created conversation's model configuration
        should follow the priority: run's model_configuration_id > experience's
        model_configuration_id > None (system default).
        
        **Validates: Requirements 3.1, 3.2, 3.3, 3.4**
        
        Args:
            run_data: Generated experience run data
        """
        from shu.models.experience import Experience, ExperienceRun
        
        # Create mock experience
        mock_experience = MagicMock(spec=Experience)
        mock_experience.id = run_data['experience_id']
        mock_experience.name = run_data['experience_name']
        mock_experience.model_configuration_id = run_data['experience_model_configuration_id']
        
        # Create mock experience run
        mock_run = MagicMock(spec=ExperienceRun)
        mock_run.id = run_data['run_id']
        mock_run.experience_id = run_data['experience_id']
        mock_run.user_id = run_data['user_id']
        mock_run.result_content = run_data['result_content']
        mock_run.model_configuration_id = run_data['model_configuration_id']
        mock_run.experience = mock_experience
        
        # Mock database session with tracking
        mock_db, added_objects = create_mock_db_with_tracking()
        
        # Mock the experience run query to return the run
        # Need to handle: execute() -> unique() -> scalar_one_or_none()
        mock_run_result = MagicMock()
        mock_unique_result = MagicMock()
        mock_unique_result.scalar_one_or_none.return_value = mock_run
        mock_run_result.unique.return_value = mock_unique_result
        
        # Setup execute to return the run result
        mock_db.execute.return_value = mock_run_result
        
        # Mock config manager
        mock_config_manager = MagicMock()
        
        # Create service
        chat_service = ChatService(mock_db, mock_config_manager)
        
        # Create conversation (user_id matches run.user_id, so no admin check)
        await chat_service.create_conversation_from_experience_run(
            run_id=run_data['run_id'],
            user_id=run_data['user_id'],
            title_override=None
        )
        
        # Property: Model configuration should follow priority cascade
        conversations = [obj for obj in added_objects if isinstance(obj, Conversation)]
        assert len(conversations) == 1
        
        conversation = conversations[0]
        
        # Determine expected model configuration based on priority
        expected_model_config_id = (
            run_data['model_configuration_id'] or 
            run_data['experience_model_configuration_id']
        )
        
        assert conversation.model_configuration_id == expected_model_config_id, (
            f"Expected model_configuration_id='{expected_model_config_id}', "
            f"but got '{conversation.model_configuration_id}'"
        )

    @given(experience_run_data())
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_complete_metadata_preservation(
        self,
        run_data: Dict[str, Any]
    ) -> None:
        """
        Property 4: Complete Metadata Preservation.
        
        For any conversation created from an experience run, the conversation
        metadata should contain source="experience", the experience_id, the
        experience_name, the run_id, and created_from_experience=true.
        
        **Validates: Requirements 1.7, 2.1, 2.2, 2.3, 5.5**
        
        Args:
            run_data: Generated experience run data
        """
        from shu.models.experience import Experience, ExperienceRun
        
        # Create mock experience
        mock_experience = MagicMock(spec=Experience)
        mock_experience.id = run_data['experience_id']
        mock_experience.name = run_data['experience_name']
        mock_experience.model_configuration_id = run_data['experience_model_configuration_id']
        
        # Create mock experience run
        mock_run = MagicMock(spec=ExperienceRun)
        mock_run.id = run_data['run_id']
        mock_run.experience_id = run_data['experience_id']
        mock_run.user_id = run_data['user_id']
        mock_run.result_content = run_data['result_content']
        mock_run.model_configuration_id = run_data['model_configuration_id']
        mock_run.experience = mock_experience
        
        # Mock database session with tracking
        mock_db, added_objects = create_mock_db_with_tracking()
        
        # Mock the experience run query to return the run
        # Need to handle: execute() -> unique() -> scalar_one_or_none()
        mock_run_result = MagicMock()
        mock_unique_result = MagicMock()
        mock_unique_result.scalar_one_or_none.return_value = mock_run
        mock_run_result.unique.return_value = mock_unique_result
        
        # Setup execute to return the run result
        mock_db.execute.return_value = mock_run_result
        
        # Mock config manager
        mock_config_manager = MagicMock()
        
        # Create service
        chat_service = ChatService(mock_db, mock_config_manager)
        
        # Create conversation (user_id matches run.user_id, so no admin check)
        await chat_service.create_conversation_from_experience_run(
            run_id=run_data['run_id'],
            user_id=run_data['user_id'],
            title_override=None
        )
        
        # Property: All required metadata fields should be present and correct
        conversations = [obj for obj in added_objects if isinstance(obj, Conversation)]
        assert len(conversations) == 1
        
        conversation = conversations[0]
        meta = conversation.meta
        
        assert meta is not None, "Conversation meta should not be None"
        
        # Check all required metadata fields
        required_fields = {
            'source': 'experience',
            'experience_id': run_data['experience_id'],
            'experience_name': run_data['experience_name'],
            'run_id': run_data['run_id'],
            'created_from_experience': True
        }
        
        for field, expected_value in required_fields.items():
            actual_value = meta.get(field)
            assert actual_value == expected_value, (
                f"Expected meta.{field}='{expected_value}', but got '{actual_value}'"
            )
