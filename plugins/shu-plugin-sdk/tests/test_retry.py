"""Tests for shu_plugin_sdk.retry (RetryConfig, with_retry, error types)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from shu_plugin_sdk.retry import NonRetryableError, RetryableError, RetryConfig, with_retry


# ---------------------------------------------------------------------------
# RetryConfig.delay_for()
# ---------------------------------------------------------------------------


def test_delay_for_first_retry() -> None:
    cfg = RetryConfig(base_delay=1.0, backoff_factor=2.0, max_delay=60.0)
    assert cfg.delay_for(0) == 1.0


def test_delay_for_exponential_growth() -> None:
    cfg = RetryConfig(base_delay=1.0, backoff_factor=2.0, max_delay=60.0)
    assert cfg.delay_for(1) == 2.0
    assert cfg.delay_for(2) == 4.0
    assert cfg.delay_for(3) == 8.0


def test_delay_for_capped_at_max_delay() -> None:
    cfg = RetryConfig(base_delay=1.0, backoff_factor=2.0, max_delay=5.0)
    assert cfg.delay_for(10) == 5.0


def test_delay_for_custom_base_delay() -> None:
    cfg = RetryConfig(base_delay=0.5, backoff_factor=3.0, max_delay=60.0)
    assert cfg.delay_for(0) == 0.5
    assert cfg.delay_for(1) == 1.5


# ---------------------------------------------------------------------------
# @with_retry — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_on_first_attempt() -> None:
    """Function succeeds immediately — no retries, no sleep."""
    mock_fn = AsyncMock(return_value="ok")
    decorated = with_retry(RetryConfig(max_retries=3))(mock_fn)

    with patch("shu_plugin_sdk.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await decorated()

    assert result == "ok"
    mock_fn.assert_awaited_once()
    mock_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_passes_args_and_kwargs_through() -> None:
    mock_fn = AsyncMock(return_value=42)
    decorated = with_retry(RetryConfig())(mock_fn)

    with patch("shu_plugin_sdk.retry.asyncio.sleep", new_callable=AsyncMock):
        result = await decorated("a", "b", key="val")

    mock_fn.assert_awaited_once_with("a", "b", key="val")
    assert result == 42


# ---------------------------------------------------------------------------
# @with_retry — retry on RetryableError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retries_on_retryable_error_then_succeeds() -> None:
    """Fails twice with RetryableError then succeeds on the third attempt."""
    call_count = 0

    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RetryableError("transient")
        return "done"

    decorated = with_retry(RetryConfig(max_retries=3))(flaky)

    with patch("shu_plugin_sdk.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await decorated()

    assert result == "done"
    assert call_count == 3
    assert mock_sleep.await_count == 2  # slept before attempt 2 and 3


@pytest.mark.asyncio
async def test_raises_after_max_retries_exhausted() -> None:
    """Always raises RetryableError — should re-raise after max_retries attempts."""
    mock_fn = AsyncMock(side_effect=RetryableError("always fails"))
    decorated = with_retry(RetryConfig(max_retries=2))(mock_fn)

    with patch("shu_plugin_sdk.retry.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(RetryableError, match="always fails"):
            await decorated()

    assert mock_fn.await_count == 3  # 1 initial + 2 retries


@pytest.mark.asyncio
async def test_sleep_durations_match_config() -> None:
    """Sleep is called with the correct backoff delay each time."""
    mock_fn = AsyncMock(side_effect=RetryableError("fail"))
    cfg = RetryConfig(max_retries=3, base_delay=1.0, backoff_factor=2.0, max_delay=60.0)
    decorated = with_retry(cfg)(mock_fn)

    with patch("shu_plugin_sdk.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(RetryableError):
            await decorated()

    sleep_calls = [call.args[0] for call in mock_sleep.call_args_list]
    assert sleep_calls == [1.0, 2.0, 4.0]


@pytest.mark.asyncio
async def test_no_sleep_after_final_retry() -> None:
    """No sleep call is made after the last attempt fails."""
    mock_fn = AsyncMock(side_effect=RetryableError("fail"))
    cfg = RetryConfig(max_retries=1)
    decorated = with_retry(cfg)(mock_fn)

    with patch("shu_plugin_sdk.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(RetryableError):
            await decorated()

    assert mock_sleep.await_count == 1  # only before the one retry


# ---------------------------------------------------------------------------
# @with_retry — NonRetryableError bypasses retries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_retryable_error_raised_immediately() -> None:
    """NonRetryableError skips all retries and is re-raised on the first attempt."""
    mock_fn = AsyncMock(side_effect=NonRetryableError("auth failed"))
    decorated = with_retry(RetryConfig(max_retries=5))(mock_fn)

    with patch("shu_plugin_sdk.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(NonRetryableError, match="auth failed"):
            await decorated()

    mock_fn.assert_awaited_once()
    mock_sleep.assert_not_awaited()


# ---------------------------------------------------------------------------
# @with_retry — other exceptions propagate unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_other_exceptions_propagate_without_retry() -> None:
    """Arbitrary exceptions are not caught — they propagate immediately."""
    mock_fn = AsyncMock(side_effect=ValueError("bad input"))
    decorated = with_retry(RetryConfig(max_retries=3))(mock_fn)

    with patch("shu_plugin_sdk.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(ValueError, match="bad input"):
            await decorated()

    mock_fn.assert_awaited_once()
    mock_sleep.assert_not_awaited()


# ---------------------------------------------------------------------------
# @with_retry — preserves function metadata
# ---------------------------------------------------------------------------


def test_wraps_preserves_function_name() -> None:
    async def my_plugin_call():
        pass

    decorated = with_retry(RetryConfig())(my_plugin_call)
    assert decorated.__name__ == "my_plugin_call"


def test_wraps_preserves_docstring() -> None:
    async def my_plugin_call():
        """Fetches data from the API."""

    decorated = with_retry(RetryConfig())(my_plugin_call)
    assert decorated.__doc__ == "Fetches data from the API."


# ---------------------------------------------------------------------------
# @with_retry — zero retries (max_retries=0)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_retries_raises_on_first_failure() -> None:
    """max_retries=0 means no retries — raises on the first RetryableError."""
    mock_fn = AsyncMock(side_effect=RetryableError("fail"))
    decorated = with_retry(RetryConfig(max_retries=0))(mock_fn)

    with patch("shu_plugin_sdk.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(RetryableError):
            await decorated()

    mock_fn.assert_awaited_once()
    mock_sleep.assert_not_awaited()
