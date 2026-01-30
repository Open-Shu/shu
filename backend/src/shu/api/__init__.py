"""
API package for Shu RAG Backend.

This package contains FastAPI routers for all API endpoints.
"""

from .auth import router as auth_router
from .branding import router as branding_router
from .config import router as config_router
from .groups import router as groups_router
from .health import router as health_router
from .knowledge_bases import router as knowledge_bases_router
from .permissions import router as permissions_router
from .prompts import router as prompts_router
from .query import router as query_router
from .system import router as system_router
from .user_permissions import router as user_permissions_router
from .user_preferences import router as user_preferences_router

__all__ = [
    "auth_router",
    "branding_router",
    "config_router",
    "groups_router",
    "health_router",
    "knowledge_bases_router",
    "permissions_router",
    "prompts_router",
    "query_router",
    "system_router",
    "user_permissions_router",
    "user_preferences_router",
]
