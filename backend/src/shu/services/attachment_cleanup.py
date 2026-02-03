"""Scheduled cleanup for chat attachments."""

import asyncio
import logging
import os
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings_instance
from ..core.database import get_db_session
from ..models.attachment import Attachment

logger = logging.getLogger(__name__)


class AttachmentCleanupService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.settings = get_settings_instance()

    async def cleanup_expired_attachments(self, batch_size: int = 500, dry_run: bool = False) -> int:
        """Delete expired attachments and remove files from disk.
        Returns number of attachments deleted.
        """
        now = datetime.now(UTC)
        # Primary criterion: expires_at <= now
        stmt = select(Attachment).where(Attachment.expires_at != None, Attachment.expires_at <= now).limit(batch_size)
        result = await self.db.execute(stmt)
        attachments = list(result.scalars().all())
        deleted = 0
        for att in attachments:
            try:
                if att.storage_path and os.path.exists(att.storage_path):
                    if dry_run:
                        logger.info(f"DRY RUN: would remove file {att.storage_path}")
                    else:
                        os.remove(att.storage_path)
                if not dry_run:
                    await self.db.delete(att)
                deleted += 1
            except Exception as e:
                logger.warning(f"Error cleaning attachment {att.id}: {e}")
        if not dry_run:
            await self.db.commit()
        return deleted


async def start_attachment_cleanup_scheduler():
    settings = get_settings_instance()
    interval = getattr(settings, "chat_attachment_cleanup_interval_seconds", 6 * 3600)

    async def _runner():
        while True:
            try:
                db = await get_db_session()
                async with db as session:
                    service = AttachmentCleanupService(session)
                    count = await service.cleanup_expired_attachments()
                    if count:
                        logger.info(f"Attachment cleanup deleted {count} expired attachments")
            except Exception as e:
                logger.warning(f"Attachment cleanup run failed: {e}")
            finally:
                try:
                    await asyncio.sleep(interval)
                except asyncio.CancelledError:
                    break

    # Fire-and-forget task; caller should hold task handle if they need cancellation
    return asyncio.create_task(_runner(), name="attachments:cleanup:scheduler")
