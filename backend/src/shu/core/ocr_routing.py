"""OCR-vs-text routing classifier (SHU-728).

Decides whether a PDF should be sent to OCR or text-extracted directly,
based on a per-page "does this page already have real text?" check
aggregated across the document.

Adapted from OCRmyPDF's page-classification ideas (12.5% margin filter,
corrupt-text exclusion); see SHU-728 design notes for the full rationale.
The rule is purely structural — no language detection, no dictionary.

Reproducing a routing decision from logs
----------------------------------------
Every `auto` routing decision emits one INFO line at
``shu.core.ocr_service`` (event ``ocr_routing.auto_decision``) carrying
``decision``, ``reason``, ``page_count``, ``real_text_fraction``, and the
configured thresholds (``page_margin_ratio``, ``text_page_fraction``).
That line alone is sufficient to identify a misroute and explain the
broad reason class.

For per-page detail without re-uploading the document:

1. Raise the ``shu.core.ocr_service`` logger to DEBUG. The next ingestion
   pass over the document emits ``ocr_routing.per_page_signals`` with the
   full per-page signal vector (interior block count, corrupt-text flag,
   per-page real-text verdict).
2. Or replay the document offline against
   ``scripts/ocr_routing_calibrate.py``: pass the file path with ``-v`` to
   print every page's signals at the calibrated thresholds.

Either path reproduces the decision deterministically without requiring
the original ingestion job's file bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import fitz

# Unicode replacement character emitted by PyMuPDF when a glyph cannot be
# decoded via the page's character map. A page whose extracted text contains
# this glyph has a broken text layer and must be routed to OCR even if the
# bbox/structural checks pass.
_REPLACEMENT_CHAR = "�"


@dataclass(frozen=True)
class PageSignals:
    """Per-page diagnostic signals captured during classification."""

    interior_text_blocks: int
    """Count of text blocks with non-empty stripped text whose bbox intersects
    the page rectangle inset by `margin_ratio` on each side."""

    has_corrupt_text: bool
    """True if the page's extracted text contains the Unicode replacement char,
    indicating a broken character map. Treated as 'no real text' for routing."""

    has_real_text: bool
    """Final per-page verdict: at least one interior, non-empty,
    non-corrupt text block."""


@dataclass(frozen=True)
class RoutingThresholds:
    """Tunable thresholds for the classifier.

    Defaults match the calibrated values from `scripts/
    ocr_routing_calibration_report.md`. Production callers normally use
    `RoutingThresholds.from_settings()` to honour runtime overrides via
    `SHU_OCR_PAGE_MARGIN_RATIO` and `SHU_OCR_TEXT_PAGE_FRACTION`.
    """

    page_margin_ratio: float = 0.125
    """Fraction of page width/height to inset on each side when filtering out
    header/footer chrome from the 'real text' decision. Borrowed from
    OCRmyPDF's --redo-ocr detailed-analysis margin filter."""

    text_page_fraction: float = 0.5
    """Document is routed to OCR iff
    (pages with real text) / (total pages) < this threshold."""

    @classmethod
    def from_settings(cls) -> RoutingThresholds:
        """Resolve thresholds from runtime settings."""
        # Local import keeps this module free of pydantic-settings at import time
        # for callers that only want the dataclasses (e.g. the calibration script).
        from .config import get_settings_instance

        settings = get_settings_instance()
        return cls(
            page_margin_ratio=settings.ocr_page_margin_ratio,
            text_page_fraction=settings.ocr_text_page_fraction,
        )


@dataclass(frozen=True)
class RoutingDecision:
    """Result of `classify_pdf`. Carries enough detail to diagnose a misroute
    from the log line alone.
    """

    use_ocr: bool
    real_text_fraction: float
    page_count: int
    pages: list[PageSignals] = field(default_factory=list)
    reason: str = ""


def page_has_real_text(page: fitz.Page, margin_ratio: float) -> PageSignals:
    """Return diagnostic signals for one page.

    A page 'has real text' iff at least one text block satisfies all of:
    - non-empty stripped text
    - no Unicode replacement chars (broken cmap)
    - bbox intersects the page rectangle inset by `margin_ratio` on each side
      (filters out header/footer chrome, page numbers, watermarks).
    """
    rect = page.rect
    interior = fitz.Rect(
        rect.x0 + margin_ratio * rect.width,
        rect.y0 + margin_ratio * rect.height,
        rect.x1 - margin_ratio * rect.width,
        rect.y1 - margin_ratio * rect.height,
    )

    interior_blocks = 0
    has_corrupt = False
    has_real = False

    # get_text("blocks") returns list of (x0, y0, x1, y1, text, block_no, block_type)
    for block in page.get_text("blocks"):
        x0, y0, x1, y1, text = block[0], block[1], block[2], block[3], block[4]
        stripped = text.strip()
        if not stripped:
            continue
        if _REPLACEMENT_CHAR in stripped:
            has_corrupt = True
            continue
        bbox = fitz.Rect(x0, y0, x1, y1)
        if bbox.intersects(interior):
            interior_blocks += 1
            has_real = True

    return PageSignals(
        interior_text_blocks=interior_blocks,
        has_corrupt_text=has_corrupt,
        has_real_text=has_real,
    )


def classify_pdf(doc: fitz.Document, thresholds: RoutingThresholds) -> RoutingDecision:
    """Decide whether a PDF should be OCR'd or text-extracted.

    Aggregates per-page `page_has_real_text` signals into a single binary
    decision: route to OCR when the fraction of pages with real text falls
    below `thresholds.text_page_fraction`.

    Reuses the caller's already-open `fitz.Document` — no double-open, no
    bytes copy. Per-page cost is one `page.get_text("blocks")` call plus
    a small bbox-intersection loop; measured at ~1-7 ms per page on real
    PDFs (dense scientific text is the slow end, scanned image-only pages
    are the fast end).

    The scan exits early as soon as the decision is locked:
    - If the running real-text count reaches `text_page_fraction * total`,
      OCR can't be triggered no matter what the remaining pages say —
      route to TEXT and stop.
    - If the running real-text count plus all remaining pages can't reach
      that count, OCR is locked in — route to OCR and stop.
    Worst case (a perfectly balanced doc that toggles real/blank every
    other page) still scans every page; common case (pure born-digital
    or pure scan) decides within the first few pages.
    """
    page_count = doc.page_count
    if page_count == 0:
        return RoutingDecision(
            use_ocr=False,
            real_text_fraction=0.0,
            page_count=0,
            pages=[],
            reason="empty_document",
        )

    # Real-text pages required to *avoid* OCR.
    # use_ocr = real/total < T  ↔  use_ocr=False iff real >= total*T
    needed = thresholds.text_page_fraction * page_count

    pages: list[PageSignals] = []
    real_text_pages = 0

    for i in range(page_count):
        sig = page_has_real_text(doc.load_page(i), thresholds.page_margin_ratio)
        pages.append(sig)
        if sig.has_real_text:
            real_text_pages += 1

        scanned = i + 1
        remaining = page_count - scanned

        # Locked TEXT: enough confirmed real-text pages already.
        if real_text_pages >= needed:
            return _build_decision(
                use_ocr=False,
                real_text_pages=real_text_pages,
                page_count=page_count,
                pages=pages,
                thresholds=thresholds,
                exited_early=remaining > 0,
                scanned=scanned,
            )

        # Locked OCR: even if all remaining pages were real-text, we couldn't reach `needed`.
        if real_text_pages + remaining < needed:
            return _build_decision(
                use_ocr=True,
                real_text_pages=real_text_pages,
                page_count=page_count,
                pages=pages,
                thresholds=thresholds,
                exited_early=remaining > 0,
                scanned=scanned,
            )

    # Loop ran to completion (one of the early-exit branches must trigger
    # before this — included for safety).
    return _build_decision(
        use_ocr=(real_text_pages / page_count) < thresholds.text_page_fraction,
        real_text_pages=real_text_pages,
        page_count=page_count,
        pages=pages,
        thresholds=thresholds,
        exited_early=False,
        scanned=page_count,
    )


def _build_decision(
    *,
    use_ocr: bool,
    real_text_pages: int,
    page_count: int,
    pages: list[PageSignals],
    thresholds: RoutingThresholds,
    exited_early: bool,
    scanned: int,
) -> RoutingDecision:
    """Construct a `RoutingDecision` with a human-readable reason string."""
    fraction = real_text_pages / page_count
    op = "<" if use_ocr else ">="
    base = (
        f"real_text_fraction={fraction:.3f} {op} threshold={thresholds.text_page_fraction:.3f} "
        f"({real_text_pages}/{page_count} pages with real text)"
    )
    reason = f"{base} — early exit after {scanned} of {page_count} pages" if exited_early else base
    return RoutingDecision(
        use_ocr=use_ocr,
        real_text_fraction=fraction,
        page_count=page_count,
        pages=pages,
        reason=reason,
    )
