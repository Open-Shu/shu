import types
from unittest.mock import AsyncMock

import pytest

from shu.services.providers.adapter_base import ProviderAdapterContext
from shu.services.providers.adapters.lmstudio_adapter import LMStudioAdapter
from shu.services.providers.parameter_definitions import IntegerParameter, NumberParameter


@pytest.fixture(scope="function")
def mock_db_session():
    return AsyncMock()


@pytest.fixture(scope="function")
def mock_provider():
    return types.SimpleNamespace(
        name="test-lm-studio",
        api_key_encrypted=None,
        config={},
    )


@pytest.fixture(scope="function")
def lmstudio_adapter(mock_db_session, mock_provider):
    return LMStudioAdapter(
        ProviderAdapterContext(
            db_session=mock_db_session,
            provider=mock_provider,
            conversation_owner_id="unit-test-user",
        )
    )


def test_provider_settings(lmstudio_adapter):
    info = lmstudio_adapter.get_provider_information()
    assert info.key == "lm_studio"
    assert info.display_name == "LM Studio"

    capabilities = lmstudio_adapter.get_capabilities()
    assert capabilities.streaming is True
    assert capabilities.tools is True
    assert capabilities.vision is False

    assert lmstudio_adapter.get_api_base_url() == "http://localhost:1234/v1"
    assert lmstudio_adapter.get_chat_endpoint() == "/responses"
    assert lmstudio_adapter.get_models_endpoint() == "/models"
    assert lmstudio_adapter.get_model_information_path() == "data[*].{id: id, name: id}"

    authorization_header = lmstudio_adapter.get_authorization_header()
    assert authorization_header.get("scheme") is None
    assert authorization_header.get("headers") == {}

    parameter_mapping = lmstudio_adapter.get_parameter_mapping()
    assert isinstance(parameter_mapping, dict)
    assert isinstance(parameter_mapping.get("temperature"), NumberParameter)
    assert isinstance(parameter_mapping.get("top_p"), NumberParameter)
    assert isinstance(parameter_mapping.get("max_output_tokens"), IntegerParameter)
