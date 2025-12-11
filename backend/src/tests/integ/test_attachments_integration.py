"""
Integration tests for Chat Attachments (upload and usage in context).

Follows custom integration test framework. Negative tests log expected error output explicitly.
"""
import sys
import os
import logging
from typing import List, Callable

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.response_utils import extract_data
from integ.expected_error_context import expect_validation_errors, expect_authentication_errors

logger = logging.getLogger(__name__)

# Test fixtures borrowed from chat integration patterns
PROVIDER_DATA = {
    "name": "Test Provider for Attachments",
    "provider_type": "openai",
    "api_endpoint": "https://api.openai.com/v1",
    "api_key": "test-api-key-attachments",
    "is_active": True
}

MODEL_DATA = {
    "model_name": "gpt-4",
    "display_name": "GPT-4 Attachments",
    "description": "Model for attachments integration tests",
    "context_window": 8192,
    "max_tokens": 4096,
    "supports_streaming": True,
}

MODEL_CONFIG_DATA = {
    "name": "Test Attachments Assistant",
    "description": "Model configuration for attachments integration",
    "is_active": True,
    "created_by": "test-user",
    "knowledge_base_ids": []
}


async def _create_conversation(client, auth_headers) -> str:
    # Create provider
    provider_resp = await client.post("/api/v1/llm/providers", json=PROVIDER_DATA, headers=auth_headers)
    assert provider_resp.status_code == 201, provider_resp.text
    provider_id = extract_data(provider_resp)["id"]

    # Create model
    model_resp = await client.post(f"/api/v1/llm/providers/{provider_id}/models", json=MODEL_DATA, headers=auth_headers)
    assert model_resp.status_code == 200, model_resp.text

    # Create model configuration
    model_config = {
        **MODEL_CONFIG_DATA,
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"],
    }
    config_resp = await client.post("/api/v1/model-configurations", json=model_config, headers=auth_headers)
    assert config_resp.status_code == 201, config_resp.text
    model_config_id = extract_data(config_resp)["id"]

    # Create conversation
    conv_resp = await client.post(
        "/api/v1/chat/conversations",
        json={"title": "Attachments Test Conversation", "model_configuration_id": model_config_id},
        headers=auth_headers,
    )
    assert conv_resp.status_code == 200, conv_resp.text
    return extract_data(conv_resp)["id"]


async def test_upload_pdf_ocr_fields(client, db, auth_headers):
    """Upload known PDF asset and verify OCR metadata fields are present and propagated."""
    conversation_id = await _create_conversation(client, auth_headers)

    # Use repo asset: tools/ocr-tests/US20230413815A1.pdf
    asset_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_files/file3.pdf"))
    assert os.path.exists(asset_path), f"Missing test asset: {asset_path}"

    with open(asset_path, "rb") as f:
        file_bytes = f.read()
    files = {"file": (os.path.basename(asset_path), file_bytes, "application/pdf")}

    upload = await client.post(f"/api/v1/chat/conversations/{conversation_id}/attachments", files=files, headers=auth_headers)
    assert upload.status_code == 200, upload.text
    attachment = extract_data(upload)

    # Alpha policy: fast extraction only in chat uploads
    assert "attachment_id" in attachment
    assert isinstance(attachment.get("extracted_text_length"), int)
    assert attachment.get("is_ocr") in (False, None)

    # Link it to a user message and verify propagation in MessageResponse
    send_resp = await client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json={"role": "user", "content": "Please use my attachment", "attachment_ids": [attachment["attachment_id"]]},
        headers=auth_headers,
    )
    assert send_resp.status_code == 200, send_resp.text

    msgs = await client.get(f"/api/v1/chat/conversations/{conversation_id}/messages", headers=auth_headers)
    assert msgs.status_code == 200
    messages = extract_data(msgs)
    found = False
    for m in messages:
        if m.get("role") != "user":
            continue
        for att in m.get("attachments") or []:
            if att.get("id") == attachment["attachment_id"]:
                assert "extracted_text_length" in att
                assert att.get("is_ocr") in (False, None)
                found = True
                break
        if found:
            break
    assert found, "Uploaded attachment not found with OCR metadata in message attachments"


async def test_upload_txt_success(client, db, auth_headers):
    conversation_id = await _create_conversation(client, auth_headers)

    files = {"file": ("sample.txt", b"This is a small attachment for testing.", "text/plain")}
    resp = await client.post(f"/api/v1/chat/conversations/{conversation_id}/attachments", files=files, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = extract_data(resp)
    assert "attachment_id" in data
    assert data["mime_type"].startswith("text/") or data["mime_type"] == "application/octet-stream"
    assert data["file_size"] > 0

    # Clean up file created by this test (unlink and delete row)
    from sqlalchemy import select
    from shu.models.attachment import Attachment
    result = await db.execute(select(Attachment).where(Attachment.id == data["attachment_id"]))
    att = result.scalar_one_or_none()
    if att and att.storage_path:
        import os
        try:
            if os.path.exists(att.storage_path):
                os.remove(att.storage_path)
        except Exception:
            pass
        await db.delete(att)
        await db.commit()


async def test_upload_unsupported_type(client, db, auth_headers):
    conversation_id = await _create_conversation(client, auth_headers)

    logger.info("=== EXPECTED TEST OUTPUT: Unsupported type error is expected ===")
    files = {"file": ("image.jpg", b"fakejpegdata", "image/jpeg")}
    resp = await client.post(f"/api/v1/chat/conversations/{conversation_id}/attachments", files=files, headers=auth_headers)
    # Our API returns 400 for unsupported type
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert "error" in body or "detail" in body

    # Nothing created on disk in unsupported case


async def test_upload_oversized_file(client, db, auth_headers):
    conversation_id = await _create_conversation(client, auth_headers)

    logger.info("=== EXPECTED TEST OUTPUT: Oversized file error is expected ===")
    # Build a ~21MB payload to exceed default 20MB limit
    big_bytes = b"0" * (21 * 1024 * 1024)
    files = {"file": ("huge.txt", big_bytes, "text/plain")}
    resp = await client.post(f"/api/v1/chat/conversations/{conversation_id}/attachments", files=files, headers=auth_headers)
    assert resp.status_code in (400, 413), resp.text  # 413 preferred, allow 400 fallback

    # Nothing created on disk in oversized case


async def test_upload_unauthorized(client, db, auth_headers):
    conversation_id = await _create_conversation(client, auth_headers)

    logger.info("=== EXPECTED TEST OUTPUT: Unauthorized upload error is expected ===")
    files = {"file": ("sample.txt", b"hello", "text/plain")}
    resp = await client.post(f"/api/v1/chat/conversations/{conversation_id}/attachments", files=files)  # no auth headers
    assert resp.status_code in (401, 403), resp.text


async def test_send_with_attachment_ids_acceptance(client, db, auth_headers):
    conversation_id = await _create_conversation(client, auth_headers)
    # Upload
    files = {"file": ("context.md", b"# Header\nSome content for context injection.", "text/markdown")}
    up = await client.post(f"/api/v1/chat/conversations/{conversation_id}/attachments", files=files, headers=auth_headers)
    assert up.status_code == 200, up.text
    attachment_id = extract_data(up)["attachment_id"]

    # Send with attachment_ids
    payload = {
        "message": "Use my attachment to answer.",
        "rag_rewrite_mode": "no_rag",
        "attachment_ids": [attachment_id],
    }
    # LLM may error with test key; acceptance means no validation/404
    resp = await client.post(f"/api/v1/chat/conversations/{conversation_id}/send", json=payload, headers=auth_headers)
    try:
        assert resp.status_code != 404, resp.text
    finally:
        try:
            if hasattr(resp, "aclose"):
                await resp.aclose()
            else:
                resp.close()
        except Exception:
            pass

    # Verify linkage exists in DB for the most recent user message
    # Note: assistant message is returned; we link attachments to the user message created in send flow
    from sqlalchemy import text
    result = await db.execute(
        text(
            """
            SELECT a.storage_path, ma.attachment_id
            FROM message_attachments ma
            JOIN messages m ON m.id = ma.message_id
            JOIN attachments a ON a.id = ma.attachment_id
            WHERE m.conversation_id = :cid AND m.role = 'user'
            ORDER BY m.created_at DESC
            LIMIT 1
            """
        ),
        {"cid": conversation_id},
    )
    row = result.fetchone()
    assert row is not None and row[1] == attachment_id, f"message attachment record: {row}"

    # Clean up the file and the row explicitly
    storage_path = row[0]
    if storage_path:
        import os
        try:
            if os.path.exists(storage_path):
                os.remove(storage_path)
        except Exception:
            pass
    # Delete the attachment row
    from sqlalchemy import delete
    from shu.models.attachment import Attachment
    await db.execute(delete(Attachment).where(Attachment.id == attachment_id))
    await db.commit()


class AttachmentsIntegrationTestSuite(BaseIntegrationTestSuite):
    def get_suite_name(self) -> str:
        return "Chat Attachments Integration"

    def get_suite_description(self) -> str:
        return "Integration tests for chat attachments: upload and usage in send flow."

    def get_test_functions(self) -> List[Callable]:
        return [
            test_upload_pdf_ocr_fields,
            test_upload_txt_success,
            test_upload_unsupported_type,
            test_upload_oversized_file,
            test_upload_unauthorized,
            test_send_with_attachment_ids_acceptance,
        ]


if __name__ == "__main__":
    # Allow running this suite directly
    suite = AttachmentsIntegrationTestSuite()
    import asyncio
    exit_code = asyncio.run(suite.run_suite())
    raise SystemExit(exit_code)
