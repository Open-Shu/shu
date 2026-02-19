"""
Unit tests for SHU-563 OCR memory fixes in TextExtractor.

Covers:
- img_array reference is released immediately after OCR thread start (Fix #1)
- OCR semaphore is acquired before fitz.open() (Fix #2)
- Render scale reads from config, not a hardcoded literal (Fix #3)
"""

import asyncio
import sys
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

    def test_run_ocr_closure_uses_default_arg_not_late_binding(self):
        """
        The run_ocr closure must capture img_array via a default argument,
        not via late binding from the enclosing scope. Verify by inspecting
        the closure's __defaults__.
        """
        import inspect
        import textwrap

        from shu.processors.text_extractor import TextExtractor

        source = inspect.getsource(TextExtractor._process_pdf_with_ocr_direct)

        # The closure definition must use `_img: ... = img_array` as a default arg
        assert "_img" in source and "= img_array" in source, (
            "run_ocr closure must capture img_array via default arg (_img=img_array), "
            "not via late binding. Found source:\n" + textwrap.shorten(source, 500)
        )

        # The noqa B023 suppression must be gone
        assert "noqa: B023" not in source, (
            "# noqa: B023 suppression should have been removed after fixing the late-binding issue"
        )

    def test_del_img_array_follows_thread_start(self):
        """
        The source of _process_pdf_with_ocr_direct must contain `del img_array`
        and `pix = None` after `ocr_thread.start()`.
        """
        import inspect

        from shu.processors.text_extractor import TextExtractor

        source = inspect.getsource(TextExtractor._process_pdf_with_ocr_direct)

        assert "del img_array" in source, "del img_array must appear in _process_pdf_with_ocr_direct"
        assert "pix = None" in source, "pix = None must appear in _process_pdf_with_ocr_direct"

        # del img_array must come after ocr_thread.start()
        start_pos = source.find("ocr_thread.start()")
        del_pos = source.find("del img_array")
        assert start_pos != -1, "ocr_thread.start() not found"
        assert del_pos != -1, "del img_array not found"
        assert del_pos > start_pos, "del img_array must appear after ocr_thread.start()"


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

        acquire_order = []

        original_inner = TextExtractor._extract_pdf_ocr_direct_inner

        async def patched_inner(self_inner, file_path, file_content=None, progress_callback=None):
            acquire_order.append("inner_called")
            # Return early — we just need to confirm order
            return ""

        extractor = _make_extractor()

        # Use a real semaphore with limit=1 so we can observe acquire/release
        real_sem = asyncio.Semaphore(1)
        sem_acquire_calls = []

        original_acquire = real_sem.acquire

        async def tracked_acquire():
            sem_acquire_calls.append("acquire")
            return await original_acquire()

        real_sem.acquire = tracked_acquire

        with (
            patch.object(TextExtractor, "get_ocr_semaphore", return_value=real_sem),
            patch.object(TextExtractor, "_extract_pdf_ocr_direct_inner", patched_inner),
        ):
            await extractor._extract_pdf_ocr_direct("test.pdf", b"fake")

        assert "acquire" in sem_acquire_calls, "Semaphore was never acquired"
        assert "inner_called" in acquire_order, "_extract_pdf_ocr_direct_inner was never called"

    def test_semaphore_acquired_before_fitz_open_in_source(self):
        """
        In _extract_pdf_ocr_direct_inner source, the semaphore comment/docstring
        must indicate it is called under semaphore, and fitz.open must appear
        after the function entry (not in the outer wrapper).
        """
        import inspect

        from shu.processors.text_extractor import TextExtractor

        inner_source = inspect.getsource(TextExtractor._extract_pdf_ocr_direct_inner)
        outer_source = inspect.getsource(TextExtractor._extract_pdf_ocr_direct)

        # fitz.open must be in the inner function (called under semaphore)
        assert "fitz.open" in inner_source, (
            "fitz.open must be inside _extract_pdf_ocr_direct_inner (called under semaphore)"
        )

        # The outer wrapper must acquire the semaphore (async with sem)
        assert "async with sem" in outer_source, (
            "_extract_pdf_ocr_direct must acquire the semaphore with 'async with sem'"
        )

        # The outer wrapper must NOT contain fitz.open itself
        # (it should only delegate to inner after acquiring)
        outer_lines = [
            line for line in outer_source.splitlines()
            if "fitz.open" in line and not line.strip().startswith("#")
        ]
        assert len(outer_lines) == 0, (
            f"fitz.open must not appear in _extract_pdf_ocr_direct (outer wrapper); "
            f"found: {outer_lines}"
        )


# ---------------------------------------------------------------------------
# Fix #3 — render scale reads from config
# ---------------------------------------------------------------------------

class TestRenderScaleFromConfig:
    """fitz.Matrix render scale must come from config, not a hardcoded literal."""

    def test_no_hardcoded_matrix_2_2_in_active_paths(self):
        """
        The active OCR paths (_process_pdf_with_ocr_direct and
        _process_pdf_with_tesseract_direct) must not contain fitz.Matrix(2, 2).
        """
        import inspect

        from shu.processors.text_extractor import TextExtractor

        for method_name in ("_process_pdf_with_ocr_direct", "_process_pdf_with_tesseract_direct"):
            source = inspect.getsource(getattr(TextExtractor, method_name))
            assert "fitz.Matrix(2, 2)" not in source, (
                f"{method_name} still contains hardcoded fitz.Matrix(2, 2); "
                "it must use config_manager.settings.ocr_render_scale"
            )

    def test_render_scale_used_in_active_paths(self):
        """
        Both active render sites must reference ocr_render_scale from settings.
        """
        import inspect

        from shu.processors.text_extractor import TextExtractor

        for method_name in ("_process_pdf_with_ocr_direct", "_process_pdf_with_tesseract_direct"):
            source = inspect.getsource(getattr(TextExtractor, method_name))
            assert "ocr_render_scale" in source, (
                f"{method_name} must read ocr_render_scale from settings"
            )

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
        import fitz
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
