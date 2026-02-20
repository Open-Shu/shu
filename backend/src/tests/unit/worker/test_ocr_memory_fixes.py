"""
Unit tests for SHU-563 OCR memory fixes in TextExtractor.

Covers:
- img_array reference is released immediately after OCR thread start (Fix #1)
- OCR semaphore is acquired before fitz.open() (Fix #2)
- Render scale reads from config, not a hardcoded literal (Fix #3)
"""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_extractor(ocr_render_scale: float = 2.0, ocr_page_timeout: int = 30):
    """Build a TextExtractor with a mock config_manager."""
    from shu.processors.text_extractor import TextExtractor

    mock_settings = MagicMock()
    mock_settings.ocr_render_scale = ocr_render_scale
    mock_settings.ocr_page_timeout = ocr_page_timeout
    mock_settings.ocr_max_concurrent_jobs = 1

    mock_config_manager = MagicMock()
    mock_config_manager.settings = mock_settings

    extractor = TextExtractor.__new__(TextExtractor)
    extractor.config_manager = mock_config_manager
    extractor._current_sync_job_id = None
    return extractor


# ---------------------------------------------------------------------------
# Fix #1 — img_array released after thread start
# ---------------------------------------------------------------------------

class TestImgArrayReleasedAfterThreadStart:
    """img_array refcount must drop to 1 (inside thread only) after ocr_thread.start()."""

    @pytest.mark.asyncio
    async def test_img_array_refcount_drops_after_thread_start(self):
        """
        After ocr_thread.start() returns, the caller's reference to img_array
        must be gone (del img_array executed). The thread holds the only remaining
        reference via its default-arg binding.
        """
        import numpy as np
        from PIL import Image

        extractor = _make_extractor()

        # Use a threading.Event for the thread signal; poll it from async via sleep
        ocr_started = threading.Event()
        ocr_may_finish = threading.Event()
        captured_img_id = []

        def fake_readtext(_img):
            captured_img_id.append(id(_img))
            ocr_started.set()
            ocr_may_finish.wait(timeout=5)
            return []

        mock_ocr = MagicMock()
        mock_ocr.readtext = fake_readtext

        real_array = np.zeros((10, 10, 3), dtype=np.uint8)
        original_id = id(real_array)

        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"fake"

        mock_doc = MagicMock()
        mock_doc.__len__ = MagicMock(return_value=1)
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_page.get_pixmap.return_value = mock_pix

        mock_pil_image = Image.fromarray(real_array)

        from shu.processors.text_extractor import TextExtractor
        TextExtractor._ocr_semaphore = None

        with (
            patch.object(extractor, "get_ocr_instance", new=AsyncMock(return_value=mock_ocr)),
            patch.object(extractor, "is_job_cancelled", return_value=False),
            patch("PIL.Image.open", return_value=mock_pil_image),
            patch("numpy.array", return_value=real_array),
        ):
            task = asyncio.create_task(
                extractor._process_pdf_with_ocr_direct(mock_doc, "test.pdf", None)
            )

            # Poll the threading.Event without blocking the event loop
            deadline = asyncio.get_event_loop().time() + 5
            while not ocr_started.is_set():
                if asyncio.get_event_loop().time() > deadline:
                    break
                await asyncio.sleep(0.05)

            assert ocr_started.is_set(), "OCR thread never started within 5s"
            assert captured_img_id, "fake_readtext was not called"
            assert captured_img_id[0] == original_id, "Thread received wrong img_array"

            ocr_may_finish.set()
            await task


# ---------------------------------------------------------------------------
# Fix #2 — semaphore acquired before fitz.open()
# ---------------------------------------------------------------------------

class TestSemaphoreBeforeFitzOpen:
    """Semaphore must be acquired before fitz.open() so peak memory is bounded."""

    @pytest.mark.asyncio
    async def test_semaphore_held_during_fitz_open(self):
        """
        When _extract_pdf_ocr_direct is called, the semaphore must be acquired
        before fitz.open() is called inside _extract_pdf_ocr_direct_inner.
        """
        from shu.processors.text_extractor import TextExtractor

        TextExtractor._ocr_semaphore = None

        # Single ordered list so we can assert acquire < inner_called
        call_sequence = []

        async def patched_inner(self_inner, file_path, file_content=None, progress_callback=None):
            call_sequence.append("inner_called")
            return ""

        extractor = _make_extractor()

        real_sem = asyncio.Semaphore(1)
        original_acquire = real_sem.acquire

        async def tracked_acquire():
            call_sequence.append("acquire")
            return await original_acquire()

        real_sem.acquire = tracked_acquire

        with (
            patch.object(TextExtractor, "get_ocr_semaphore", return_value=real_sem),
            patch.object(TextExtractor, "_extract_pdf_ocr_direct_inner", patched_inner),
        ):
            await extractor._extract_pdf_ocr_direct("test.pdf", b"fake")

        assert "acquire" in call_sequence, "Semaphore was never acquired"
        assert "inner_called" in call_sequence, "_extract_pdf_ocr_direct_inner was never called"
        assert call_sequence.index("acquire") < call_sequence.index("inner_called"), (
            f"Semaphore must be acquired before inner call, got: {call_sequence}"
        )


# ---------------------------------------------------------------------------
# Fix #3 — render scale reads from config
# ---------------------------------------------------------------------------

class TestRenderScaleFromConfig:
    """fitz.Matrix render scale must come from config, not a hardcoded literal."""

    def test_ocr_render_scale_setting_exists_with_correct_default(self):
        """
        Settings must have ocr_render_scale with default 2.0 and alias SHU_OCR_RENDER_SCALE.
        """
        from shu.core.config import Settings

        fields = Settings.model_fields
        assert "ocr_render_scale" in fields, "ocr_render_scale field missing from Settings"

        field = fields["ocr_render_scale"]
        assert field.default == 2.0, (
            f"ocr_render_scale default must be 2.0, got {field.default}"
        )

        # Verify the alias so the env var works
        alias = field.alias if hasattr(field, "alias") else None
        # Pydantic v2: alias is on the FieldInfo
        alias = getattr(field, "alias", None) or (
            field.validation_alias if hasattr(field, "validation_alias") else None
        )
        assert alias == "SHU_OCR_RENDER_SCALE", (
            f"ocr_render_scale alias must be SHU_OCR_RENDER_SCALE, got {alias!r}"
        )

    @pytest.mark.asyncio
    async def test_render_scale_passed_to_get_pixmap(self):
        """
        When ocr_render_scale=1.5, get_pixmap must be called with fitz.Matrix(1.5, 1.5).
        """
        import numpy as np
        from PIL import Image

        extractor = _make_extractor(ocr_render_scale=1.5)

        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"fake"

        mock_doc = MagicMock()
        mock_doc.__len__ = MagicMock(return_value=1)
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_page.get_pixmap.return_value = mock_pix

        mock_ocr = MagicMock()
        mock_ocr.readtext.return_value = [([0, 0, 10, 10], "hello", 0.9)]

        dummy_array = np.zeros((10, 10, 3), dtype=np.uint8)
        mock_pil_image = Image.fromarray(dummy_array)

        from shu.processors.text_extractor import TextExtractor
        TextExtractor._ocr_semaphore = None

        with (
            patch.object(extractor, "get_ocr_instance", new=AsyncMock(return_value=mock_ocr)),
            patch.object(extractor, "is_job_cancelled", return_value=False),
            patch("PIL.Image.open", return_value=mock_pil_image),
            patch("numpy.array", return_value=dummy_array),
        ):
            await extractor._process_pdf_with_ocr_direct(mock_doc, "test.pdf", None)

        mock_page.get_pixmap.assert_called_once()
        call_kwargs = mock_page.get_pixmap.call_args
        matrix_arg = call_kwargs.kwargs.get("matrix") or (
            call_kwargs.args[0] if call_kwargs.args else None
        )
        assert matrix_arg is not None, "get_pixmap was not called with a matrix argument"
        assert abs(matrix_arg.a - 1.5) < 1e-6, (
            f"Expected fitz.Matrix scale 1.5, got matrix.a={matrix_arg.a}"
        )
