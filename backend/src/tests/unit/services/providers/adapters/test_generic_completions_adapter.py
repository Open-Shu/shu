import types
from unittest.mock import AsyncMock

import pytest

from shu.services.providers.adapter_base import (
    ProviderAdapterContext,
    ProviderFinalEventResult,
)
from shu.services.providers.adapters.generic_completions_adapter import GenericCompletionsAdapter
from tests.unit.services.providers.adapters.shared import COMPLETIONS_COMPLETE_OUTPUT_PAYLOAD


@pytest.fixture(scope="function")
def mock_db_session():
    return AsyncMock()


@pytest.fixture(scope="function")
def mock_provider():
    return types.SimpleNamespace(
        name="generic-completions",
        api_key_encrypted=None,
        config={},
    )


@pytest.fixture(scope="function")
def adapter(mock_db_session, mock_provider):
    return GenericCompletionsAdapter(
        ProviderAdapterContext(
            db_session=mock_db_session,
            provider=mock_provider,
            conversation_owner_id="unit-test-user",
        )
    )


def test_defaults(adapter):
    caps = adapter.get_capabilities()
    assert caps.streaming is True
    assert caps.tools is True
    assert caps.vision is False
    assert adapter.get_chat_endpoint() == "/chat/completions"
    assert adapter.get_models_endpoint() == "/models"
    assert adapter.get_model_information_path() == "data[*].{id: id, name: id}"
