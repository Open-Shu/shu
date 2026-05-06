"""Knowledge Base Service for Shu RAG Backend.

This module provides business logic for managing knowledge bases,
including CRUD operations, statistics, and configuration management.
"""

from typing import Any, ClassVar

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, selectinload

from ..core.exceptions import (
    ConflictError,
    KnowledgeBaseNotFoundError,
    NotFoundError,
    ShuException,
    ValidationError,
)
from ..core.logging import get_logger
from ..core.text import slugify
from ..models.document import Document, DocumentChunk
from ..models.knowledge_base import KnowledgeBase
from ..schemas.knowledge_base import RAGConfig, RAGConfigResponse
from ..services.policy_engine import POLICY_CACHE, enforce_pbac

logger = get_logger(__name__)


def resolve_personal_kb_name(user) -> str:
    """Compute the display name for a user's Personal Knowledge KB.

    Mirrors the precedence used in ``frontend/.../usePersonalKB.js``:

    1. Multi-token name → ``"{first} {last}'s Knowledge"`` (drops middle names).
       Disambiguates two users sharing a first name — the common case.
    2. Single-token name → ``"{first}'s Knowledge"``.
    3. Email local part → ``"{local}'s Knowledge"`` even if generic-looking
       (admins still need to identify the owner).
    4. Fallback → ``"Personal Knowledge"`` only when neither name nor email
       is present.

    Always prefers something identifying so admins viewing the full KB list
    can tell whose is whose.
    """
    name = (getattr(user, "name", None) or "").strip()
    if name:
        tokens = [t for t in name.split() if t]
        if tokens:
            first = tokens[0]
            if len(tokens) > 1:
                last = tokens[-1]
                return f"{first} {last}'s Knowledge"
            return f"{first}'s Knowledge"

    email = (getattr(user, "email", None) or "").strip()
    if email and "@" in email:
        local = email.split("@", 1)[0].strip()
        if local:
            return f"{local}'s Knowledge"

    return "Personal Knowledge"


class KnowledgeBaseService:
    """Service for managing knowledge bases."""

    def __init__(self, db: AsyncSession, config_manager=None) -> None:
        self.db = db

        # Use dependency injection for ConfigurationManager
        if config_manager is None:
            from ..core.config import get_config_manager

            config_manager = get_config_manager()

        self._config_manager = config_manager

    # Default templates for different use cases
    DEFAULT_TEMPLATES: ClassVar[dict[str, dict[str, Any]]] = {
        "academic": {
            "include_references": True,
            "reference_format": "markdown",
            "context_format": "detailed",
            "prompt_template": "academic",
        },
        "business": {
            "include_references": True,
            "reference_format": "markdown",
            "context_format": "detailed",
            "prompt_template": "business",
        },
        "technical": {
            "include_references": True,
            "reference_format": "markdown",
            "context_format": "detailed",
            "prompt_template": "technical",
        },
        "custom": {
            "include_references": True,
            "reference_format": "markdown",
            "context_format": "detailed",
            "prompt_template": "custom",
        },
    }

    async def get_default_templates(self) -> dict[str, dict[str, Any]]:
        """Get default templates for different use cases.

        Returns:
            Dictionary of default templates

        """
        return self.DEFAULT_TEMPLATES.copy()

    async def get_rag_config(self, kb_id: str) -> RAGConfigResponse:
        """Get RAG configuration for a knowledge base.

        Args:
            kb_id: Knowledge base ID

        Returns:
            RAGConfigResponse with configuration settings

        """
        try:
            knowledge_base = await self._get_knowledge_base(kb_id)
            if not knowledge_base:
                raise KnowledgeBaseNotFoundError(kb_id)

            # Get RAG configuration from the knowledge base model
            rag_config = knowledge_base.get_rag_config()
            logger.debug(f"Retrieved RAG config for KB {kb_id}")

            return RAGConfigResponse(
                include_references=rag_config["include_references"],
                reference_format=rag_config["reference_format"],
                context_format=rag_config["context_format"],
                prompt_template=rag_config["prompt_template"],
                search_threshold=rag_config["search_threshold"],
                max_chunks=rag_config["max_chunks"],
                max_chunks_per_document=rag_config["max_chunks_per_document"],
                chunk_overlap_ratio=rag_config["chunk_overlap_ratio"],
                search_type=rag_config["search_type"],
                title_weighting_enabled=rag_config["title_weighting_enabled"],
                title_weight_multiplier=rag_config["title_weight_multiplier"],
                title_chunk_enabled=rag_config["title_chunk_enabled"],
                minimum_query_words=rag_config["minimum_query_words"],
                fetch_full_documents=rag_config["fetch_full_documents"],
                full_doc_max_docs=rag_config["full_doc_max_docs"],
                full_doc_token_cap=rag_config["full_doc_token_cap"],
                version=rag_config["version"],
            )

        except Exception as e:
            logger.error(f"Failed to get RAG config for knowledge base '{kb_id}': {e}", exc_info=True)
            raise ShuException(f"Failed to get RAG configuration: {e!s}", "RAG_CONFIG_GET_ERROR")

    async def update_rag_config(self, kb_id: str, rag_config: RAGConfig) -> RAGConfigResponse:
        """Update RAG configuration for a knowledge base.

        Args:
            kb_id: Knowledge base ID
            rag_config: New RAG configuration

        Returns:
            Updated RAGConfigResponse

        """
        try:
            knowledge_base = await self._get_knowledge_base(kb_id)
            if not knowledge_base:
                raise KnowledgeBaseNotFoundError(kb_id)

            # Update RAG configuration in the knowledge base model
            config_dict = {
                "include_references": rag_config.include_references,
                "reference_format": rag_config.reference_format,
                "context_format": rag_config.context_format,
                "prompt_template": rag_config.prompt_template,
                "search_threshold": rag_config.search_threshold,
                "max_chunks": rag_config.max_chunks,
                "chunk_overlap_ratio": rag_config.chunk_overlap_ratio,
                "search_type": rag_config.search_type,
                "title_weighting_enabled": rag_config.title_weighting_enabled,
                "title_weight_multiplier": rag_config.title_weight_multiplier,
                "title_chunk_enabled": rag_config.title_chunk_enabled,
                # Full Document Escalation
                "fetch_full_documents": rag_config.fetch_full_documents,
                "full_doc_max_docs": rag_config.full_doc_max_docs,
                "full_doc_token_cap": rag_config.full_doc_token_cap,
                "version": "1.0",
            }

            knowledge_base.update_rag_config(config_dict)

            # Commit the changes to the database
            await self.db.commit()
            await self.db.refresh(knowledge_base)

            # Clear any cached RAG configuration for this knowledge base
            from ..core.cache import get_config_cache

            cache = get_config_cache()
            if hasattr(cache, "_rag_configs") and kb_id in cache._rag_configs:
                del cache._rag_configs[kb_id]
                logger.debug(f"Cleared cached RAG config for KB {kb_id}")

            logger.info(f"Successfully updated RAG config for KB {kb_id}")

            # Get the actual saved configuration from the database (with ConfigurationManager defaults)
            # This ensures we return the real saved values, not just the input values
            saved_config = knowledge_base.get_rag_config()

            return RAGConfigResponse(
                include_references=saved_config["include_references"],
                reference_format=saved_config["reference_format"],
                context_format=saved_config["context_format"],
                prompt_template=saved_config["prompt_template"],
                search_threshold=saved_config["search_threshold"],
                max_chunks=saved_config["max_chunks"],
                max_chunks_per_document=saved_config["max_chunks_per_document"],
                chunk_overlap_ratio=saved_config["chunk_overlap_ratio"],
                search_type=saved_config["search_type"],
                title_weighting_enabled=saved_config["title_weighting_enabled"],
                title_weight_multiplier=saved_config["title_weight_multiplier"],
                title_chunk_enabled=saved_config["title_chunk_enabled"],
                minimum_query_words=saved_config["minimum_query_words"],
                fetch_full_documents=saved_config["fetch_full_documents"],
                full_doc_max_docs=saved_config["full_doc_max_docs"],
                full_doc_token_cap=saved_config["full_doc_token_cap"],
                version=saved_config["version"],
            )

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to update RAG config for knowledge base '{kb_id}': {e}", exc_info=True)
            raise ShuException(f"Failed to update RAG configuration: {e!s}", "RAG_CONFIG_UPDATE_ERROR")

    async def recalculate_kb_stats(self, kb_id: str) -> dict[str, int]:
        """Recalculate and update KB document/chunk counts from actual data.

        This performs a full recalculation rather than incremental updates,
        which handles edge cases like feed cursor resets or re-runs.

        Args:
            kb_id: Knowledge base ID

        Returns:
            Dictionary with updated document_count and total_chunks

        """
        try:
            # Count documents
            doc_count_result = await self.db.execute(
                select(func.count(Document.id)).where(Document.knowledge_base_id == kb_id)
            )
            document_count = doc_count_result.scalar() or 0

            # Count chunks using direct FK (no JOIN needed - DocumentChunk has knowledge_base_id)
            chunk_count_result = await self.db.execute(
                select(func.count(DocumentChunk.id)).where(DocumentChunk.knowledge_base_id == kb_id)
            )
            total_chunks = chunk_count_result.scalar() or 0

            # Update the KB's denormalized stats
            kb = await self.db.get(KnowledgeBase, kb_id)
            if kb:
                kb.update_document_stats(document_count, total_chunks)
                await self.db.commit()
                logger.debug(f"Recalculated KB stats: kb_id={kb_id}, docs={document_count}, chunks={total_chunks}")

            return {"document_count": document_count, "total_chunks": total_chunks}

        except Exception as e:
            logger.error(f"Failed to recalculate stats for KB '{kb_id}': {e}", exc_info=True)
            await self.db.rollback()
            raise

    async def adjust_document_stats(self, kb_id: str, doc_delta: int = 0, chunk_delta: int = 0) -> None:
        """Atomically adjust KB document/chunk counts by delta values.

        Use this for single-document operations (manual upload, delete) where a full
        recalculation would be wasteful. For batch operations (feed sync), use
        recalculate_kb_stats() instead.

        Args:
            kb_id: Knowledge base ID
            doc_delta: Change in document count (+1 for add, -1 for delete)
            chunk_delta: Change in chunk count

        """
        if doc_delta == 0 and chunk_delta == 0:
            return

        try:
            from sqlalchemy import update

            await self.db.execute(
                update(KnowledgeBase)
                .where(KnowledgeBase.id == kb_id)
                .values(
                    document_count=KnowledgeBase.document_count + doc_delta,
                    total_chunks=KnowledgeBase.total_chunks + chunk_delta,
                )
            )
            await self.db.commit()
            logger.debug(f"Adjusted KB stats: kb_id={kb_id}, doc_delta={doc_delta}, chunk_delta={chunk_delta}")

        except Exception as e:
            logger.error(f"Failed to adjust stats for KB '{kb_id}': {e}", exc_info=True)
            await self.db.rollback()
            raise

    async def get_knowledge_base_stats(self, kb_id: str) -> dict[str, Any]:
        """Get statistics for a specific knowledge base by recalculating from actual data.

        This method recalculates stats from the database. For listing KBs,
        prefer using the denormalized stats directly from the KB model.

        Args:
            kb_id: Knowledge base ID

        Returns:
            Dictionary with document and chunk counts

        """
        try:
            # Get document count
            doc_count_result = await self.db.execute(
                select(func.count(Document.id)).where(Document.knowledge_base_id == kb_id)
            )
            document_count = doc_count_result.scalar() or 0

            # Get total chunk count using direct FK (no JOIN needed)
            chunk_count_result = await self.db.execute(
                select(func.count(DocumentChunk.id)).where(DocumentChunk.knowledge_base_id == kb_id)
            )
            total_chunks = chunk_count_result.scalar() or 0

            return {"document_count": document_count, "total_chunks": total_chunks}

        except Exception as e:
            logger.error(f"Failed to get stats for knowledge base '{kb_id}': {e}", exc_info=True)
            return {"document_count": 0, "total_chunks": 0}

    async def list_knowledge_bases(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
    ) -> tuple[list[KnowledgeBase], int]:
        """List knowledge bases with optional filtering, pagination, and PBAC.

        Results are filtered by ``kb.read`` PBAC; denied slugs are pushed
        into the SQL WHERE clause so pagination happens in the database
        rather than in Python.

        Args:
            user_id: User ID for PBAC ``kb.read`` enforcement.
            limit: Maximum number of knowledge bases to return.
            offset: Number of knowledge bases to skip.
            search: Optional search term for filtering by name.

        Returns:
            Tuple of (knowledge_bases, total_count).

        """
        try:
            slug_result = await self.db.execute(select(KnowledgeBase.slug))
            all_slugs = [row[0] for row in slug_result.fetchall()]
            denied = await self._get_denied_kb_slugs(user_id, all_slugs)

            conditions = []
            if denied:
                conditions.append(KnowledgeBase.slug.notin_(denied))
            if search:
                escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                conditions.append(KnowledgeBase.name.ilike(f"%{escaped}%", escape="\\"))

            base = select(KnowledgeBase)
            if conditions:
                base = base.where(and_(*conditions))

            count_result = await self.db.execute(
                select(func.count(KnowledgeBase.id)).where(and_(*conditions))
                if conditions
                else select(func.count(KnowledgeBase.id))
            )
            total = count_result.scalar() or 0

            query = base.order_by(KnowledgeBase.status, KnowledgeBase.name).offset(offset).limit(limit)
            result = await self.db.execute(query)
            return list(result.scalars().all()), total

        except ShuException:
            raise
        except Exception as e:
            logger.error(f"Failed to list knowledge bases: {e}", exc_info=True)
            raise ShuException(f"Failed to list knowledge bases: {e!s}", "KNOWLEDGE_BASE_LIST_ERROR")

    async def _get_knowledge_base(self, kb_id: str) -> KnowledgeBase | None:
        """Fetch a KB by ID without access control.

        Use this only from admin/system code paths where there is no user
        context: management operations behind role guards, background jobs,
        and other internal services that operate on already-validated
        resources. User-facing code should call ``get_knowledge_base(kb_id,
        user_id)`` instead.

        """
        try:
            from ..utils import KnowledgeBaseVerifier

            return await KnowledgeBaseVerifier.get_optional(self.db, kb_id)

        except Exception as e:
            logger.error(f"Failed to get knowledge base '{kb_id}': {e}", exc_info=True)
            raise ShuException(f"Failed to get knowledge base: {e!s}", "KNOWLEDGE_BASE_GET_ERROR")

    async def fetch_raw_knowledge_base(self, kb_id: str) -> KnowledgeBase | None:
        """Fetch a KB by ID without PBAC enforcement for internal system callers."""
        return await self._get_knowledge_base(kb_id)

    async def get_knowledge_base(self, kb_id: str, user_id: str) -> KnowledgeBase:
        """Get a knowledge base by ID with PBAC kb.read enforcement.

        Raises NotFoundError if the KB does not exist or the user is denied,
        using the same error to avoid leaking KB existence.

        Owner escape (SHU-742) — must mirror ``_get_denied_kb_slugs`` so the
        single-fetch path agrees with the list / filter / chat-attach paths.
        Without that consistency, a regular user could see a KB in their
        list but 404 when opening it or fetching its docs.

        - **Owner escape**: a user always has kb.read on KBs they own,
          without needing an explicit PBAC grant. Necessary so a user's
          auto-provisioned Personal Knowledge KB stays accessible without
          per-user policy setup.

        Non-owners go through ``enforce_pbac`` regardless of whether the KB
        is personal or not. Cross-user reads require an explicit PBAC
        ``kb.read`` allow policy authored by an admin (typically targeting
        a user, group, or ``*``).

        Args:
            kb_id: Knowledge base ID.
            user_id: The user to enforce access for.

        Returns:
            The KnowledgeBase if found and access is granted.

        Raises:
            NotFoundError: KB missing or access denied.

        """
        kb = await self._get_knowledge_base(kb_id)
        if not kb:
            raise NotFoundError(f"Knowledge base '{kb_id}' not found")
        if kb.owner_id is not None and str(kb.owner_id) == str(user_id):
            return kb
        await enforce_pbac(
            user_id,
            "kb.read",
            f"kb:{kb.slug}",
            self.db,
            message=f"Knowledge base '{kb_id}' not found",
        )
        return kb

    async def slug_exists(self, slug: str) -> bool:
        """Check whether a knowledge base with the given slug already exists.

        Args:
            slug: The slug to check.

        Returns:
            True if a KB with this slug exists, False otherwise.

        """
        return await self._get_kb_by_slug(slug) is not None

    async def _get_kb_by_slug(self, slug: str, exclude_id: str | None = None) -> KnowledgeBase | None:
        """Get a knowledge base by slug, optionally excluding a specific KB.

        Args:
            slug: The slug to look up.
            exclude_id: KB ID to exclude from the search (used during updates).

        Returns:
            KnowledgeBase if found, None otherwise.

        """
        stmt = select(KnowledgeBase).where(KnowledgeBase.slug == slug)
        if exclude_id:
            stmt = stmt.where(KnowledgeBase.id != exclude_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _return_or_heal_existing_personal_kb(self, existing: KnowledgeBase, owner_id: str) -> KnowledgeBase:
        """Idempotent return for ensure-style personal-KB creates.

        Heals ``is_personal=True`` on the existing row if the flag was missing
        (legacy rows that predate the column or were otherwise mis-flagged) so
        the frontend's ``findPersonalKB`` filter picks it up on subsequent
        fetches.

        Refuses to take over a row owned by a different user. The owner-scoped
        slug ``personal-knowledge-{owner_id}`` makes ownership match by
        construction in the normal flow, but defense-in-depth: if a row with
        that slug exists with a different owner, raise ConflictError rather
        than healing.
        """
        if existing.owner_id is not None and str(existing.owner_id) != str(owner_id):
            raise ConflictError("Knowledge base slug already in use")
        if not existing.is_personal:
            existing.is_personal = True
            await self.db.commit()
            await self.db.refresh(existing)
        logger.info(f"Personal KB already exists; returning existing: {existing.name}")
        return existing

    async def create_knowledge_base(self, kb_data, owner_id: str) -> KnowledgeBase:
        """Create a new (non-personal) knowledge base.

        Personal KBs go through ``ensure_personal_knowledge_base`` instead.
        ``is_personal`` is server-controlled per endpoint and always False here,
        so the schema no longer accepts it as a client field.

        Args:
            kb_data: Validated request body (KnowledgeBaseCreate).
            owner_id: ID of the creating user; becomes ``kb.owner_id``.

        Returns:
            Created KnowledgeBase row.

        Raises:
            ValidationError: name has no alphanumeric content.
            ConflictError: slug already exists.

        """
        slug = slugify(kb_data.name)
        if not slug:
            raise ValidationError("Knowledge base name must contain at least one alphanumeric character")

        try:
            if await self._get_kb_by_slug(slug) is not None:
                raise ConflictError(f"Knowledge base '{kb_data.name}' already exists")

            kb_dict = kb_data.model_dump()
            kb_dict["slug"] = slug
            kb_dict["owner_id"] = owner_id
            kb_dict["is_personal"] = False

            knowledge_base = KnowledgeBase(**kb_dict)
            self.db.add(knowledge_base)
            await self.db.commit()
            await self.db.refresh(knowledge_base)

            logger.info(f"Created knowledge base: {knowledge_base.name}")
            return knowledge_base

        except ShuException:
            await self.db.rollback()
            raise
        except IntegrityError as e:
            await self.db.rollback()
            constraint_name = (getattr(getattr(e, "orig", None), "constraint_name", "") or "").lower()
            if "slug" in constraint_name:
                raise ConflictError(f"Knowledge base '{kb_data.name}' already exists")
            logger.error(f"Unexpected integrity error creating KB: {e}", exc_info=True)
            raise ShuException(f"Failed to create knowledge base: {e!s}", "KNOWLEDGE_BASE_CREATE_ERROR")
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to create knowledge base: {e}", exc_info=True)
            raise ShuException(f"Failed to create knowledge base: {e!s}", "KNOWLEDGE_BASE_CREATE_ERROR")

    async def ensure_personal_knowledge_base(self, owner_id: str, display_name: str) -> KnowledgeBase:
        """Idempotently ensure the caller's Personal Knowledge KB exists.

        Returns the existing row if one already exists for ``owner_id`` (with
        ``is_personal`` healed if needed), otherwise creates and returns a new
        one. Safe to call from a hot path like the brain icon's first upload.

        The slug is owner-scoped (``personal-knowledge-{owner_id}``), so two
        users with the same display name each get their own KB. Personal KBs
        get the divergent RAG profile (Full Document Escalation enabled) from
        ``settings.personal_kb_rag_fetch_full_documents``.

        Args:
            owner_id: The caller's user ID; becomes both the slug suffix and
                ``kb.owner_id``.
            display_name: Resolved display name for the KB. Computed by the
                router from the User row via ``resolve_personal_kb_name``.

        Returns:
            The KnowledgeBase row (existing or newly created).

        Raises:
            ValidationError: ``owner_id`` is missing.
            ConflictError: a row with the owner-scoped slug exists with a
                different owner_id.

        """
        if not owner_id:
            raise ValidationError("Personal knowledge bases require an owner_id")

        slug = f"personal-knowledge-{owner_id}"

        try:
            existing = await self._get_kb_by_slug(slug)
            if existing is not None:
                return await self._return_or_heal_existing_personal_kb(existing, owner_id)

            from ..core.config import get_settings_instance

            settings = get_settings_instance()

            knowledge_base = KnowledgeBase(
                name=display_name,
                slug=slug,
                owner_id=owner_id,
                is_personal=True,
                rag_fetch_full_documents=settings.personal_kb_rag_fetch_full_documents,
            )
            self.db.add(knowledge_base)
            await self.db.commit()
            await self.db.refresh(knowledge_base)

            logger.info(f"Created personal knowledge base: {knowledge_base.name}")
            return knowledge_base

        except ShuException:
            await self.db.rollback()
            raise
        except IntegrityError as e:
            await self.db.rollback()
            constraint_name = (getattr(getattr(e, "orig", None), "constraint_name", "") or "").lower()
            if "slug" in constraint_name:
                # Concurrent create raced us to the same slug. Re-fetch and
                # heal-or-return; if the racing row was created by a different
                # owner (defense in depth), the helper raises ConflictError.
                existing = await self._get_kb_by_slug(slug)
                if existing is not None:
                    return await self._return_or_heal_existing_personal_kb(existing, owner_id)
            logger.error(f"Unexpected integrity error creating personal KB: {e}", exc_info=True)
            raise ShuException(f"Failed to create personal knowledge base: {e!s}", "KNOWLEDGE_BASE_CREATE_ERROR")
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to create personal knowledge base: {e}", exc_info=True)
            raise ShuException(f"Failed to create personal knowledge base: {e!s}", "KNOWLEDGE_BASE_CREATE_ERROR")

    async def update_knowledge_base(self, kb_id: str, update_data) -> KnowledgeBase:
        """Update an existing knowledge base.

        Args:
            kb_id: Knowledge base ID
            update_data: Update data (KnowledgeBaseUpdate Pydantic model)

        Returns:
            Updated KnowledgeBase

        """
        try:
            knowledge_base = await self._get_knowledge_base(kb_id)
            if not knowledge_base:
                raise KnowledgeBaseNotFoundError(kb_id)

            # Update fields from Pydantic model
            update_dict = update_data.model_dump(exclude_unset=True)

            if "name" in update_dict and update_dict["name"] != knowledge_base.name:
                new_slug = slugify(update_dict["name"])
                if not new_slug:
                    raise ValidationError("Knowledge base name must contain at least one alphanumeric character")
                if await self._get_kb_by_slug(new_slug, exclude_id=kb_id):
                    raise ConflictError(f"Knowledge base '{update_dict['name']}' already exists")
                knowledge_base.slug = new_slug

            for field, value in update_dict.items():
                if hasattr(knowledge_base, field):
                    setattr(knowledge_base, field, value)

            await self.db.commit()
            await self.db.refresh(knowledge_base)

            logger.info(f"Updated knowledge base: {knowledge_base.name}")
            return knowledge_base

        except ShuException:
            await self.db.rollback()
            raise
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to update knowledge base '{kb_id}': {e}", exc_info=True)
            raise ShuException(f"Failed to update knowledge base: {e!s}", "KNOWLEDGE_BASE_UPDATE_ERROR")

    async def delete_knowledge_base(self, kb_id: str) -> None:
        """Delete a knowledge base.

        Args:
            kb_id: Knowledge base ID

        """
        try:
            knowledge_base = await self._get_knowledge_base(kb_id)
            if not knowledge_base:
                raise KnowledgeBaseNotFoundError(kb_id)

            # Check if knowledge base has documents (this would need to be implemented based on your document model)
            # For now, we'll just delete the knowledge base
            await self.db.delete(knowledge_base)
            await self.db.commit()

            logger.info(f"Deleted knowledge base: {knowledge_base.name}")

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to delete knowledge base '{kb_id}': {e}", exc_info=True)
            raise ShuException(f"Failed to delete knowledge base: {e!s}", "KNOWLEDGE_BASE_DELETE_ERROR")

    async def get_overall_knowledge_base_stats(self) -> dict[str, Any]:
        """Get overall statistics for all knowledge bases.

        Aggregates stats from KB denormalized columns for efficiency.

        Returns:
            Dictionary with overall statistics

        """
        try:
            # Get total knowledge bases
            total_result = await self.db.execute(select(func.count(KnowledgeBase.id)))
            total_kbs = total_result.scalar() or 0

            # Get active knowledge bases
            active_result = await self.db.execute(
                select(func.count(KnowledgeBase.id)).where(KnowledgeBase.status == "active")
            )
            active_kbs = active_result.scalar() or 0

            # Get sync enabled count
            sync_enabled_result = await self.db.execute(
                select(func.count(KnowledgeBase.id)).where(KnowledgeBase.sync_enabled)
            )
            sync_enabled_count = sync_enabled_result.scalar() or 0

            # Aggregate document and chunk counts from KB denormalized stats
            total_docs_result = await self.db.execute(select(func.sum(KnowledgeBase.document_count)))
            total_documents = total_docs_result.scalar() or 0

            total_chunks_result = await self.db.execute(select(func.sum(KnowledgeBase.total_chunks)))
            total_chunks = total_chunks_result.scalar() or 0

            return {
                "total_knowledge_bases": total_kbs,
                "active_knowledge_bases": active_kbs,
                "total_documents": total_documents,
                "total_chunks": total_chunks,
                "sync_enabled_count": sync_enabled_count,
                "source_type_breakdown": {},
                "status_breakdown": {"active": active_kbs, "inactive": total_kbs - active_kbs},
            }

        except Exception as e:
            logger.error(f"Failed to get knowledge base statistics: {e}", exc_info=True)
            raise ShuException(f"Failed to get knowledge base statistics: {e!s}", "KNOWLEDGE_BASE_STATS_ERROR")

    async def enable_sync(self, kb_id: str) -> KnowledgeBase:
        """Enable sync for a knowledge base.

        Args:
            kb_id: Knowledge base ID

        Returns:
            Updated KnowledgeBase

        """
        try:
            knowledge_base = await self._get_knowledge_base(kb_id)
            if not knowledge_base:
                raise KnowledgeBaseNotFoundError(kb_id)

            knowledge_base.sync_enabled = True  # type: ignore[attr-defined]
            await self.db.commit()
            await self.db.refresh(knowledge_base)

            logger.info(f"Enabled sync for knowledge base: {knowledge_base.name}")
            return knowledge_base

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to enable sync for knowledge base '{kb_id}': {e}", exc_info=True)
            raise ShuException(f"Failed to enable sync: {e!s}", "KNOWLEDGE_BASE_SYNC_ENABLE_ERROR")

    async def disable_sync(self, kb_id: str) -> KnowledgeBase:
        """Disable sync for a knowledge base.

        Args:
            kb_id: Knowledge base ID

        Returns:
            Updated KnowledgeBase

        """
        try:
            knowledge_base = await self._get_knowledge_base(kb_id)
            if not knowledge_base:
                raise KnowledgeBaseNotFoundError(kb_id)

            knowledge_base.sync_enabled = False  # type: ignore[attr-defined]
            await self.db.commit()
            await self.db.refresh(knowledge_base)

            logger.info(f"Disabled sync for knowledge base: {knowledge_base.name}")
            return knowledge_base

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to disable sync for knowledge base '{kb_id}': {e}", exc_info=True)
            raise ShuException(f"Failed to disable sync: {e!s}", "KNOWLEDGE_BASE_SYNC_DISABLE_ERROR")

    async def get_documents(
        self,
        kb_id: str,
        limit: int = 50,
        offset: int = 0,
        search_query: str | None = None,
        filter_by: str = "all",
        source_type: str | None = None,
        file_type: str | None = None,
        *,
        user_id: str,
    ) -> tuple[list[Document], int]:
        """Get documents for a knowledge base with pagination.

        Args:
            kb_id: Knowledge base ID
            limit: Maximum number of documents to return
            offset: Number of documents to skip
            search_query: Optional title search term
            filter_by: Extraction metadata filter (all/ocr/text/high-confidence/low-confidence)
            source_type: Optional filter by document source type
            file_type: Optional filter by file type
            user_id: User ID for PBAC kb.read enforcement

        Returns:
            Tuple of (documents, total_count)

        """
        try:
            await self.get_knowledge_base(kb_id, user_id)

            document_filter_condition = self.get_document_filter_condition(
                kb_id, search_query, filter_by, source_type, file_type
            )

            # Get total count
            count_query = select(func.count(Document.id)).where(document_filter_condition)
            total_result = await self.db.execute(count_query)
            total = total_result.scalar() or 0

            # Get documents with pagination (exclude content field)
            query = (
                select(Document)
                .options(defer(Document.content))
                .where(document_filter_condition)
                .order_by(Document.created_at.desc())
                .offset(offset)
                .limit(limit)
            )

            result = await self.db.execute(query)
            documents = list(result.scalars().all())

            logger.debug(f"Retrieved {len(documents)} documents for KB {kb_id}")
            return documents, int(total)

        except ShuException:
            raise
        except Exception as e:
            logger.error(f"Failed to get documents for knowledge base {kb_id}: {e}")
            raise ShuException(f"Failed to get documents: {e!s}", "DOCUMENT_LIST_ERROR")

    def get_document_filter_condition(
        self,
        kb_id: str,
        search_query: str | None,
        filter_by: str,
        source_type: str | None = None,
        file_type: str | None = None,
    ):
        conditions = [Document.knowledge_base_id == kb_id]

        if search_query:
            escaped = search_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions.append(Document.title.ilike(f"%{escaped}%", escape="\\"))

        if source_type:
            conditions.append(Document.source_type == source_type)

        if file_type:
            conditions.append(Document.file_type == file_type)

        if filter_by and filter_by != "all":
            if filter_by == "ocr":
                conditions.append(Document.extraction_method == "ocr")
            elif filter_by == "text":
                conditions.append(Document.extraction_method == "text")
            elif filter_by == "high-confidence":
                conditions.append(Document.extraction_confidence >= 0.8)
            elif filter_by == "low-confidence":
                conditions.append(Document.extraction_confidence < 0.6)

        return and_(*conditions)

    async def get_document(
        self,
        kb_id: str,
        document_id: str,
        include_chunks: bool = False,
        *,
        user_id: str,
    ) -> Document:
        """Get a specific document from a knowledge base.

        Args:
            kb_id: Knowledge base ID
            document_id: Document ID
            include_chunks: Whether to eager-load document chunks
            user_id: User ID for PBAC kb.read enforcement

        Returns:
            Document instance.

        Raises:
            NotFoundError: Document or knowledge base not found.

        """
        try:
            await self.get_knowledge_base(kb_id, user_id)

            query = select(Document).where(Document.knowledge_base_id == kb_id, Document.id == document_id)
            if include_chunks:
                query = query.options(selectinload(Document.chunks))

            result = await self.db.execute(query)
            doc = result.scalar_one_or_none()
            if not doc:
                raise NotFoundError(f"Document '{document_id}' not found in knowledge base '{kb_id}'")
            return doc

        except ShuException:
            raise
        except Exception as e:
            logger.error(f"Failed to get document {document_id} from KB {kb_id}: {e}")
            raise ShuException(f"Failed to get document: {e!s}")

    async def get_knowledge_base_summary(self, kb_id: str, *, user_id: str):
        """Build a high-level summary for a knowledge base including distinct source types
        and aggregate document/chunk counts.
        """
        try:
            kb = await self.get_knowledge_base(kb_id, user_id)

            # Distinct source types for this KB
            result = await self.db.execute(
                select(Document.source_type).where(Document.knowledge_base_id == kb_id).distinct()
            )
            source_types = [row[0] for row in result.fetchall() if row[0] is not None]

            # Return a dict that matches KnowledgeBaseSummary fields
            # Use denormalized stats directly from KB model
            return {
                "id": kb.id,
                "slug": kb.slug,
                "name": kb.name,
                "description": kb.description,
                "source_types": source_types,
                "status": kb.status,
                "document_count": kb.document_count,
                "total_chunks": kb.total_chunks,
                "last_sync_at": kb.last_sync_at,
            }
        except ShuException:
            raise
        except Exception as e:
            logger.error(f"Failed to build knowledge base summary for '{kb_id}': {e}", exc_info=True)
            raise ShuException(f"Failed to get knowledge base summary: {e!s}", "KNOWLEDGE_BASE_SUMMARY_ERROR")

    async def get_knowledge_base_source_types(self, kb_id: str) -> list[str]:
        """Return distinct source types present in this knowledge base's documents."""
        try:
            # Ensure KB exists
            kb = await self._get_knowledge_base(kb_id)
            if not kb:
                raise KnowledgeBaseNotFoundError(kb_id)

            result = await self.db.execute(
                select(Document.source_type).where(Document.knowledge_base_id == kb_id).distinct()
            )
            return [row[0] for row in result.fetchall() if row[0] is not None]
        except Exception as e:
            logger.error(f"Failed to get source types for KB '{kb_id}': {e}", exc_info=True)
            raise ShuException(f"Failed to get source types: {e!s}", "KNOWLEDGE_BASE_SOURCE_TYPES_ERROR")

    async def validate_knowledge_base_config(self, kb_id: str) -> dict[str, Any]:
        """Validate the knowledge base configuration and return errors/warnings.
        This performs lightweight schema/range checks; it does not perform I/O validation.
        """
        try:
            kb = await self._get_knowledge_base(kb_id)
            if not kb:
                raise KnowledgeBaseNotFoundError(kb_id)

            cfg = kb.get_rag_config()
            errors: list[str] = []
            warnings: list[str] = []

            # Range validations
            thr = cfg.get("search_threshold")
            if thr is None or not (0 <= float(thr) <= 1):
                errors.append("search_threshold must be between 0 and 1")

            max_chunks = cfg.get("max_chunks")
            if max_chunks is None or int(max_chunks) <= 0:
                errors.append("max_chunks must be a positive integer")

            overlap = cfg.get("chunk_overlap_ratio")
            if overlap is None or not (0 <= float(overlap) < 1):
                errors.append("chunk_overlap_ratio must be between 0 (inclusive) and 1 (exclusive)")

            twm = cfg.get("title_weight_multiplier")
            if twm is None or float(twm) <= 0:
                errors.append("title_weight_multiplier must be > 0")

            # Template warning (non-fatal)
            tmpl = cfg.get("prompt_template")
            try:
                defaults = await self.get_default_templates()
                if tmpl not in defaults:
                    warnings.append(f"Unknown prompt_template '{tmpl}' - using as custom")
            except Exception:
                # If defaults lookup fails, do not hard-fail validation
                warnings.append("Could not verify prompt_template against defaults")

            is_valid = len(errors) == 0
            return {"is_valid": is_valid, "errors": errors, "warnings": warnings}
        except ShuException:
            raise
        except Exception as e:
            logger.error(f"Failed to validate KB config for '{kb_id}': {e}", exc_info=True)
            raise ShuException(f"Failed to validate configuration: {e!s}", "KNOWLEDGE_BASE_VALIDATE_ERROR")

    async def set_knowledge_base_status(self, kb_id: str, new_status: dict[str, Any]) -> KnowledgeBase:
        """Update the status field on a knowledge base with basic validation."""
        try:
            kb = await self._get_knowledge_base(kb_id)
            if not kb:
                raise KnowledgeBaseNotFoundError(kb_id)

            allowed = {"active", "inactive", "error"}
            # Allow either a dict with 'status' key or a raw string
            status_value = new_status.get("status") if isinstance(new_status, dict) else new_status
            if status_value not in allowed:
                raise ValidationError(f"Invalid status '{status_value}'. Allowed: {sorted(allowed)}")

            kb.status = status_value
            await self.db.commit()
            await self.db.refresh(kb)
            return kb
        except ShuException:
            await self.db.rollback()
            raise
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to set status for KB '{kb_id}': {e}", exc_info=True)
            raise ShuException(f"Failed to set knowledge base status: {e!s}", "KNOWLEDGE_BASE_SET_STATUS_ERROR")

    async def trigger_re_embedding(
        self,
        kb_id: str,
        *,
        embedding_service,
        queue_backend,
    ) -> dict[str, Any]:
        """Validate, mark, and enqueue a re-embedding job for a knowledge base.

        Checks that the KB exists and is eligible (stale or error), marks it
        as ``re_embedding``, and enqueues the worker job. If enqueue fails the
        status is reverted so the admin can retry.

        Args:
            kb_id: Knowledge base ID.
            embedding_service: Injected EmbeddingService instance.
            queue_backend: Injected QueueBackend instance.

        Returns:
            Dict with ``status``, ``knowledge_base_id``, and ``total_chunks``.

        Raises:
            KnowledgeBaseNotFoundError: KB does not exist.
            ValidationError: KB embeddings are already current.
            ConflictError: Re-embedding already in progress.

        """
        from ..core.workload_routing import WorkloadType, enqueue_job

        # Lock the row to prevent concurrent re-embedding requests
        result = await self.db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id).with_for_update())
        kb = result.scalar_one_or_none()
        if kb is None:
            raise KnowledgeBaseNotFoundError(kb_id)

        if kb.embedding_model == embedding_service.model_name and kb.embedding_status == "current":
            raise ValidationError("Knowledge base embeddings are already current")

        if kb.embedding_status == "re_embedding":
            from datetime import UTC, datetime, timedelta

            stale_after = timedelta(minutes=3)
            last_updated = kb.updated_at
            if last_updated is not None and last_updated.tzinfo is None:
                last_updated = last_updated.replace(tzinfo=UTC)

            is_stale = last_updated is None or (datetime.now(UTC) - last_updated) >= stale_after
            if not is_stale:
                raise ConflictError("Re-embedding is already in progress for this knowledge base")
            logger.info(
                "Re-embedding appears stale for KB, allowing re-trigger",
                extra={
                    "kb_id": kb_id,
                    "last_updated": str(last_updated),
                    "stale_after_seconds": int(stale_after.total_seconds()),
                },
            )

        # Count chunks for progress tracking
        result = await self.db.execute(
            select(func.count(DocumentChunk.id)).where(DocumentChunk.knowledge_base_id == kb_id)
        )
        total_chunks = result.scalar() or 0

        # Determine number of parallel chunk workers
        from ..core.config import get_settings_instance

        settings = get_settings_instance()
        batches_needed = max(1, -(-total_chunks // settings.embedding_batch_size))  # ceil division
        num_workers = min(settings.worker_concurrency, batches_needed)

        # Capture original state so we can restore it on enqueue failure
        original_status = kb.embedding_status
        original_progress = kb.re_embedding_progress

        # Mark KB as re-embedding
        kb.mark_re_embedding_started(total_chunks, workers_total=num_workers)
        await self.db.commit()

        # Enqueue parallel chunk jobs; revert status on failure so admin can retry
        try:
            for i in range(num_workers):
                await enqueue_job(
                    queue_backend,
                    WorkloadType.RE_EMBEDDING,
                    payload={
                        "knowledge_base_id": kb_id,
                        "action": "re_embed_chunks",
                        "worker_index": i,
                        "workers_total": num_workers,
                    },
                    max_attempts=3,
                    visibility_timeout=600,
                )
        except Exception:
            kb.embedding_status = original_status
            kb.re_embedding_progress = original_progress
            await self.db.commit()
            raise

        logger.info(
            "Re-embedding jobs enqueued",
            extra={"kb_id": kb_id, "total_chunks": total_chunks, "workers": num_workers},
        )

        return {"status": "queued", "knowledge_base_id": kb_id, "total_chunks": total_chunks}

    async def _get_denied_kb_slugs(self, user_id: str, slugs: list[str]) -> set[str]:
        """Return the set of KB slugs denied by PBAC ``kb.read`` for *user_id*.

        Read-visibility model:

        - **Owner escape**: KBs owned by *user_id* are always readable by
          their owner, even without a PBAC binding. Encodes the universal
          "creators can read what they create" invariant — necessary so a
          user's auto-provisioned Personal Knowledge KB stays accessible
          without per-user policy setup.
        - **Everything else**: PBAC default-deny applies. Cross-user reads
          (whether the target KB is personal or not) require an explicit
          ``kb.read`` allow policy authored by an admin.

        Without the owner escape, ``PolicyCache.get_denied_resources`` would
        be default-deny for users with no policy bindings, hiding their own
        KBs from list, chat-attach verify, and single-fetch endpoints.

        Mirrored by ``get_knowledge_base`` so single-fetch and list paths
        agree — otherwise a user could see a KB in their list but 404 when
        opening it.
        """
        if not slugs:
            return set()
        denied = await POLICY_CACHE.get_denied_resources(user_id, "kb.read", "kb", slugs, self.db)
        if denied:
            escape_result = await self.db.execute(
                select(KnowledgeBase.slug).where(
                    and_(
                        KnowledgeBase.slug.in_(denied),
                        KnowledgeBase.owner_id == user_id,
                    )
                )
            )
            escape_slugs = {row[0] for row in escape_result.fetchall()}
            denied -= escape_slugs
        return denied

    async def filter_accessible_kb_ids(self, user_id: str, kbs: list[KnowledgeBase]) -> list[str]:
        """Filter a list of KB ORM objects, returning IDs of those the user can read."""
        if not kbs:
            return []
        denied = await self._get_denied_kb_slugs(user_id, [kb.slug for kb in kbs])
        return [kb.id for kb in kbs if kb.slug not in denied]

    async def check_kb_read_access(self, user_id: str, kb_ids: list[str]) -> str | None:
        """Check PBAC kb.read for a list of KB IDs.

        Unknown IDs are treated identically to denied IDs so callers
        cannot distinguish "missing" from "forbidden" (non-enumeration).

        Returns None if all accessible, or the first denied/missing KB ID.
        """
        if not kb_ids:
            return None
        kbs = (await self.db.execute(select(KnowledgeBase).where(KnowledgeBase.id.in_(kb_ids)))).scalars().all()

        found_ids = {kb.id for kb in kbs}
        for kb_id in kb_ids:
            if kb_id not in found_ids:
                return kb_id

        denied = await self._get_denied_kb_slugs(user_id, [kb.slug for kb in kbs])
        for kb in kbs:
            if kb.slug in denied:
                return kb.id
        return None


async def detect_stale_kbs(db: AsyncSession, system_model: str) -> list[str]:
    """Detect and mark KBs whose embeddings are from a different model.

    Compares each KB's recorded embedding_model against the system's configured
    model. KBs that don't match are marked as 'stale'.

    Args:
        db: Database session.
        system_model: The currently configured embedding model name.

    Returns:
        List of KB IDs that were marked stale.

    """
    # Find KBs that are 'current' but have a different embedding model
    result = await db.execute(
        select(KnowledgeBase.id).where(
            and_(
                or_(
                    KnowledgeBase.embedding_model != system_model,
                    KnowledgeBase.embedding_model.is_(None),
                ),
                KnowledgeBase.embedding_status == "current",
            )
        )
    )
    stale_ids = [str(row[0]) for row in result.fetchall()]

    if stale_ids:
        await db.execute(update(KnowledgeBase).where(KnowledgeBase.id.in_(stale_ids)).values(embedding_status="stale"))
        await db.commit()
        logger.warning(
            f"Marked {len(stale_ids)} knowledge base(s) as stale — "
            f"embedding model mismatch (system={system_model})",
            extra={"stale_kb_ids": stale_ids},
        )

    return stale_ids
