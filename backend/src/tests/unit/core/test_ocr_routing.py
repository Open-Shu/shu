"""Unit tests for the per-page real-text classifier (SHU-728)."""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from shu.core.ocr_routing import (
    PageSignals,
    RoutingThresholds,
    classify_pdf,
    page_has_real_text,
)

CORPUS_DIR = Path(__file__).resolve().parents[3] / ".." / ".." / "docs" / ".pdf-corpus"
CORPUS_DIR = CORPUS_DIR.resolve()


# ---------------------------------------------------------------------------
# In-memory PDFs constructed via fitz — no fixtures on disk needed for the
# unit-level invariants. Each helper produces the smallest PDF that exercises
# one branch of the classifier.
# ---------------------------------------------------------------------------


def _pdf_with_centered_text() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)  # US Letter
    page.insert_text((250, 400), "real body text here")
    return doc.tobytes()


def _pdf_blank() -> bytes:
    doc = fitz.open()
    doc.new_page(width=612, height=792)  # zero text, no images
    return doc.tobytes()


def _pdf_with_only_header_text() -> bytes:
    """Text positioned in the top 12.5% — should be filtered by the margin rule."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    # 792 * 0.125 = 99 — anything above ~99 is in the header strip.
    page.insert_text((50, 30), "page 1 of 12 — header chrome only")
    return doc.tobytes()


def _fake_page(blocks: list[tuple[float, float, float, float, str]], width: float = 612.0, height: float = 792.0):
    """Construct a Mock page object with a controlled `get_text("blocks")` return.

    Synthesizing a real PDF with a broken-cmap text layer via fitz isn't
    possible from the high-level API — fitz's `insert_text` won't emit a glyph
    that decodes to `�`. Mocking the block list is how we unit-test the
    `has_corrupt_text` exclusion in isolation. The classifier only depends on
    `page.rect` and `page.get_text("blocks")`, so a simple mock suffices.
    """
    from unittest.mock import MagicMock

    mock = MagicMock()
    mock.rect = fitz.Rect(0, 0, width, height)
    block_tuples = [(x0, y0, x1, y1, text, 0, 0) for (x0, y0, x1, y1, text) in blocks]
    mock.get_text.return_value = block_tuples
    return mock


@pytest.fixture
def thresholds() -> RoutingThresholds:
    return RoutingThresholds(page_margin_ratio=0.125, text_page_fraction=0.5)


# ---------------------------------------------------------------------------
# page_has_real_text
# ---------------------------------------------------------------------------


class TestPageHasRealText:
    def test_centered_text_passes(self, thresholds):
        with fitz.open(stream=_pdf_with_centered_text(), filetype="pdf") as doc:
            sig = page_has_real_text(doc.load_page(0), thresholds.page_margin_ratio)
        assert sig.has_real_text is True
        assert sig.interior_text_blocks >= 1
        assert sig.has_corrupt_text is False

    def test_blank_page_fails(self, thresholds):
        with fitz.open(stream=_pdf_blank(), filetype="pdf") as doc:
            sig = page_has_real_text(doc.load_page(0), thresholds.page_margin_ratio)
        assert sig == PageSignals(interior_text_blocks=0, has_corrupt_text=False, has_real_text=False)

    def test_header_only_text_fails(self, thresholds):
        """Text in the outer 12.5% margin must not count as real text."""
        with fitz.open(stream=_pdf_with_only_header_text(), filetype="pdf") as doc:
            sig = page_has_real_text(doc.load_page(0), thresholds.page_margin_ratio)
        assert sig.has_real_text is False
        assert sig.interior_text_blocks == 0

    def test_corrupt_text_is_excluded(self, thresholds):
        # 612x792 page, block in the page interior, but text contains `�`.
        page = _fake_page(
            blocks=[(100.0, 100.0, 500.0, 200.0, "broken ��� cmap text")],
        )
        sig = page_has_real_text(page, thresholds.page_margin_ratio)
        assert sig.has_corrupt_text is True
        assert sig.has_real_text is False
        assert sig.interior_text_blocks == 0

    def test_corrupt_text_with_clean_neighbour_still_passes(self, thresholds):
        """If at least one block has clean interior text, the page passes —
        a corrupt block alone shouldn't disqualify a page that has other
        legitimate text."""
        page = _fake_page(
            blocks=[
                (100.0, 100.0, 500.0, 200.0, "broken ��� cmap text"),
                (100.0, 300.0, 500.0, 400.0, "clean readable text"),
            ],
        )
        sig = page_has_real_text(page, thresholds.page_margin_ratio)
        assert sig.has_corrupt_text is True
        assert sig.has_real_text is True
        assert sig.interior_text_blocks == 1

    def test_zero_margin_passes_header_text(self):
        """With margin=0, header chrome counts. Useful for sweeps; not for prod."""
        with fitz.open(stream=_pdf_with_only_header_text(), filetype="pdf") as doc:
            sig = page_has_real_text(doc.load_page(0), margin_ratio=0.0)
        assert sig.has_real_text is True


# ---------------------------------------------------------------------------
# classify_pdf — whole-document aggregation
# ---------------------------------------------------------------------------


class TestClassifyPdf:
    def test_empty_document(self, thresholds):
        with fitz.open() as doc:  # no pages
            decision = classify_pdf(doc, thresholds)
        assert decision.use_ocr is False
        assert decision.page_count == 0
        assert decision.real_text_fraction == 0.0
        assert "empty" in decision.reason

    def test_all_pages_text_routes_to_text(self, thresholds):
        with fitz.open(stream=_pdf_with_centered_text(), filetype="pdf") as doc:
            decision = classify_pdf(doc, thresholds)
        assert decision.use_ocr is False
        assert decision.real_text_fraction == 1.0

    def test_all_pages_blank_routes_to_ocr(self, thresholds):
        with fitz.open(stream=_pdf_blank(), filetype="pdf") as doc:
            decision = classify_pdf(doc, thresholds)
        assert decision.use_ocr is True
        assert decision.real_text_fraction == 0.0

    def test_mixed_doc_uses_fraction_aggregation(self, thresholds):
        """A 2-page doc with one text page + one blank page lands at fraction=0.5.

        The rule is `use_ocr if fraction < threshold` — at threshold=0.5, exactly
        0.5 routes to text (not OCR). This is the locked tie-breaker behaviour
        documented in the calibration report.
        """
        doc = fitz.open()
        page1 = doc.new_page(width=612, height=792)
        page1.insert_text((250, 400), "real text on page one")
        doc.new_page(width=612, height=792)  # blank page two

        decision = classify_pdf(doc, thresholds)
        try:
            assert decision.real_text_fraction == 0.5
            assert decision.use_ocr is False  # 0.5 < 0.5 is False → text
        finally:
            doc.close()

    def test_below_threshold_routes_to_ocr(self, thresholds):
        """Three pages, one with text → fraction ~0.33 < 0.5 → OCR."""
        doc = fitz.open()
        page1 = doc.new_page(width=612, height=792)
        page1.insert_text((250, 400), "real text")
        doc.new_page(width=612, height=792)
        doc.new_page(width=612, height=792)

        decision = classify_pdf(doc, thresholds)
        try:
            assert decision.use_ocr is True
            assert decision.real_text_fraction < thresholds.text_page_fraction
        finally:
            doc.close()


class TestClassifyPdfEarlyExit:
    """Performance optimization: classify_pdf stops scanning once the decision is locked."""

    def test_text_decision_locked_early_skips_remaining_pages(self, thresholds):
        """A 10-page doc where the first 5 pages all have real text locks at
        page 5 (fraction = 5/10 = 0.5 >= threshold). The remaining 5 are not
        scanned — `pages` carries only the visited subset."""
        doc = fitz.open()
        for _ in range(5):
            page = doc.new_page(width=612, height=792)
            page.insert_text((250, 400), "real body text")
        for _ in range(5):
            doc.new_page(width=612, height=792)  # blank, never scanned

        try:
            decision = classify_pdf(doc, thresholds)
            assert decision.use_ocr is False
            assert decision.page_count == 10  # total reflects the doc, not the scan
            assert len(decision.pages) == 5  # only visited pages contribute signals
            assert "early exit after 5 of 10 pages" in decision.reason
        finally:
            doc.close()

    def test_ocr_decision_locked_early_skips_remaining_pages(self, thresholds):
        """A 10-page doc where the first 6 pages are all blank means even if
        the remaining 4 had real text, fraction would be 4/10 = 0.4 < 0.5.
        Locks OCR at page 6 — pages 7-10 never load."""
        doc = fitz.open()
        for _ in range(6):
            doc.new_page(width=612, height=792)  # blank
        for _ in range(4):
            page = doc.new_page(width=612, height=792)
            page.insert_text((250, 400), "real text — never scanned")

        try:
            decision = classify_pdf(doc, thresholds)
            assert decision.use_ocr is True
            assert decision.page_count == 10
            assert len(decision.pages) == 6
            assert "early exit after 6 of 10 pages" in decision.reason
        finally:
            doc.close()

    def test_early_exit_at_text_lock_threshold(self, thresholds):
        """A doc where every odd page has text and every even page is blank
        locks TEXT exactly when scanned == needed (real_text_pages=2 >=
        needed=2.0 at page 3). Page 4 is not visited and the reason string
        records the early exit."""
        doc = fitz.open()
        # 4 pages: text, blank, text, blank → fraction = 0.5 → TEXT (locked at page 3)
        # but locked exactly when scanned == needed, not earlier.
        page1 = doc.new_page(width=612, height=792)
        page1.insert_text((250, 400), "text")
        doc.new_page(width=612, height=792)
        page3 = doc.new_page(width=612, height=792)
        page3.insert_text((250, 400), "text")
        doc.new_page(width=612, height=792)

        try:
            decision = classify_pdf(doc, thresholds)
            assert decision.use_ocr is False
            # Locks at page 3 (real_text_pages=2 >= needed=2.0); page 4 not visited.
            assert len(decision.pages) == 3
            assert "early exit" in decision.reason
        finally:
            doc.close()


# ---------------------------------------------------------------------------
# Regression corpus — ground truth from `scripts/ocr_routing_labels.json`.
#
# These tests are the production-correctness check: they ensure the calibrated
# defaults still produce the right decision on every PDF in the corpus. If a
# new corpus PDF lands in `docs/.pdf-corpus/` with a label, this test
# automatically picks it up.
# ---------------------------------------------------------------------------


def _load_corpus_labels() -> list[tuple[Path, bool, str]]:
    import json

    labels_path = Path(__file__).resolve().parents[3] / ".." / ".." / "scripts" / "ocr_routing_labels.json"
    labels_path = labels_path.resolve()
    if not labels_path.exists():
        return []
    with open(labels_path) as f:
        labels = json.load(f)
    out = []
    for name, meta in labels.items():
        if name.startswith("_"):
            continue
        path = CORPUS_DIR / name
        if not path.exists():
            continue
        out.append((path, bool(meta["should_ocr"]), str(meta.get("reason", ""))))
    return out


_CORPUS = _load_corpus_labels()


@pytest.mark.skipif(not _CORPUS, reason="No PDF corpus available")
@pytest.mark.parametrize(("pdf_path", "should_ocr", "reason"), _CORPUS, ids=lambda x: getattr(x, "name", str(x)))
def test_corpus_classifier_matches_ground_truth(pdf_path: Path, should_ocr: bool, reason: str):
    """Every labeled PDF in `docs/.pdf-corpus/` must route to its ground-truth decision."""
    thresholds = RoutingThresholds.from_settings()
    with fitz.open(pdf_path) as doc:
        decision = classify_pdf(doc, thresholds)
    assert decision.use_ocr is should_ocr, (
        f"{pdf_path.name}: expected use_ocr={should_ocr} ({reason}); "
        f"got use_ocr={decision.use_ocr}, fraction={decision.real_text_fraction:.3f}"
    )


# ---------------------------------------------------------------------------
# SHU-739 fix #3: stratified sampling with ambiguous-band fallback
# ---------------------------------------------------------------------------


class TestStratifiedSampleIndices:
    """Unit tests for the helper that picks which pages to scan in sample mode."""

    def test_sample_smaller_than_doc_picks_distinct_pages(self):
        from shu.core.ocr_routing import _stratified_sample_indices

        indices = _stratified_sample_indices(page_count=482, sample_size=10)
        assert len(indices) == len(set(indices)) == 10
        assert indices == sorted(indices)
        # Head and tail must be hit
        assert 0 in indices
        assert 481 in indices

    def test_sample_at_least_as_big_as_doc_returns_all_pages(self):
        from shu.core.ocr_routing import _stratified_sample_indices

        assert _stratified_sample_indices(page_count=5, sample_size=10) == [0, 1, 2, 3, 4]
        assert _stratified_sample_indices(page_count=10, sample_size=10) == list(range(10))

    def test_sample_size_zero_returns_full_range(self):
        from shu.core.ocr_routing import _stratified_sample_indices

        assert _stratified_sample_indices(page_count=100, sample_size=0) == list(range(100))


class TestSamplingClassifier:
    """SHU-739 fix #3: long documents that are decisively text-or-OCR get
    classified from a 10-page sample instead of a full 241-page scan."""

    def _build_doc(self, *, page_count: int, real_text_indices: set[int]):
        """Make a fake fitz.Document where specific page indices have real text."""
        from unittest.mock import MagicMock

        doc = MagicMock()
        doc.page_count = page_count

        def load_page(i: int):
            if i in real_text_indices:
                # Centered block within the interior margin
                return _fake_page([(200, 300, 500, 500, "real body text")])
            return _fake_page([])  # empty

        doc.load_page.side_effect = load_page
        return doc

    def test_long_decisively_text_doc_uses_sample_path(self):
        """482-page born-digital book: every sampled page has text. Sample fraction = 1.0,
        which is > threshold + band, so the sample decision sticks. Full scan is skipped."""
        from shu.core.ocr_routing import classify_pdf

        thresholds = RoutingThresholds(
            page_margin_ratio=0.125,
            text_page_fraction=0.5,
            sample_size=10,
            sample_min_pages=30,
            ambiguous_band=0.15,
        )
        # Mark all 482 pages as having real text
        doc = self._build_doc(page_count=482, real_text_indices=set(range(482)))
        decision = classify_pdf(doc, thresholds)

        assert decision.use_ocr is False
        assert decision.real_text_fraction == 1.0
        assert "sampled_fraction" in decision.reason
        # Crucially: only the 10 sample pages were loaded, not 241+
        assert doc.load_page.call_count == 10

    def test_long_decisively_ocr_doc_uses_sample_path(self):
        """482-page scanned book: every sampled page is empty. Sample fraction = 0.0,
        which is < threshold - band, so OCR is chosen from the sample alone."""
        from shu.core.ocr_routing import classify_pdf

        thresholds = RoutingThresholds(
            page_margin_ratio=0.125,
            text_page_fraction=0.5,
            sample_size=10,
            sample_min_pages=30,
            ambiguous_band=0.15,
        )
        doc = self._build_doc(page_count=482, real_text_indices=set())
        decision = classify_pdf(doc, thresholds)

        assert decision.use_ocr is True
        assert decision.real_text_fraction == 0.0
        assert "sampled_fraction" in decision.reason
        assert doc.load_page.call_count == 10

    def test_ambiguous_sample_falls_back_to_full_scan(self):
        """A doc with exactly 50% real text in the sample sits inside the band
        and triggers a full per-page scan, with the actual decision based on
        the full ground truth."""
        from shu.core.ocr_routing import classify_pdf

        thresholds = RoutingThresholds(
            page_margin_ratio=0.125,
            text_page_fraction=0.5,
            sample_size=10,
            sample_min_pages=30,
            ambiguous_band=0.15,
        )
        # 60-page doc, every other page real-text → fraction = 0.5 (perfectly ambiguous).
        # Both the sample and the full scan see fraction=0.5, but the sample
        # triggers fallback so we exercise the full-scan path.
        real_text = {i for i in range(60) if i % 2 == 0}
        doc = self._build_doc(page_count=60, real_text_indices=real_text)
        decision = classify_pdf(doc, thresholds)

        # Sample loaded 10 pages, then the full scan loaded all 60.
        # Implementation may re-load pages already sampled — we just check
        # at minimum the full-scan total of 60 is reached.
        assert doc.load_page.call_count >= 60
        # The reason string for the full scan path uses "real_text_fraction=...",
        # not "sampled_fraction=..." — confirms we didn't return the sample decision.
        assert "sampled_fraction" not in decision.reason

    def test_short_doc_skips_sampling(self):
        """Documents under sample_min_pages always do the full scan."""
        from shu.core.ocr_routing import classify_pdf

        thresholds = RoutingThresholds(
            page_margin_ratio=0.125,
            text_page_fraction=0.5,
            sample_size=10,
            sample_min_pages=30,
            ambiguous_band=0.15,
        )
        # 20 pages: under sample_min_pages, sampling is skipped entirely.
        doc = self._build_doc(page_count=20, real_text_indices=set(range(20)))
        decision = classify_pdf(doc, thresholds)

        assert decision.use_ocr is False
        # No "sampled_fraction" — full-scan path was used (early-exit at page 10).
        assert "sampled_fraction" not in decision.reason

    def test_sampling_disabled_via_zero_sample_size(self):
        """Setting sample_size=0 disables sampling regardless of doc length."""
        from shu.core.ocr_routing import classify_pdf

        thresholds = RoutingThresholds(
            page_margin_ratio=0.125,
            text_page_fraction=0.5,
            sample_size=0,
            sample_min_pages=30,
            ambiguous_band=0.15,
        )
        doc = self._build_doc(page_count=482, real_text_indices=set(range(482)))
        decision = classify_pdf(doc, thresholds)

        assert decision.use_ocr is False
        assert "sampled_fraction" not in decision.reason
