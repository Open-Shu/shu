"""Migration 001: First Release Squash (001..005)

This migration squashes 001..005 into a single base migration that can
initialize the schema from scratch for the current alpha deployment.

Replaces:
- 001_initial_tables
- 002_chat_attachments
- 003_message_variants
- 004_kb_full_document_escalation
- 005_nullable_full_doc_fields
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from datetime import datetime, timezone
import uuid
from sqlalchemy import text

# Optional pgvector
try:
    from pgvector.sqlalchemy import Vector  # type: ignore
except Exception:  # pragma: no cover
    Vector = lambda dim: sa.Text  # fallback for environments without pgvector

# revision identifiers, used by Alembic.
revision = "001"
down_revision = None
branch_labels = None
depends_on = None
replaces = ("001", "002", "003", "004", "005")


def upgrade() -> None:
    # Ensure pgvector is available before creating vector columns
    try:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    except Exception:
        # On platforms without superuser access this might fail; the DB jobs should
        # ensure the extension exists prior to running migrations.
        pass

    # knowledge_bases
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, index=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("sync_enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("last_sync_at", sa.DateTime, nullable=True),
        sa.Column("embedding_model", sa.String(100), nullable=False, server_default=sa.text("'sentence-transformers/all-MiniLM-L6-v2'")),
        sa.Column("chunk_size", sa.Integer, nullable=False, server_default=sa.text("1000")),
        sa.Column("chunk_overlap", sa.Integer, nullable=False, server_default=sa.text("200")),
        sa.Column("rag_include_references", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("rag_reference_format", sa.String(20), nullable=False, server_default=sa.text("'markdown'")),
        sa.Column("rag_context_format", sa.String(20), nullable=False, server_default=sa.text("'detailed'")),
        sa.Column("rag_prompt_template", sa.String(20), nullable=False, server_default=sa.text("'custom'")),
        sa.Column("rag_search_threshold", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default=sa.text("'0.7'")),
        sa.Column("rag_max_results", sa.Integer, nullable=False, server_default=sa.text("10")),
        sa.Column("rag_chunk_overlap_ratio", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default=sa.text("'0.2'")),
        sa.Column("rag_search_type", sa.String(20), nullable=False, server_default=sa.text("'hybrid'")),
        sa.Column("rag_config_version", sa.String(10), nullable=False, server_default=sa.text("'1.0'")),
        sa.Column("rag_title_weighting_enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("rag_title_weight_multiplier", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default=sa.text("'3.0'")),
        sa.Column("rag_title_chunk_enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("rag_max_chunks_per_document", sa.Integer, nullable=False, server_default=sa.text("2")),
        sa.Column("rag_minimum_query_words", sa.Integer, nullable=False, server_default=sa.text("3")),
        sa.Column("status", sa.String(50), nullable=False, server_default=sa.text("'active'")),
        sa.Column("document_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("total_chunks", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("owner_id", sa.String(36), nullable=True),
        # Full-document escalation fields (squashed 004 + 005 => nullable)
        sa.Column("rag_fetch_full_documents", sa.Boolean, nullable=True),
        sa.Column("rag_full_doc_max_docs", sa.Integer, nullable=True),
        sa.Column("rag_full_doc_token_cap", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

    # source_types
    source_types_table = op.create_table(
        "source_types",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(50), unique=True, nullable=False, index=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("configuration_schema", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("default_config", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("requires_authentication", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("supports_sync", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("supports_webhooks", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("supported_file_types", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("max_file_size", sa.String(20), nullable=True),
        sa.Column("supports_incremental_sync", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("supports_deletion_detection", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("supports_metadata_extraction", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

    op.bulk_insert(
        source_types_table,
        [
            {
                "id": "d7c07ede-6c00-43eb-80be-d8f89dd39201",
                "name": "filesystem",
                "display_name": "File System",
                "description": "Local file system source",
                "is_enabled": True,
                "is_default": True,
                "configuration_schema": None,
                "default_config": None,
                "requires_authentication": False,
                "supports_sync": True,
                "supports_webhooks": False,
                "supported_file_types": '["pdf","docx","txt","md"]',
                "max_file_size": "50MB",
                "supports_incremental_sync": True,
                "supports_deletion_detection": True,
                "supports_metadata_extraction": True,
                "created_at": "2025-09-06 01:16:22.882",
                "updated_at": "2025-09-06 01:16:22.882",
            },
            {
                "id": "840ece51-f1ae-4a21-aeb8-477d4bbf2fe4",
                "name": "google_drive",
                "display_name": "Google Drive",
                "description": "Google Drive source",
                "is_enabled": True,
                "is_default": False,
                "configuration_schema": None,
                "default_config": None,
                "requires_authentication": True,
                "supports_sync": True,
                "supports_webhooks": True,
                "supported_file_types": '["pdf","docx","txt","md"]',
                "max_file_size": "50MB",
                "supports_incremental_sync": True,
                "supports_deletion_detection": True,
                "supports_metadata_extraction": True,
                "created_at": "2025-09-06 01:16:22.882",
                "updated_at": "2025-09-06 01:16:22.882",
            },
            {
                "id": "5f9e75a0-cd92-45da-b52f-69619737cf0a",
                "name": "gmail",
                "display_name": "Gmail",
                "description": "Gmail email source for organizational intelligence and personal productivity",
                "is_enabled": True,
                "is_default": False,
                "configuration_schema": None,
                "default_config": None,
                "requires_authentication": True,
                "supports_sync": True,
                "supports_webhooks": False,
                "supported_file_types": '["email"]',
                "max_file_size": "25MB",
                "supports_incremental_sync": True,
                "supports_deletion_detection": True,
                "supports_metadata_extraction": True,
                "created_at": "2025-09-06 01:16:22.882",
                "updated_at": "2025-09-06 01:16:22.882",
            },
        ]
    )

    # documents
    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("knowledge_base_id", sa.String(36), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.String(50), sa.ForeignKey("source_types.name"), nullable=False, index=True),
        sa.Column("source_id", sa.String(500), nullable=False, index=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("file_type", sa.String(50), nullable=False),
        sa.Column("file_size", sa.Integer, nullable=True),
        sa.Column("mime_type", sa.String(100), nullable=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True, index=True),
        sa.Column("source_hash", sa.String(64), nullable=True, index=True),
        sa.Column("processing_status", sa.String(50), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("processing_error", sa.Text, nullable=True),
        sa.Column("extraction_method", sa.String(50), nullable=True),
        sa.Column("extraction_engine", sa.String(50), nullable=True),
        sa.Column("extraction_confidence", sa.Float(), nullable=True),
        sa.Column("extraction_duration", sa.Float(), nullable=True),
        sa.Column("extraction_metadata", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("source_url", sa.String(1000), nullable=True),
        sa.Column("source_modified_at", sa.DateTime, nullable=True),
        sa.Column("source_metadata", sa.Text, nullable=True),
        sa.Column("processed_at", sa.DateTime, nullable=True),
        sa.Column("word_count", sa.Integer, nullable=True),
        sa.Column("character_count", sa.Integer, nullable=True),
        sa.Column("chunk_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_unique_constraint("uq_documents_kb_source_sourceid", "documents", ["knowledge_base_id", "source_type", "source_id"])

    # document_chunks
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("knowledge_base_id", sa.String(36), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column("char_count", sa.Integer, nullable=False),
        sa.Column("word_count", sa.Integer, nullable=True),
        sa.Column("token_count", sa.Integer, nullable=True),
        sa.Column("start_char", sa.Integer, nullable=True),
        sa.Column("end_char", sa.Integer, nullable=True),
        sa.Column("embedding_model", sa.String(100), nullable=True),
        sa.Column("embedding_created_at", sa.DateTime, nullable=True),
        sa.Column("chunk_metadata", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

    # sync_jobs
    op.create_table(
        "sync_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("knowledge_base_id", sa.String(36), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_type", sa.String(50), nullable=False, server_default=sa.text("'sync'")),
        sa.Column("source_type", sa.String(50), sa.ForeignKey("source_types.name"), nullable=True, index=True),
        sa.Column("status", sa.String(50), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("total_documents", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("processed_documents", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("documents_added", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("documents_updated", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("documents_deleted", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("documents_failed", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("error_details", sa.Text, nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("max_retries", sa.Integer, nullable=False, server_default=sa.text("3")),
        sa.Column("duration_seconds", sa.Float, nullable=True),
        sa.Column("documents_per_second", sa.Float, nullable=True),
        sa.Column("sync_config", sa.Text, nullable=True),
        sa.Column("current_document_id", sa.String(), nullable=True),
        sa.Column("current_document_title", sa.String(), nullable=True),
        sa.Column("current_document_method", sa.String(), nullable=True),
        sa.Column("current_document_progress", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column("current_document_pages", sa.Integer(), nullable=True),
        sa.Column("current_document_current_page", sa.Integer(), nullable=True),
        sa.Column("estimated_completion_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

    # knowledge_base_sources
    op.create_table(
        "knowledge_base_sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("knowledge_base_id", sa.String(36), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.String(50), sa.ForeignKey("source_types.name"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("config", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("sync_frequency", sa.String(50), nullable=True),
        sa.Column("last_sync_at", sa.DateTime, nullable=True),
        sa.Column("last_sync_status", sa.String(50), nullable=True),
        sa.Column("last_sync_error", sa.Text, nullable=True),
        sa.Column("ocr_mode", sa.String(20), nullable=False, server_default=sa.text("'auto'")),
        sa.Column("ocr_confidence_threshold", sa.Float, nullable=False, server_default=sa.text("0.8")),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

    # prompts
    op.create_table(
        "prompts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("is_system_default", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    # prompt_assignments
    op.create_table(
        "prompt_assignments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("prompt_id", sa.String(36), sa.ForeignKey("prompts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("assigned_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    # Seed system default prompts (LLM model personas and KB context styles)
    try:
        conn = op.get_bind()

        def _prompt_exists(name: str, entity_type: str) -> bool:
            res = conn.execute(
                text(
                    """
                    SELECT 1 FROM prompts
                    WHERE name = :name AND entity_type = :entity_type
                    LIMIT 1
                    """
                ),
                {"name": name, "entity_type": entity_type},
            )
            return res.first() is not None

        now = datetime.now(timezone.utc)
        defaults = [
            # LLM model personas
            {
                "name": "Helpful Assistant",
                "description": "A friendly, helpful AI assistant",
                "content": "You are a helpful, harmless, and honest AI assistant. Provide clear, accurate, and useful responses to user queries.",
                "entity_type": "llm_model",
            },
            {
                "name": "Technical Expert",
                "description": "An AI assistant specializing in technical topics",
                "content": "You are a technical expert AI assistant. Provide detailed, accurate technical information with examples and best practices when appropriate.",
                "entity_type": "llm_model",
            },
            # KB context styles (used by model configurations)
            # Note: These are system prompts - context from knowledge bases is automatically
            # appended as a separate section by the message context builder
            {
                "name": "Academic Research Assistant",
                "description": "Provides comprehensive answers with scholarly rigor and citations",
                "content": "You are an academic research assistant. When context from knowledge bases is provided, use it to give comprehensive answers with scholarly rigor. Include relevant citations from the provided sources and maintain academic standards in your response.",
                "entity_type": "llm_model",
            },
            {
                "name": "Technical Documentation Assistant",
                "description": "Provides precise technical answers with code examples",
                "content": "You are a technical documentation assistant. When context from knowledge bases is provided, use it to give precise and accurate answers. Include specific technical details and code examples where relevant. Reference the source documents when citing specific information.",
                "entity_type": "llm_model",
            },
            {
                "name": "General Knowledge Assistant",
                "description": "Provides helpful answers based on retrieved context",
                "content": "You are a helpful knowledge assistant. When context from knowledge bases is provided, use it to give accurate and informative answers. If the context doesn't contain relevant information, say so rather than speculating.",
                "entity_type": "llm_model",
            },
        ]

        inserted = 0
        for p in defaults:
            if not _prompt_exists(p["name"], p["entity_type"]):
                conn.execute(
                    text(
                        """
                        INSERT INTO prompts (id, name, description, content, entity_type,
                                             is_active, version, is_system_default, created_at, updated_at)
                        VALUES (:id, :name, :description, :content, :entity_type,
                                true, 1, true, :created_at, :updated_at)
                        """
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "name": p["name"],
                        "description": p["description"],
                        "content": p["content"],
                        "entity_type": p["entity_type"],
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                inserted += 1
        if inserted:
            print(f"  💬 Seeded {inserted} system default prompts")
    except Exception as e:
        # Seeding should not block schema creation in constrained environments
        print(f"  ⚠️  Prompt seeding skipped due to error: {e}")

    # users and preferences
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(50), nullable=False, server_default=sa.text("'regular_user'")),
        sa.Column("google_id", sa.String(255), unique=True, nullable=True, index=True),
        sa.Column("picture_url", sa.String(500), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.Column("last_login", sa.DateTime, nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("auth_method", sa.String(50), nullable=False, server_default=sa.text("'google'")),
    )
    op.create_table(
        "user_preferences",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True),
        sa.Column("enable_cross_session_memory_by_default", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("memory_depth", sa.Integer, nullable=False, server_default=sa.text("5")),
        sa.Column("memory_similarity_threshold", sa.Float, nullable=False, server_default=sa.text("0.6")),
        sa.Column("enable_streaming_by_default", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("theme", sa.String(20), nullable=False, server_default=sa.text("'light'")),
        sa.Column("language", sa.String(10), nullable=False, server_default=sa.text("'en'")),
        sa.Column("timezone", sa.String(50), nullable=False, server_default=sa.text("'UTC'")),
        sa.Column("advanced_settings", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

    op.create_table(
        "user_google_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False, index=True),
        sa.Column("access_token", sa.Text, nullable=False),
        sa.Column("refresh_token", sa.Text, nullable=False),
        sa.Column("token_uri", sa.String(255), nullable=False, server_default=sa.text("'https://oauth2.googleapis.com/token'")),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("client_secret", sa.Text, nullable=False),
        sa.Column("scopes", sa.JSON, nullable=False),
        sa.Column("service_type", sa.String(50), nullable=False, server_default=sa.text("'gmail'")),
        sa.Column("expires_at", sa.DateTime, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("last_used_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )

    # LLM provider/model/usage
    op.create_table(
        "llm_providers",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("provider_type", sa.String(50), nullable=False),
        sa.Column("api_endpoint", sa.Text(), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("organization_id", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("supports_streaming", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("supports_functions", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("supports_vision", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("rate_limit_rpm", sa.Integer(), nullable=False, server_default=sa.text("60")),
        sa.Column("rate_limit_tpm", sa.Integer(), nullable=False, server_default=sa.text("60000")),
        sa.Column("budget_limit_monthly", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("config", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "llm_models",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("provider_id", sa.String(), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column("model_type", sa.String(50), nullable=False, server_default=sa.text("'chat'")),
        sa.Column("context_window", sa.Integer(), nullable=False, server_default=sa.text("4000")),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False, server_default=sa.text("4000")),
        sa.Column("supports_streaming", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("supports_functions", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("supports_vision", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("cost_per_input_token", sa.Numeric(precision=12, scale=10), nullable=True),
        sa.Column("cost_per_output_token", sa.Numeric(precision=12, scale=10), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["llm_providers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "llm_usage",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("provider_id", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("request_type", sa.String(50), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("input_cost", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.Column("output_cost", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.Column("total_cost", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.Column("response_time_ms", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("request_metadata", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["llm_providers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_id"], ["llm_models.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    # conversations and messages (with 003 additions)
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(), nullable=False, primary_key=True),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("title", sa.String(200), nullable=True),
        # New unified configuration reference for the conversation
        sa.Column("model_configuration_id", sa.String(), nullable=True),
        # Legacy fields retained for compatibility with existing data
        sa.Column("provider_id", sa.String(), nullable=True),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.String(), nullable=False, primary_key=True),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("provider_id", sa.String(), nullable=True),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.Column("response_time_ms", sa.Integer(), nullable=True),
        sa.Column("message_metadata", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        # 003 additions
        sa.Column("parent_message_id", sa.String(), nullable=True),
        sa.Column("variant_index", sa.Integer(), nullable=True),
    )

    # model configurations
    op.create_table(
        "model_configurations",
        sa.Column("id", sa.String(), nullable=False, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("llm_provider_id", sa.String(), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("prompt_id", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    # Add FK for conversations.model_configuration_id -> model_configurations.id
    op.create_foreign_key(
        "fk_conversations_model_configuration_id",
        "conversations",
        "model_configurations",
        ["model_configuration_id"],
        ["id"],
        ondelete="SET NULL",
    )


    op.create_table(
        "model_configuration_knowledge_bases",
        sa.Column("model_configuration_id", sa.String(), nullable=False),
        sa.Column("knowledge_base_id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("model_configuration_id", "knowledge_base_id"),
    )

    op.create_table(
        "model_configuration_kb_prompts",
        sa.Column("id", sa.String(), nullable=False, primary_key=True),
        sa.Column("model_configuration_id", sa.String(), nullable=False),
        sa.Column("knowledge_base_id", sa.String(), nullable=False),
        sa.Column("prompt_id", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    # RBAC: user groups and memberships
    op.create_table(
        "user_groups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "user_group_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(50), nullable=False, server_default=sa.text("'member'")),
        sa.Column("granted_by", sa.String(36), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("user_id", "group_id", name="uq_user_group_membership"),
    )

    op.create_table(
        "knowledge_base_permissions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("knowledge_base_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=True),
        sa.Column("group_id", sa.String(36), nullable=True),
        sa.Column("permission_level", sa.String(50), nullable=False),
        sa.Column("granted_by", sa.String(36), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Attachments (002)
    op.create_table(
        "attachments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("storage_path", sa.String(1000), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("file_type", sa.String(20), nullable=False),
        sa.Column("file_size", sa.Integer, nullable=False),
        sa.Column("extracted_text", sa.Text, nullable=True),
        sa.Column("extracted_text_length", sa.Integer, nullable=True),
        sa.Column("extraction_method", sa.String(50), nullable=True),
        sa.Column("extraction_engine", sa.String(50), nullable=True),
        sa.Column("extraction_confidence", sa.Float(), nullable=True),
        sa.Column("extraction_duration", sa.Float(), nullable=True),
        sa.Column("extraction_metadata", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

    op.create_table(
        "message_attachments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attachment_id", sa.String(36), sa.ForeignKey("attachments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("message_id", "attachment_id", name="uq_message_attachment"),
    )

    # Minimal indexes (performance)
    op.create_index("idx_knowledge_bases_name", "knowledge_bases", ["name"])
    op.create_index("idx_documents_kb_id", "documents", ["knowledge_base_id"])
    op.create_index("idx_document_chunks_doc_id", "document_chunks", ["document_id"])
    try:
        op.create_index("idx_document_chunks_embedding", "document_chunks", ["embedding"], postgresql_using="ivfflat")
    except Exception:
        pass
    op.create_index("ix_messages_parent_message_id", "messages", ["parent_message_id"])
    op.create_index("ix_attachments_conversation_id", "attachments", ["conversation_id"])
    op.create_index("ix_attachments_user_id", "attachments", ["user_id"])
    # Index for conversations.model_configuration_id to support lookups
    op.create_index("ix_conversations_model_configuration_id", "conversations", ["model_configuration_id"])


def downgrade() -> None:
    # Drop in reverse dependency order
    for t in [
        "message_attachments",
        "attachments",
        "knowledge_base_permissions",
        "user_group_memberships",
        "user_groups",
        "model_configuration_kb_prompts",
        "model_configuration_knowledge_bases",
        "model_configurations",
        "messages",
        "conversations",
        "llm_usage",
        "llm_models",
        "llm_providers",
        "user_google_credentials",
        "user_preferences",
        "users",
        "prompt_assignments",
        "prompts",
        "knowledge_base_sources",
        "sync_jobs",
        "document_chunks",
        "documents",
        "source_types",
        "knowledge_bases",
    ]:
        try:
            op.drop_table(t)
        except Exception:
            pass

