"""pytest configuration for the GitHub plugin test suite.

Automatically suppresses ``asyncio.sleep`` delays inside ``@with_retry``
decorated functions so retry tests run instantly without any manual patching.
"""

from __future__ import annotations

import pytest

from shu_plugin_sdk.testing import patch_retry_sleep


@pytest.fixture(autouse=True)
def _no_retry_sleep():
    with patch_retry_sleep():
        yield
