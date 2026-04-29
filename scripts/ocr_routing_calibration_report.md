# OCR Routing Calibration Report (SHU-728)

**Date:** 2026-04-28
**Corpus:** `shu/docs/.pdf-corpus/` (27 PDFs)
**Classifier:** `shu.core.ocr_routing.classify_pdf` (per-page interior-text + corrupt-text exclusion + fraction aggregation)

## Summary

The classifier achieves **100% agreement** with hand-labeled ground truth at the recommended defaults:

| Threshold | Value | Env var |
|---|---|---|
| Page margin ratio | `0.125` | `SHU_OCR_PAGE_MARGIN_RATIO` |
| Text-page fraction | `0.5` | `SHU_OCR_TEXT_PAGE_FRACTION` |

A wide plateau of `(margin, fraction)` settings yields 100% agreement on this corpus, indicating the classifier is well-separated for the document types we ingest in practice (born-digital scientific PDFs, slide decks, scanned patent filings, screenshot-PDFs).

## Threshold sweep

Agreement (correct / labeled) across a grid of `(margin, fraction)`:

```
margin   fraction  pct
 0.000    0.300   100.0
 0.000    0.400   100.0
 0.000    0.500   100.0
 0.000    0.600   100.0
 0.000    0.700   100.0
 0.000    0.800   100.0
 0.000    0.900    96.3
 0.050    0.300   100.0
 0.050    0.400   100.0
 0.050    0.500   100.0
 0.050    0.600   100.0
 0.050    0.700   100.0
 0.050    0.800   100.0
 0.050    0.900    96.3
 0.100    0.300   100.0
 0.100    0.400   100.0
 0.100    0.500   100.0
 0.100    0.600    96.3
 0.100    0.700    96.3
 0.100    0.800    96.3
 0.100    0.900    92.6
 0.125    0.300   100.0
 0.125    0.400   100.0
 0.125    0.500   100.0
 0.125    0.600    96.3
 0.125    0.700    96.3
 0.125    0.800    96.3
 0.125    0.900    92.6
 0.150    0.300   100.0
 0.150    0.400   100.0
 0.150    0.500   100.0
 0.150    0.600    96.3
 0.150    0.700    96.3
 0.150    0.800    96.3
 0.150    0.900    92.6
 0.200    0.300   100.0
 0.200    0.400   100.0
 0.200    0.500   100.0
 0.200    0.600    96.3
 0.200    0.700    92.6
 0.200    0.800    92.6
 0.200    0.900    88.9
```

The cliff at `fraction = 0.6` is driven by **Maxwell Biosciences Fungal Slides.pdf** (2 pages, 1/2 with interior text — fraction = 0.5 exactly). At `fraction >= 0.6`, that 50/50 sparse-text slide deck flips to OCR even though its text layer is real.

## Choice rationale

- **`margin_ratio = 0.125`** — matches OCRmyPDF's `--redo-ocr` detailed-analysis margin filter. Sits comfortably in the middle of the stable plateau (0.0–0.20 all agree). 12.5% of page width/height is conventional for header/footer chrome in standard page layouts.
- **`text_page_fraction = 0.5`** — the highest stable value before the cliff. Picking the high end of the plateau biases the classifier toward routing 50/50 hybrids (e.g. born-digital cover + scanned body) to OCR, which is the safer recovery option: re-OCRing born-digital pages still produces usable text, while skipping OCR on a half-scanned document silently loses half the content. Comparison: the rule is `use_ocr if fraction < threshold`, so at exactly `0.5`, a 50/50 doc routes to text (not OCR) — but a 49/51 (slight scan majority) routes to OCR. This matches operator intuition.

## Per-fixture results (defaults)

| Decision | Fraction | Pages | Filename |
|---|---|---|---|
| TEXT | 1.000 | 1 | 000029.pdf |
| TEXT | 1.000 | 1 | 000143.pdf |
| TEXT | 0.921 | 76 | 000152.pdf |
| TEXT | 0.973 | 147 | 000159.pdf |
| TEXT | 1.000 | 55 | 000187.pdf |
| TEXT | 1.000 | 76 | 000208.pdf |
| TEXT | 0.830 | 194 | 000282.pdf |
| TEXT | 1.000 | 1 | 000340.pdf |
| TEXT | 0.989 | 87 | 000584.pdf |
| TEXT | 0.982 | 226 | 000595.pdf |
| TEXT | 1.000 | 23 | 000859.pdf |
| **OCR** | **0.010** | **99** | **63_557217 Provisional Application As Filed 02_23_2024 118433-0091.pdf** |
| TEXT | 1.000 | 2 | Additional New References list - Response to IR_20260428.pdf |
| TEXT | 0.998 | 482 | Agentic_Design_Patterns.pdf |
| TEXT | 1.000 | 23 | Barrero-Guevara 2019 ... .pdf |
| TEXT | 1.000 | 1 | Batemen 2019 ... .pdf |
| TEXT | 1.000 | 39 | BenefitsGuide_Full-time_44070.pdf |
| **OCR** | **0.000** | **1** | **Claromer label 1.pdf** |
| **OCR** | **0.000** | **1** | **Claromer label 2.pdf** |
| TEXT | 0.909 | 11 | Harrison's Principles of Internal Medicine ... .pdf |
| TEXT | 1.000 | 13 | Howroyd 2024 ... .pdf |
| TEXT | 0.500 | 2 | Maxwell Biosciences Fungal Slides.pdf |
| TEXT | 1.000 | 5 | Maxwell poster version ... .pdf |
| **OCR** | **0.000** | **4** | **RDIF loan.pdf** |
| TEXT | 0.931 | 58 | RDIF slides.pdf |
| TEXT | 1.000 | 12 | Wang 2022 ... .pdf |
| TEXT | 1.000 | 10 | ge-et-al-2025 ... .pdf |

**4 OCR routes / 23 TEXT routes — all 27 match ground truth.**

## Coverage gaps and known limitations

The corpus over-represents born-digital scientific PDFs and under-represents:

- **Hybrid docs** (born-digital cover + scanned body) — corpus has none with this exact shape. The fraction-aggregation rule should handle this correctly (fraction lands between 0 and 1), but no labeled fixture exercises it.
- **PDFs with broken cmaps** (`�` replacement chars) — corpus has none. The `has_corrupt_text` exclusion is implemented but untested against a real fixture.
- **PDFs with stale OCR layer** (text rendered in mode 3 / invisible) — out of scope per ticket; no fitz API for Tr render mode without parsing content streams.
- **Scanned PDFs with a digital header in the top 12.5%** — corpus has none. The margin filter is implemented but the failure mode it addresses isn't represented in the labeled set.

These gaps are acceptable for shipping: the rule was adapted from OCRmyPDF (which has hardened these cases over years) and the calibration confirms the chosen thresholds are not over-fit to the corpus we have. If a misroute is reported in production, the per-page DEBUG logs from `RoutingDecision` give us enough to diagnose without replaying the file.

## Performance

Measured on the full corpus (1650 total pages across 27 PDFs) with the
calibrated defaults. The classifier opens the PDF via `fitz.open` and
walks pages calling `page.get_text("blocks")`; per-page cost is roughly
**1-7 ms** depending on text-block density.

The scan exits early as soon as the routing decision is locked — either
because enough real-text pages have accumulated to guarantee TEXT, or
because remaining pages can't possibly reach the threshold so OCR is
guaranteed. With early exit, the corpus visits **51% of total pages**
on average (845 of 1650).

Wallclock measurements (full corpus, calibrated defaults):

- **Corpus total**: 2854 ms without early exit → 1574 ms with early exit (45% reduction).
- **Worst case per document**: 775 ms on `Agentic_Design_Patterns.pdf` (482 pages, full scan) → 399 ms with early exit at page 242 of 482 (49% reduction).
- **Average per visited page**: ~1.9 ms.
- **Cold-start overhead**: ~150 ms once per worker process for fitz/MuPDF library load (paid before any document is processed).

Memory: peak Python-side allocation per document is < 200 KiB
regardless of page count (block tuples + small `PageSignals` records).
The MuPDF native heap is bounded by the SHU-710 process-global store
cap (128 MiB) with `store_shrink(100)` after each `doc.close()`.

The classifier call is wrapped in `loop.run_in_executor(None, ...)` so
the worker's event loop stays responsive during the CPU-bound scan.

## Reproducing

```bash
cd shu

# Single-doc decision (default thresholds):
python scripts/ocr_routing_calibrate.py docs/.pdf-corpus/<file>.pdf -v

# Full corpus comparison vs labels:
python scripts/ocr_routing_calibrate.py docs/.pdf-corpus \
    --labels scripts/ocr_routing_labels.json

# Threshold sweep:
python scripts/ocr_routing_calibrate.py docs/.pdf-corpus \
    --labels scripts/ocr_routing_labels.json --sweep
```
