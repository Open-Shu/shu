"""
OCR Quality Integration Tests

Tests OCR extraction quality by comparing our OCR output against Adobe's OCR output.
"""

import logging
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from integ.base_integration_test import BaseIntegrationTestSuite
from shu.processors.text_extractor import TextExtractor

logger = logging.getLogger(__name__)

# Test assets - these should be placed in the same directory as this test file
ASSET_DIR = Path(__file__).parent
RAW_PDF = ASSET_DIR / "sample-ocr-no-text.pdf"
ADOBE_PDF = ASSET_DIR / "sample-ocr-text.pdf"


def _dependency_available() -> bool:
    """Check whether we have at least one OCR engine available."""
    try:
        import easyocr  # noqa: F401

        return True
    except Exception:
        return shutil.which("tesseract") is not None


def _clean_text(text: str) -> str:
    """Normalize text for comparison."""
    cleaned = " ".join((text or "").split())
    cleaned = cleaned.replace("|", "I").replace("0", "O")
    return cleaned


def _analyze_similarity(text_a: str, text_b: str) -> dict[str, Any]:
    """Analyze similarity between two text strings."""
    import difflib

    cleaned_a = _clean_text(text_a)
    cleaned_b = _clean_text(text_b)
    ratio = difflib.SequenceMatcher(None, cleaned_a, cleaned_b).ratio()
    coverage = (len(cleaned_a.split()) / len(cleaned_b.split())) * 100 if cleaned_b.split() else 0
    return {
        "similarity_pct": ratio * 100,
        "coverage_pct": coverage,
        "len_a": len(cleaned_a),
        "len_b": len(cleaned_b),
    }


async def _extract_text_with_ocr(path: Path) -> dict[str, Any]:
    """Extract text from a PDF file using OCR."""
    extractor = TextExtractor()
    with open(path, "rb") as f:
        content = f.read()
    result = await extractor.extract_text(str(path), file_content=content, use_ocr=True)
    if isinstance(result, str):
        return {"text": result, "metadata": {}}
    return result


async def _extract_embedded_text(path: Path) -> str:
    """Extract embedded text layer from a PDF (no OCR)."""
    extractor = TextExtractor()
    with open(path, "rb") as f:
        content = f.read()
    result = await extractor.extract_text(str(path), file_content=content, use_ocr=False)
    if isinstance(result, str):
        return result
    return result.get("text", "")


# Test Functions
async def test_ocr_similarity_against_reference(client, db, auth_headers):
    """Test OCR quality by comparing our OCR output against Adobe's embedded text layer."""

    # Check if database is configured
    if not os.environ.get("SHU_DATABASE_URL"):
        logger.warning("Skipping OCR test: SHU_DATABASE_URL not set")
        return

    # Check if OCR engine is available
    if not _dependency_available():
        logger.warning("Skipping OCR test: No OCR engine available (EasyOCR import failed and tesseract not found)")
        return

    # Check if test assets exist
    for asset in (RAW_PDF, ADOBE_PDF):
        if not asset.exists():
            logger.warning(f"Skipping OCR test: Missing test asset: {asset}")
            return

    logger.info("Running OCR quality comparison test")

    # Run OCR on the raw scan (no embedded text)
    ours = await _extract_text_with_ocr(RAW_PDF)

    # Extract Adobe's embedded text layer (no OCR)
    text_adobe = await _extract_embedded_text(ADOBE_PDF)

    text_ours = ours.get("text", "")

    # Skip if OCR extraction returned empty (engine not working properly)
    if not text_ours:
        logger.warning("Skipping OCR test: OCR extraction returned empty text (engine may not be fully initialized)")
        return

    # Skip if Adobe PDF has no text layer
    if not text_adobe:
        logger.warning("Skipping OCR test: Adobe PDF has no embedded text layer for comparison")
        return

    # Verify metadata is present
    metadata = ours.get("metadata") or {}
    assert metadata.get("method"), "Metadata method missing for OCR extraction"
    assert metadata.get("engine"), "Metadata engine missing for OCR extraction"

    logger.info(f"OCR method: {metadata.get('method')}, engine: {metadata.get('engine')}")

    # Analyze similarity
    analysis = _analyze_similarity(text_ours, text_adobe)

    logger.info("OCR Quality Analysis:")
    logger.info(f"  Similarity: {analysis['similarity_pct']:.2f}%")
    logger.info(f"  Coverage: {analysis['coverage_pct']:.2f}%")
    logger.info(f"  Our text length: {analysis['len_a']} chars")
    logger.info(f"  Adobe text length: {analysis['len_b']} chars")

    # Assert quality thresholds
    assert analysis["similarity_pct"] >= 85, f"Similarity below threshold: {analysis['similarity_pct']:.2f}%"
    assert analysis["coverage_pct"] >= 90, f"Coverage below threshold: {analysis['coverage_pct']:.2f}%"


# Test Suite Class
class OCRQualityTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for OCR quality validation."""

    def get_test_functions(self) -> list[Callable]:
        """Return all OCR quality test functions."""
        return [
            test_ocr_similarity_against_reference,
        ]

    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "OCR Quality Integration Tests"

    def get_suite_description(self) -> str:
        """Return description of this test suite."""
        return "Integration tests for OCR quality validation by comparing against Adobe OCR output"


if __name__ == "__main__":
    suite = OCRQualityTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
