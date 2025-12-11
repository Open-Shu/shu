"""
Service for reading and writing application-wide system settings.

Settings are stored using the SystemSetting model as JSON blobs, enabling
flexible configuration without additional schema migrations.
"""

from typing import Any, Dict, Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.system_setting import SystemSetting


class SystemSettingsService:
    """Persistence helpers for the SystemSetting table."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_setting(self, key: str) -> Optional[SystemSetting]:
        stmt = select(SystemSetting).where(SystemSetting.key == key)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_value(self, key: str, default: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        setting = await self.get_setting(key)
        if setting is None:
            return default
        # Ensure we return a shallow copy so callers don't mutate state accidentally.
        return dict(setting.value or {})

    async def upsert(self, key: str, value: Dict[str, Any]) -> SystemSetting:
        setting = await self.get_setting(key)
        if setting is None:
            setting = SystemSetting(key=key, value=value)
            self.db.add(setting)
        else:
            # Replace the entire value to avoid stale keys hanging around.
            setting.value = dict(value)

        await self.db.commit()
        await self.db.refresh(setting)
        return setting

    async def delete(self, key: str) -> None:
        await self.db.execute(delete(SystemSetting).where(SystemSetting.key == key))
        await self.db.commit()
