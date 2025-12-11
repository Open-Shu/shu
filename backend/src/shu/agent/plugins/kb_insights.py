"""
KBInsights plugin v0: pull recent documents/titles as simple insights input.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .base import Plugin, PluginInput, PluginResult
from ...core.logging import get_logger
from ...models.document import Document

logger = get_logger(__name__)


class KBInsightsPlugin:
    name = "kb_insights"

    def __init__(self, db: AsyncSession):
        # For v0 we accept a db session at construction if used directly; registry will wrap this
        self._db = db

    async def execute(self, *, user_id: str, agent_key: str, payload: PluginInput) -> PluginResult:
        try:
            params: Dict[str, Any] = payload.params or {}
            hours = int(params.get("since_hours", 72))
            since_dt = datetime.now(timezone.utc) - timedelta(hours=hours)
            kb_id = params.get("knowledge_base_id")
            limit = int(params.get("limit", 20))

            stmt = select(Document).where(
                Document.updated_at >= since_dt
            ).order_by(Document.updated_at.desc()).limit(limit)
            # Note: optionally filter by KB if schema supports it later

            result = await self._db.execute(stmt)
            rows = result.scalars().all()
            items = []
            for d in rows:
                items.append({
                    "id": d.id,
                    "title": d.title,
                    "mime_type": d.mime_type,
                    "updated_at": d.updated_at.isoformat() if d.updated_at else None,
                    "source": d.source_type,
                })

            summary = f"Collected {len(items)} recent KB resources"
            return PluginResult(ok=True, name=self.name, summary=summary, data={"resources": items})
        except Exception as e:
            logger.error("KBInsightsPlugin error", extra={"error": str(e), "agent_key": agent_key, "user_id": user_id})
            return PluginResult(ok=False, name=self.name, summary="kb insights failed", error=str(e))

