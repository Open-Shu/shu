"""shu-plugin-sdk: Developer SDK for building and testing Shu plugins."""

from __future__ import annotations

from shu_plugin_sdk.contracts import assert_plugin_contract
from shu_plugin_sdk.result import IngestionResult, PluginResult, Skip, SkipReason
from shu_plugin_sdk.retry import NonRetryableError, RetryableError, RetryConfig, with_retry
from shu_plugin_sdk.testing import FakeHostBuilder, HttpRequestFailed, patch_retry_sleep

__all__ = [
    "assert_plugin_contract",
    "FakeHostBuilder",
    "HttpRequestFailed",
    "patch_retry_sleep",
    "IngestionResult",
    "NonRetryableError",
    "PluginResult",
    "RetryableError",
    "RetryConfig",
    "Skip",
    "SkipReason",
    "with_retry",
]
