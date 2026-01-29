"""
Provider adapters package.

Import built-in adapters so they register with the adapter registry.
"""

# Import adapters for side-effect registration
from .adapters import (
    anthropic_adapter,
    gemini_adapter,
    generic_completions_adapter,
    lmstudio_adapter,
    local_adapter,
    ollama_adapter,
    openai_adapter,
    perplexity_adapter,
    xai_adapter,
)

__all__ = [
    "anthropic_adapter",
    "gemini_adapter",
    "generic_completions_adapter",
    "lmstudio_adapter",
    "local_adapter",
    "ollama_adapter",
    "openai_adapter",
    "perplexity_adapter",
    "xai_adapter",
]
