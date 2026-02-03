"""
Property-based tests for config API endpoints.

Tests the setup status API to ensure it correctly reflects experience creation
across various scenarios using property-based testing with Hypothesis.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.strategies import composite

from shu.api.config import get_setup_status
from shu.schemas.config import SetupStatus


@composite
def setup_status_data(draw):
    """Generate realistic setup status data for property testing."""
    return {
        "llm_providers": draw(st.integers(min_value=0, max_value=10)),
        "model_configs": draw(st.integers(min_value=0, max_value=10)),
        "knowledge_bases": draw(st.integers(min_value=0, max_value=20)),
        "documents": draw(st.integers(min_value=0, max_value=1000)),
        "plugins": draw(st.integers(min_value=0, max_value=50)),
        "feeds": draw(st.integers(min_value=0, max_value=100)),
        "experiences": draw(st.integers(min_value=0, max_value=50)),
    }


class TestSetupStatusProperty:
    """Property-based tests for setup status logic."""

    @given(setup_status_data())
    @pytest.mark.asyncio
    async def test_setup_status_reflects_experience_creation(self, status_data):
        """
        Property 1: Setup status reflects experience creation

        For any user who has created at least one experience (including drafts),
        the setup status API should return experience_created: true

        **Validates: Requirements 4.1**
        """
        # Mock database session and query result
        mock_db = AsyncMock()
        mock_result = MagicMock()

        # Create a mock row with the test data
        mock_row = MagicMock()
        mock_row.llm_providers = status_data["llm_providers"]
        mock_row.model_configs = status_data["model_configs"]
        mock_row.knowledge_bases = status_data["knowledge_bases"]
        mock_row.documents = status_data["documents"]
        mock_row.plugins = status_data["plugins"]
        mock_row.feeds = status_data["feeds"]
        mock_row.experiences = status_data["experiences"]

        mock_result.one.return_value = mock_row
        mock_db.execute.return_value = mock_result

        # Mock current user
        mock_user = MagicMock()
        mock_user.id = "test-user-id"

        # Call the setup status function
        response = await get_setup_status(_current_user=mock_user, db=mock_db)

        # Verify the response structure
        assert hasattr(response, "data")
        status = response.data
        assert isinstance(status, SetupStatus)

        # Property: experience_created should be True if and only if experiences > 0
        expected_experience_created = status_data["experiences"] > 0
        assert status.experience_created == expected_experience_created, (
            f"Expected experience_created={expected_experience_created} "
            f"for experiences count={status_data['experiences']}, "
            f"but got {status.experience_created}"
        )

        # Verify other status fields follow the same pattern
        assert status.llm_provider_configured == (status_data["llm_providers"] > 0)
        assert status.model_configuration_created == (status_data["model_configs"] > 0)
        assert status.knowledge_base_created == (status_data["knowledge_bases"] > 0)
        assert status.documents_added == (status_data["documents"] > 0)
        assert status.plugins_enabled == (status_data["plugins"] > 0)
        assert status.plugin_feed_created == (status_data["feeds"] > 0)

    @given(st.integers(min_value=1, max_value=100))
    @pytest.mark.asyncio
    async def test_experience_creation_always_sets_status_true(self, experience_count):
        """
        Property: Any positive number of experiences should result in experience_created=True

        This tests the boundary condition that any experience count > 0 should
        result in experience_created being True.

        **Validates: Requirements 4.1**
        """
        # Mock database session and query result
        mock_db = AsyncMock()
        mock_result = MagicMock()

        # Create a mock row with experiences > 0
        mock_row = MagicMock()
        mock_row.llm_providers = 0
        mock_row.model_configs = 0
        mock_row.knowledge_bases = 0
        mock_row.documents = 0
        mock_row.plugins = 0
        mock_row.feeds = 0
        mock_row.experiences = experience_count  # Always > 0

        mock_result.one.return_value = mock_row
        mock_db.execute.return_value = mock_result

        # Mock current user
        mock_user = MagicMock()
        mock_user.id = "test-user-id"

        # Call the setup status function
        response = await get_setup_status(_current_user=mock_user, db=mock_db)

        # Property: Any positive experience count should result in True
        status = response.data
        assert status.experience_created is True, (
            f"Expected experience_created=True for experiences count={experience_count}, "
            f"but got {status.experience_created}"
        )

    @pytest.mark.asyncio
    async def test_no_experiences_sets_status_false(self):
        """
        Property: Zero experiences should result in experience_created=False

        This tests the boundary condition that experience_created should be False
        when no experiences exist.

        **Validates: Requirements 4.1**
        """
        # Mock database session and query result
        mock_db = AsyncMock()
        mock_result = MagicMock()

        # Create a mock row with no experiences
        mock_row = MagicMock()
        mock_row.llm_providers = 5  # Other fields can be positive
        mock_row.model_configs = 3
        mock_row.knowledge_bases = 2
        mock_row.documents = 10
        mock_row.plugins = 4
        mock_row.feeds = 6
        mock_row.experiences = 0  # Zero experiences

        mock_result.one.return_value = mock_row
        mock_db.execute.return_value = mock_result

        # Mock current user
        mock_user = MagicMock()
        mock_user.id = "test-user-id"

        # Call the setup status function
        response = await get_setup_status(_current_user=mock_user, db=mock_db)

        # Property: Zero experiences should result in False
        status = response.data
        assert status.experience_created is False, (
            f"Expected experience_created=False for experiences count=0, " f"but got {status.experience_created}"
        )
