"""
Provider adapters package.

Import built-in adapters so they register with the adapter registry.
"""

# Import adapters for side-effect registration
from .adapters import openai_adapter  # noqa: F401
from .adapters import anthropic_adapter  # noqa: F401
from .adapters import gemini_adapter  # noqa: F401
from .adapters import generic_completions_adapter  # noqa: F401
from .adapters import local_adapter  # noqa: F401
from .adapters import lmstudio_adapter  # noqa: F401
from .adapters import xai_adapter  # noqa: F401
from .adapters import perplexity_adapter  # noqa: F401
from .adapters import ollama_adapter  # noqa: F401

__all__ = [
    "openai_adapter",
    "anthropic_adapter",
    "gemini_adapter",
    "generic_completions_adapter",
    "local_adapter",
    "lmstudio_adapter",
    "xai_adapter",
    "perplexity_adapter",
    "ollama_adapter",
]
