"""Attachment models for chat message file uploads.

Stores uploaded blob metadata, storage path, and extracted text for context injection.
"""

from sqlalchemy import JSON, Column, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import relationship

from .base import BaseModel


class Attachment(BaseModel):
    """Attachment uploaded to a conversation for context injection."""

    __tablename__ = "attachments"

    # Ownership and scoping
    conversation_id = Column(String, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # File info
    original_filename = Column(String(500), nullable=False)
    storage_path = Column(String(1000), nullable=False)  # local path or URI
    mime_type = Column(String(100), nullable=False)
    file_type = Column(String(20), nullable=False)  # 'pdf','docx','txt'
    file_size = Column(Integer, nullable=False)  # bytes

    # Extracted text
    extracted_text = Column(Text, nullable=True)
    extracted_text_length = Column(Integer, nullable=True)

    # Extraction metadata
    extraction_method = Column(String(50), nullable=True)
    extraction_engine = Column(String(50), nullable=True)
    extraction_confidence = Column(Float, nullable=True)
    extraction_duration = Column(Float, nullable=True)
    extraction_metadata = Column(JSON, nullable=True)

    # Expiration
    expires_at = Column(TIMESTAMP(timezone=True), nullable=True)

    # Relationships
    conversation = relationship("Conversation", back_populates="attachments", lazy="selectin")


class MessageAttachment(BaseModel):
    """Link table between messages and attachments."""

    __tablename__ = "message_attachments"

    message_id = Column(String, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True)
    attachment_id = Column(String, ForeignKey("attachments.id", ondelete="CASCADE"), nullable=False, index=True)

    __table_args__ = (UniqueConstraint("message_id", "attachment_id", name="uq_message_attachment"),)
