"""KB Import job handler.

Receives KB_IMPORT jobs from the worker and delegates to
KBImportExportService.execute_import.
"""

from shu.core.logging import get_logger

logger = get_logger(__name__)


async def handle_kb_import_job(job) -> None:
    """Handle a KB_IMPORT workload job.

    Extracts the job payload, creates a fresh DB session, and runs
    the import. Follows the same session management pattern as
    re_embedding_handler.

    Args:
        job: The dequeued Job instance.

    """
    from ..core.database import get_async_session_local
    from ..services.kb_import_export import KBImportExportService
    from ..services.knowledge_base_service import KnowledgeBaseService

    knowledge_base_id = job.payload.get("knowledge_base_id")
    archive_path = job.payload.get("archive_path")
    skip_embeddings = job.payload.get("skip_embeddings", False)

    if not knowledge_base_id or not archive_path:
        raise ValueError(
            f"KB_IMPORT job {job.id} missing required payload fields "
            f"(knowledge_base_id={knowledge_base_id}, archive_path={archive_path})"
        )

    logger.info(
        "Processing KB import job",
        extra={
            "job_id": job.id,
            "knowledge_base_id": knowledge_base_id,
            "archive_path": archive_path,
            "skip_embeddings": skip_embeddings,
        },
    )

    session_local = get_async_session_local()

    async with session_local() as session:
        kb_service = KnowledgeBaseService(session)
        service = KBImportExportService(session, kb_service)
        await service.execute_import(archive_path, knowledge_base_id, skip_embeddings)

    logger.info(
        "KB import job completed",
        extra={"job_id": job.id, "knowledge_base_id": knowledge_base_id},
    )


async def mark_stale_imports_as_error() -> int:
    """Mark KBs stuck in 'importing' status as error on startup.

    If the server restarted, the queue job and temp archive are gone.
    There's no recovery path, so mark them as error so they don't sit
    in limbo.

    Returns:
        Number of KBs marked as error.

    """
    from sqlalchemy import select

    from ..core.database import get_async_session_local
    from ..models.knowledge_base import KnowledgeBase

    session_factory = get_async_session_local()
    count = 0

    async with session_factory() as session:
        result = await session.execute(select(KnowledgeBase).where(KnowledgeBase.status == "importing"))
        stuck_kbs = list(result.scalars().all())

        for kb in stuck_kbs:
            kb.status = "error"
            kb.import_progress = {
                **(kb.import_progress or {}),
                "error": "Import interrupted by server restart",
            }
            count += 1
            logger.warning(
                "Marked stale importing KB as error",
                extra={"kb_id": kb.id, "kb_name": kb.name},
            )

        if count:
            await session.commit()

    return count
