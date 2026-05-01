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

    Defaults are calibrated empirically against `docs/.pdf-corpus/`. Run
    `scripts/ocr_routing_calibrate.py --sweep` to regenerate the
    margin/fraction agreement table for the current corpus and classifier
    code. Production callers normally use `RoutingThresholds.from_settings()`
    to honour runtime overrides via `SHU_OCR_PAGE_MARGIN_RATIO` and
    `SHU_OCR_TEXT_PAGE_FRACTION`.
    """

    page_margin_ratio: float = 0.125
    """Fraction of page width/height to inset on each side when filtering out
    header/footer chrome from the 'real text' decision. Borrowed from
    OCRmyPDF's --redo-ocr detailed-analysis margin filter."""

    text_page_fraction: float = 0.5
    """Document is routed to OCR iff
    (pages with real text) / (total pages) < this threshold."""

    sample_size: int = 10
    """SHU-739 fix #3: number of stratified pages to sample before falling
    back to the full scan. 0 disables sampling (always full scan)."""

    sample_min_pages: int = 30
    """Documents shorter than this skip sampling — the existing early-exit
    handles short docs quickly enough that sampling adds no benefit."""

    ambiguous_band: float = 0.15
    """Half-width of the ambiguous band around `text_page_fraction`. If the
    sampled real-text fraction lands inside (text_page_fraction - band,
    text_page_fraction + band), the classifier falls back to the full
    per-page scan."""

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
            sample_size=settings.ocr_classify_sample_size,
            sample_min_pages=settings.ocr_classify_sample_min_pages,
            ambiguous_band=settings.ocr_classify_ambiguous_band,
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
    # block_type: 0 = text, 1 = image. Image blocks must be skipped — block[4]
    # for an image is a placeholder string like "<image: ...>" that would pass
    # the strip+replacement-char checks and falsely register an image-only
    # page as "real text".
    for block in page.get_text("blocks"):
        if len(block) < 7 or block[6] != 0:
            continue
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


def _stratified_sample_indices(page_count: int, sample_size: int) -> list[int]:
    """Pick `sample_size` page indices stratified across the document (SHU-739).

    Splits the sample into thirds: head, middle, tail. The head and tail thirds
    are contiguous from the document edges; the middle third is evenly spaced
    through the interior. Indices are returned sorted ascending and deduped.

    For a 482-page book at sample_size=10 this yields roughly:
      head:   [0, 1, 2, 3]      (first 4)
      middle: [120, 240, 360]   (3 evenly-spaced interior)
      tail:   [479, 480, 481]   (last 3)
    """
    if sample_size <= 0 or page_count <= sample_size:
        # Sample bigger than doc: just scan everything.
        return list(range(page_count))

    head_n = sample_size // 3 + (sample_size % 3 > 0)  # bias head when not divisible
    tail_n = sample_size // 3 + (sample_size % 3 > 1)
    middle_n = sample_size - head_n - tail_n

    indices: set[int] = set()
    indices.update(range(min(head_n, page_count)))
    indices.update(range(max(0, page_count - tail_n), page_count))
    if middle_n > 0:
        # Evenly spaced through the interior, excluding head and tail regions.
        interior_start = head_n
        interior_end = max(head_n, page_count - tail_n - 1)
        interior_span = max(1, interior_end - interior_start)
        for k in range(middle_n):
            # Distribute middle samples in the interior at evenly-spaced
            # fractions, avoiding the bookends already covered above.
            frac = (k + 1) / (middle_n + 1)
            indices.add(interior_start + round(frac * interior_span))
    return sorted(indices)


def _classify_pdf_sampled(doc: fitz.Document, thresholds: RoutingThresholds) -> RoutingDecision | None:
    """Try to decide using a stratified page sample (SHU-739 fix #3).

    Returns a `RoutingDecision` if the sample's real-text fraction falls
    cleanly outside the ambiguous band around `text_page_fraction`. Returns
    `None` if the sample is ambiguous and the caller should fall back to
    the full per-page scan.
    """
    page_count = doc.page_count
    sample_indices = _stratified_sample_indices(page_count, thresholds.sample_size)
    if not sample_indices:
        return None

    sampled_signals: list[PageSignals] = []
    real_text_in_sample = 0
    for idx in sample_indices:
        sig = page_has_real_text(doc.load_page(idx), thresholds.page_margin_ratio)
        sampled_signals.append(sig)
        if sig.has_real_text:
            real_text_in_sample += 1

    sample_fraction = real_text_in_sample / len(sample_indices)
    threshold = thresholds.text_page_fraction
    band = thresholds.ambiguous_band

    # Inside the ambiguous band — defer to the full scan.
    if (threshold - band) < sample_fraction < (threshold + band):
        return None

    use_ocr = sample_fraction < threshold
    op = "<" if use_ocr else ">="
    reason = (
        f"sampled_fraction={sample_fraction:.3f} {op} threshold={threshold:.3f} "
        f"({real_text_in_sample}/{len(sample_indices)} sampled pages with real text "
        f"of {page_count} total — outside ambiguous band ±{band:.2f})"
    )
    return RoutingDecision(
        use_ocr=use_ocr,
        # Report the sample fraction as the document's estimated fraction;
        # consumers reading this for diagnostics need to know it's a sample.
        real_text_fraction=sample_fraction,
        page_count=page_count,
        pages=sampled_signals,
        reason=reason,
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

    SHU-739 fix #3: for documents with `page_count >= sample_min_pages`,
    a stratified sample of `sample_size` pages is checked first. If the
    sample's real-text fraction falls outside the ambiguous band around
    `text_page_fraction`, the sample's verdict is returned directly —
    cutting per-job CPU from O(page_count) to O(sample_size) on the
    common case where the document is decisively one or the other.
    Documents shorter than `sample_min_pages` and ambiguous-sample
    documents fall through to the full per-page scan with early-exit.

    The full scan exits early as soon as the decision is locked:
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

    # SHU-739 fix #3: try sampling first for long documents.
    # `sample_size=0` disables sampling entirely; `sample_min_pages=0` enables
    # sampling for all documents regardless of length (the `>= 0` is trivially
    # true). Together these match the documented config semantics.
    if thresholds.sample_size > 0 and page_count >= thresholds.sample_min_pages:
        sampled = _classify_pdf_sampled(doc, thresholds)
        if sampled is not None:
            return sampled
        # Sample was ambiguous — fall through to the full scan below.

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
