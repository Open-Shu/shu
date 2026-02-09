"""Unit tests for DecisionControlStep.

This module tests the decision control step implementation for experience workflows,
ensuring that deterministic decision logic works correctly for all decision types.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from shu.experiences.steps.decision_control import DecisionControlStep


class TestDecisionControlStep:
    """Test suite for DecisionControlStep class."""

    @pytest.fixture
    def decision_step(self):
        """Create a DecisionControlStep instance for testing.

        Returns:
            DecisionControlStep instance
        """
        return DecisionControlStep()

    @pytest.fixture
    def mock_host(self):
        """Create a mock host object with audit capabilities.

        Returns:
            Mock host object
        """
        host = MagicMock()
        host.audit = MagicMock()
        host.audit.log = AsyncMock()
        return host

    @pytest.mark.asyncio
    async def test_car_service_decision_approved(self, decision_step):
        """Test car service decision returns approval."""
        config = {}
        context = {}

        result = await decision_step.execute(step_key="car_service_decision", config=config, context=context, host=None)

        assert result["should_execute"] is True
        assert "Car service approved" in result["rationale"]
        assert result["confidence"] == 1.0
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_tailor_notification_decision_approved(self, decision_step):
        """Test tailor notification decision returns approval."""
        config = {}
        context = {}

        result = await decision_step.execute(
            step_key="tailor_notification_decision", config=config, context=context, host=None
        )

        assert result["should_execute"] is True
        assert "Tailor hold applied" in result["rationale"]
        assert result["confidence"] == 1.0
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_restaurant_decision_approved(self, decision_step):
        """Test restaurant reservation decision returns approval."""
        config = {}
        context = {}

        result = await decision_step.execute(step_key="restaurant_decision", config=config, context=context, host=None)

        assert result["should_execute"] is True
        assert "Restaurant reservation approved" in result["rationale"]
        assert result["confidence"] == 1.0
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_spa_service_decision_declined(self, decision_step):
        """Test spa service decision returns decline based on preferences."""
        config = {}
        context = {}

        result = await decision_step.execute(step_key="spa_service_decision", config=config, context=context, host=None)

        assert result["should_execute"] is False
        assert "don't enjoy spa treatments" in result["rationale"]
        assert result["confidence"] == 1.0
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_unknown_decision_type(self, decision_step):
        """Test unknown decision type returns safe default."""
        config = {}
        context = {}

        result = await decision_step.execute(
            step_key="unknown_decision_type", config=config, context=context, host=None
        )

        assert result["should_execute"] is False
        assert "Unrecognized decision" in result["rationale"]
        assert "unknown_decision_type" in result["rationale"]
        assert result["confidence"] == 1.0
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_execute_with_host_audit_on_error(self, decision_step, mock_host):
        """Test that errors are logged to host audit when available."""
        config = {}
        context = {}

        # Mock the _simple_decision to raise an exception
        original_method = decision_step._simple_decision
        decision_step._simple_decision = MagicMock(side_effect=ValueError("Test error"))

        result = await decision_step.execute(
            step_key="car_service_decision", config=config, context=context, host=mock_host
        )

        # Restore original method
        decision_step._simple_decision = original_method

        assert result["should_execute"] is False
        assert "Decision evaluation failed" in result["rationale"]
        assert result["confidence"] == 0.0
        assert result["error"] is True

        # Verify audit log was called
        mock_host.audit.log.assert_called_once()
        call_args = mock_host.audit.log.call_args[0][0]
        # Verify correlation_id is present (not raw exception)
        assert "correlation_id" in call_args
        assert "error" in call_args
        assert call_args["error"] == "Decision evaluation failed"
        assert call_args["step"] == "decision_control"

    @pytest.mark.asyncio
    async def test_execute_without_host_on_error(self, decision_step):
        """Test that errors are handled gracefully when host is not available."""
        config = {}
        context = {}

        # Mock the _simple_decision to raise an exception
        original_method = decision_step._simple_decision
        decision_step._simple_decision = MagicMock(side_effect=ValueError("Test error"))

        result = await decision_step.execute(step_key="car_service_decision", config=config, context=context, host=None)

        # Restore original method
        decision_step._simple_decision = original_method

        assert result["should_execute"] is False
        assert "Decision evaluation failed" in result["rationale"]
        # Verify correlation ID is present (not raw exception)
        assert "correlation ID:" in result["rationale"]
        assert "correlation_id" in result
        assert result["confidence"] == 0.0
        assert result["error"] is True

    def test_simple_decision_true(self, decision_step):
        """Test _simple_decision helper with should_execute=True."""
        result = decision_step._simple_decision(True, "Test rationale for approval")

        assert result["should_execute"] is True
        assert result["rationale"] == "Test rationale for approval"
        assert result["confidence"] == 1.0

    def test_simple_decision_false(self, decision_step):
        """Test _simple_decision helper with should_execute=False."""
        result = decision_step._simple_decision(False, "Test rationale for decline")

        assert result["should_execute"] is False
        assert result["rationale"] == "Test rationale for decline"
        assert result["confidence"] == 1.0

    @pytest.mark.asyncio
    async def test_all_decision_types_return_dict(self, decision_step):
        """Test that all decision types return properly structured dictionaries."""
        decision_types = [
            "car_service_decision",
            "tailor_notification_decision",
            "restaurant_decision",
            "spa_service_decision",
            "unknown_decision",
        ]

        for decision_type in decision_types:
            result = await decision_step.execute(step_key=decision_type, config={}, context={}, host=None)

            # Verify all results have required keys
            assert "should_execute" in result
            assert "rationale" in result
            assert "confidence" in result
            assert isinstance(result["should_execute"], bool)
            assert isinstance(result["rationale"], str)
            assert isinstance(result["confidence"], float)

    @pytest.mark.asyncio
    async def test_execute_with_config_and_context(self, decision_step):
        """Test execute method accepts config and context parameters."""
        config = {"some_config": "value"}
        context = {"player_data": {"tier": "platinum"}}

        # Should not raise any errors even with populated config/context
        result = await decision_step.execute(step_key="car_service_decision", config=config, context=context, host=None)

        assert result["should_execute"] is True
        assert "error" not in result
