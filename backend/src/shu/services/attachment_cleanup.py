"""Scheduled cleanup for chat attachments."""

import logging
import os
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings_instance
from ..models.attachment import Attachment

logger = logging.getLogger(__name__)


class AttachmentCleanupService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.settings = get_settings_instance()

    async def cleanup_expired_attachments(self, batch_size: int = 500, dry_run: bool = False) -> int:
        """Delete expired attachments and remove files from disk.

        Uses FOR UPDATE SKIP LOCKED to prevent race conditions when multiple
        scheduler replicas run cleanup concurrently. Each replica claims a
        disjoint set of rows, ensuring no duplicate file deletions or DB errors.

        Returns number of attachments deleted.
        """
        now = datetime.now(UTC)
        # Primary criterion: expires_at <= now
        # Use with_for_update(skip_locked=True) for safe multi-replica operation
        stmt = (
            select(Attachment)
            .where(Attachment.expires_at.is_not(None), Attachment.expires_at <= now)
            .with_for_update(skip_locked=True)
            .limit(batch_size)
        )
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
