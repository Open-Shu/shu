"""Knowledge Base Verification Utility.

This module provides shared utilities for verifying knowledge base existence
and retrieving knowledge base instances across all services.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions import KnowledgeBaseNotFoundError
from ..core.logging import get_logger
from ..models.knowledge_base import KnowledgeBase

logger = get_logger(__name__)


class KnowledgeBaseVerifier:
    """Utility class for knowledge base verification operations.

    Provides shared methods for verifying knowledge base existence
    and retrieving knowledge base instances across all services.
    """

    @staticmethod
    async def verify_exists(db: AsyncSession, kb_id: str) -> KnowledgeBase:
        """Verify that a knowledge base exists and return it.

        Args:
            db: Database session
            kb_id: Knowledge base ID to verify

        Returns:
            KnowledgeBase instance if found

        Raises:
            KnowledgeBaseNotFoundError: If knowledge base doesn't exist

        """
        kb_result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
        knowledge_base = kb_result.scalar_one_or_none()

        if not knowledge_base:
            raise KnowledgeBaseNotFoundError(kb_id)

        return knowledge_base

    @staticmethod
    async def get_optional(db: AsyncSession, kb_id: str) -> KnowledgeBase | None:
        """Get a knowledge base by ID without raising an exception if not found.

        Args:
            db: Database session
            kb_id: Knowledge base ID to retrieve

        Returns:
            KnowledgeBase instance if found, None otherwise

        """
        kb_result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
        return kb_result.scalar_one_or_none()

    @staticmethod
    async def verify_exists_and_enabled(db: AsyncSession, kb_id: str) -> KnowledgeBase:
        """Verify that a knowledge base exists and is enabled.

        Args:
            db: Database session
            kb_id: Knowledge base ID to verify

        Returns:
            KnowledgeBase instance if found and enabled

        Raises:
            KnowledgeBaseNotFoundError: If knowledge base doesn't exist
            ShuException: If knowledge base is disabled

        """
        from ..core.exceptions import ShuException

        knowledge_base = await KnowledgeBaseVerifier.verify_exists(db, kb_id)

        if not knowledge_base.sync_enabled:
            raise ShuException(f"Knowledge base '{kb_id}' is disabled", "KNOWLEDGE_BASE_DISABLED")

        return knowledge_base
