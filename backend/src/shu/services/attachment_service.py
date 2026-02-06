"""AttachmentService handles chat attachments: saving files, extracting text, and persistence."""

import datetime as dt
import mimetypes
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings_instance
from ..models.attachment import Attachment, MessageAttachment
from ..processors.text_extractor import TextExtractor


class AttachmentService:
    def __init__(self, db_session: AsyncSession) -> None:
        self.db = db_session
        self.settings = get_settings_instance()
        # Ensure storage directory exists
        Path(self.settings.chat_attachment_storage_dir).mkdir(parents=True, exist_ok=True)

    async def _fast_extract_text(self, storage_path: Path, retry: bool = True) -> tuple[str, dict[str, Any]]:
        extractor = TextExtractor()
        try:
            extraction = await extractor.extract_text(
                str(storage_path), file_content=None, use_ocr=False, kb_config=None
            )
            text = (extraction or {}).get("text", "")
            meta = (extraction or {}).get("metadata", {}) or {}
            if not meta.get("method"):
                meta["method"] = "fast_extraction"
            return text, meta
        except Exception as ex:
            if retry:
                return await self._fast_extract_text(storage_path, retry=False)
            return "", {
                "method": "fast_extraction",
                "engine": "unknown",
                "confidence": None,
                "duration": None,
                "details": {"error": str(ex)},
            }

    def _sanitize_filename(self, filename: str, fallback_ext: str = "") -> str:
        """Sanitize a filename to prevent header injection, path traversal, etc.

        Keeps only alphanumeric characters, dots, underscores, hyphens, and spaces.
        Ensures the result is a valid, non-empty filename with preserved extension.

        Args:
            filename: The original filename to sanitize
            fallback_ext: Extension to use if filename becomes invalid (without leading dot)

        Returns:
            A sanitized filename safe for storage and HTTP headers

        """
        raw_name = Path(filename).name
        sanitized = "".join(c for c in raw_name if c.isalnum() or c in "._- ").strip()

        # Ensure we have a valid filename
        if not sanitized or sanitized.startswith("."):
            sanitized = f"attachment.{fallback_ext}" if fallback_ext else "attachment"

        # Ensure extension is preserved
        if fallback_ext and not sanitized.lower().endswith(f".{fallback_ext}"):
            sanitized = f"{sanitized}.{fallback_ext}"

        return sanitized

    async def save_upload(
        self,
        *,
        conversation_id: str,
        user_id: str,
        filename: str,
        file_bytes: bytes,
    ) -> tuple[Attachment, str]:
        """Save an uploaded file to disk, extract text, and create Attachment.

        Returns (attachment, temp_path).
        Caller may optionally remove temp_path later if using external storage.
        """
        # Validate extension
        ext = Path(filename).suffix.lower().lstrip(".")
        allowed = [t.lower() for t in self.settings.chat_attachment_allowed_types]
        if ext not in allowed:
            raise ValueError(f"Unsupported file type: {ext}")

        # Sanitize filename
        sanitized_name = self._sanitize_filename(filename, fallback_ext=ext)

        # Enforce size limit
        max_size = self.settings.chat_attachment_max_size
        if len(file_bytes) > max_size:
            raise ValueError(f"File too large: {len(file_bytes)} > {max_size}")

        # Determine MIME (use original filename for accurate detection)
        mime_type, _ = mimetypes.guess_type(filename)
        mime_type = mime_type or "application/octet-stream"

        # Save to storage
        att_id = str(uuid.uuid4())
        storage_dir = Path(self.settings.chat_attachment_storage_dir)
        storage_path = storage_dir / f"{att_id}_{sanitized_name}"
        with open(storage_path, "wb") as f:
            f.write(file_bytes)
        file_size = storage_path.stat().st_size

        # Extract text (Alpha policy: fast extraction only for chat uploads)
        text, meta = await self._fast_extract_text(storage_path)

        # Create DB record
        import datetime as _dt

        expires_at = _dt.datetime.now(_dt.UTC) + _dt.timedelta(days=self.settings.chat_attachment_ttl_days)

        attachment = Attachment(
            id=att_id,
            conversation_id=conversation_id,
            user_id=user_id,
            original_filename=sanitized_name,
            storage_path=str(storage_path),
            mime_type=mime_type,
            file_type=ext,
            file_size=file_size,
            extracted_text=text or None,
            extracted_text_length=len(text) if text else 0,
            extraction_method=meta.get("method"),
            extraction_engine=meta.get("engine"),
            extraction_confidence=meta.get("confidence"),
            extraction_duration=meta.get("duration"),
            extraction_metadata=meta.get("details"),
            expires_at=expires_at,
        )

        self.db.add(attachment)
        await self.db.commit()
        await self.db.refresh(attachment)
        return attachment, str(storage_path)

    async def get_attachments_by_ids(self, conversation_id: str, user_id: str, ids: list[str]) -> list[Attachment]:
        """Fetch attachments by IDs and ensure they belong to the conversation and user."""
        if not ids:
            return []
        stmt = select(Attachment).where(Attachment.id.in_(ids))
        result = await self.db.execute(stmt)
        return [a for a in result.scalars().all() if a.conversation_id == conversation_id and a.user_id == user_id]

    async def get_conversation_attachments_with_links(
        self, conversation_id: str, user_id: str
    ) -> list[tuple[str, Attachment]]:
        """Fetch (message_id, attachment) pairs for a conversation owned by the user.

        Excludes expired attachments (expires_at in the past).
        """
        now = dt.datetime.now(dt.UTC)

        stmt = (
            select(MessageAttachment.message_id, Attachment)
            .join(Attachment, Attachment.id == MessageAttachment.attachment_id)
            .where(
                Attachment.conversation_id == conversation_id,
                Attachment.user_id == user_id,
                # Exclude expired attachments
                (Attachment.expires_at.is_(None)) | (Attachment.expires_at > now),
            )
        )
        result = await self.db.execute(stmt)
        return result.all()
