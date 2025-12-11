"""
Plugin registry v0: code-registered callables, DB-backed enablement (PluginDefinition).
"""
from __future__ import annotations
from typing import Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from .base import Plugin
from ...models.plugin_registry import PluginDefinition
from ...core.config import get_settings_instance


class PluginRegistry:
    def __init__(self):
        self._plugins: Dict[str, Plugin] = {}

    def register(self, plugin: Plugin):
        self._plugins[plugin.name] = plugin

    def get_registered(self, name: str) -> Optional[Plugin]:
        return self._plugins.get(name)

    async def resolve_enabled(self, db: AsyncSession, name: str, version: str = "v0") -> Optional[Plugin]:
        # Check DB enablement
        stmt = select(PluginDefinition).where(
            PluginDefinition.name == name,
            PluginDefinition.version == version,
            PluginDefinition.enabled == True,
        )
        result = await db.execute(stmt)
        td = result.scalars().first()
        if not td:
            return None
        return self.get_registered(name)


# Global instance for v0
registry = PluginRegistry()

