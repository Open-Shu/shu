"""
LLM Integration Package for Shu RAG Backend.

This package provides a comprehensive LLM integration system with:
- Multi-provider support (OpenAI, Anthropic, Ollama, etc.)
- Unified API client for OpenAI-compatible endpoints
- Database-driven configuration management
- Usage tracking and cost monitoring
- Agentic capabilities and tool integration
"""

from .client import UnifiedLLMClient, LLMResponse
from .service import LLMService

__all__ = [
    "UnifiedLLMClient",
    "LLMResponse",
    "LLMService"
]
