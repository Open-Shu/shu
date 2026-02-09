"""Provider Type Definitions service: lazy-load from DB on demand.

No secrets are stored here. This module exposes read-only helpers
for listing and fetching provider type definitions.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models.provider_type_definition import ProviderTypeDefinition


class ProviderTypeDefinitionsService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list(self, include_inactive: bool = False) -> list[ProviderTypeDefinition]:
        stmt = select(ProviderTypeDefinition)
        if not include_inactive:
            stmt = stmt.where(ProviderTypeDefinition.is_active)
        res = await self.db.execute(stmt)
        return res.scalars().all()

    async def get(self, key: str) -> ProviderTypeDefinition | None:
        stmt = (
            select(ProviderTypeDefinition)
            .options(selectinload(ProviderTypeDefinition.providers))
            .where(ProviderTypeDefinition.key == key)
        )
        res = await self.db.execute(stmt)
        return res.scalar_one_or_none()
