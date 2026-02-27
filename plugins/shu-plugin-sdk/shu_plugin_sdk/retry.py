"""Retry/backoff decorator for async plugin functions.

Usage::

    from shu_plugin_sdk import RetryConfig, RetryableError, with_retry

    @with_retry(RetryConfig(max_retries=3, base_delay=1.0))
    async def fetch_data():
        try:
            return await host.http.fetch("GET", url)
        except HttpRequestFailed as e:
            if e.is_retryable:
                raise RetryableError(str(e)) from e
            raise

The decorator retries on ``RetryableError`` with exponential backoff and raises
immediately on ``NonRetryableError`` or any other exception type.
"""

from __future__ import annotations

import asyncio
import functools
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

_T = TypeVar("_T")


class RetryableError(Exception):
    """Raise inside a ``@with_retry``-decorated function to trigger a retry."""


class NonRetryableError(Exception):
    """Raise inside a ``@with_retry``-decorated function to bypass all retries."""


@dataclass
class RetryConfig:
    """Configuration for exponential-backoff retry behaviour.

    Attributes:
        max_retries: Number of *additional* attempts after the first failure.
            Total attempts = max_retries + 1.
        base_delay: Initial sleep duration in seconds before the first retry.
        max_delay: Upper bound on sleep duration regardless of backoff growth.
        backoff_factor: Multiplier applied to the delay after each retry.
    """

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    backoff_factor: float = 2.0

    def delay_for(self, attempt: int) -> float:
        """Return sleep duration (seconds) before retry number ``attempt`` (0-indexed)."""
        return min(self.base_delay * (self.backoff_factor ** attempt), self.max_delay)


def with_retry(config: RetryConfig) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator factory — wraps an async function with retry/backoff logic.

    Args:
        config: ``RetryConfig`` controlling retry count and delay schedule.

    Returns:
        A decorator that wraps the target async function.

    Raises:
        RetryableError: Re-raised after ``config.max_retries`` retries are exhausted.
        NonRetryableError: Raised immediately, bypassing all retries.
        Any other exception: Propagated immediately without retrying.
    """
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(config.max_retries + 1):
                try:
                    return await fn(*args, **kwargs)
                except NonRetryableError:
                    raise
                except RetryableError:
                    if attempt < config.max_retries:
                        await asyncio.sleep(config.delay_for(attempt))
                    else:
                        raise
        return wrapper
    return decorator
