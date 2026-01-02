"""Configuration-related schemas for Shu API"""

from pydantic import BaseModel
from typing import List


class UploadRestrictions(BaseModel):
    """File upload restrictions for different upload contexts"""
    allowed_types: List[str]
    max_size_bytes: int


class PublicConfig(BaseModel):
    """Public configuration that can be safely exposed to frontend"""
    google_client_id: str
    app_name: str
    version: str
    environment: str
    # Chat attachments (supports images via OCR)
    upload_restrictions: UploadRestrictions
    # KB document upload (no standalone image support - text extraction only)
    kb_upload_restrictions: UploadRestrictions


class SetupStatus(BaseModel):
    """Setup completion status for QuickStart wizard"""
    llm_provider_configured: bool
    model_configuration_created: bool
    knowledge_base_created: bool
    documents_added: bool
    plugins_enabled: bool
    plugin_feed_created: bool

