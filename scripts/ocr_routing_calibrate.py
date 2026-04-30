#!/usr/bin/env python3
r"""OCR routing calibration script (SHU-728).

Loads a corpus of PDFs, runs the per-page real-text classifier, optionally
compares to hand-labeled ground truth, and helps choose defaults for
SHU_OCR_PAGE_MARGIN_RATIO and SHU_OCR_TEXT_PAGE_FRACTION.

Usage:
    # Single PDF, default thresholds, verbose per-page output:
    python scripts/ocr_routing_calibrate.py path/to/file.pdf -v

    # Full corpus run, comparing to labels.json:
    python scripts/ocr_routing_calibrate.py shu/docs/.pdf-corpus \\
        --labels scripts/ocr_routing_labels.json

    # Threshold sweep against labels:
    python scripts/ocr_routing_calibrate.py shu/docs/.pdf-corpus \\
        --labels scripts/ocr_routing_labels.json --sweep

Labels file format (JSON):
    {
        "filename.pdf": {"should_ocr": true, "reason": "scanned, no text layer"},
        ...
    }
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

import fitz

# Make the shu package importable when running the script directly.
_BACKEND_SRC = Path(__file__).resolve().parent.parent / "backend" / "src"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

from shu.core.ocr_routing import RoutingThresholds, classify_pdf  # noqa: E402


def _classify_one(pdf_path: Path, thresholds: RoutingThresholds) -> dict:
    with fitz.open(pdf_path) as doc:
        decision = classify_pdf(doc, thresholds)
    return {
        "use_ocr": decision.use_ocr,
        "real_text_fraction": decision.real_text_fraction,
        "page_count": decision.page_count,
        "reason": decision.reason,
        "pages": [asdict(p) for p in decision.pages],
    }


def _print_single(pdf_path: Path, result: dict, verbose: bool) -> None:
    decision_str = "OCR" if result["use_ocr"] else "TEXT"
    print(
        f"{decision_str:4s}  fraction={result['real_text_fraction']:.3f}  "
        f"pages={result['page_count']:>4d}  {pdf_path.name}"
    )
    if verbose:
        print(f"       reason: {result['reason']}")
        for i, page in enumerate(result["pages"]):
            print(
                f"       page {i+1:>3d}: "
                f"real={int(page['has_real_text'])}  "
                f"interior_blocks={page['interior_text_blocks']:>3d}  "
                f"corrupt={int(page['has_corrupt_text'])}"
            )


def _load_labels(labels_path: Path | None) -> dict[str, dict]:
    if not labels_path:
        return {}
    if not labels_path.exists():
        print(f"warning: labels file not found: {labels_path}", file=sys.stderr)
        return {}
    with open(labels_path) as f:
        return json.load(f)


def _evaluate(corpus: list[Path], labels: dict[str, dict], thresholds: RoutingThresholds) -> tuple[int, int, list]:
    """Run the classifier across the corpus and report agreement with labels.

    Returns (correct, labeled_count, mismatches). Unlabeled PDFs are counted
    in `total_count` but excluded from agreement math.
    """
    correct = 0
    labeled = 0
    mismatches = []
    for pdf in sorted(corpus):
        try:
            result = _classify_one(pdf, thresholds)
        except Exception as e:
            print(f"ERROR  {pdf.name}: {e}", file=sys.stderr)
            continue
        label = labels.get(pdf.name)
        if label is None:
            tag = "?"
        else:
            labeled += 1
            expected = label["should_ocr"]
            actual = result["use_ocr"]
            if expected == actual:
                correct += 1
                tag = "ok"
            else:
                tag = "MISS"
                mismatches.append(
                    {
                        "name": pdf.name,
                        "expected": expected,
                        "actual": actual,
                        "fraction": result["real_text_fraction"],
                        "page_count": result["page_count"],
                        "label_reason": label.get("reason", ""),
                        "classifier_reason": result["reason"],
                    }
                )
        decision_str = "OCR" if result["use_ocr"] else "TEXT"
        print(
            f"{tag:4s}  {decision_str:4s}  fraction={result['real_text_fraction']:.3f}  "
            f"pages={result['page_count']:>4d}  {pdf.name}"
        )
    return correct, labeled, mismatches


def _sweep(corpus: list[Path], labels: dict[str, dict]) -> None:
    """Try a small grid of (margin_ratio, fraction) and report agreement counts."""
    margins = [0.0, 0.05, 0.10, 0.125, 0.15, 0.20]
    fractions = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]

    # Cache classifier results per (pdf, margin, fraction). Sampling-based
    # classification (SHU-739 fix #3) can take different paths at different
    # `fraction` values — the ambiguous band shifts with fraction, so a doc
    # whose sampled real-text fraction is 0.55 gets a sampled decision at
    # threshold=0.5 (outside band) but falls back to a full scan at
    # threshold=0.7 (inside band 0.55-0.85), potentially producing a
    # different `real_text_fraction`. Including `fraction` in the cache key
    # ensures each row in the sweep table reflects what the production
    # classifier would actually do at that threshold.
    cache: dict[tuple[str, float, float], dict] = {}

    print(f"{'margin':>7s}  {'fraction':>9s}  {'correct':>8s}  {'labeled':>8s}  {'pct':>5s}")
    print("-" * 50)
    best = (0, None)
    for margin in margins:
        for frac in fractions:
            thresholds = RoutingThresholds(page_margin_ratio=margin, text_page_fraction=frac)
            correct = 0
            labeled = 0
            for pdf in corpus:
                label = labels.get(pdf.name)
                if label is None:
                    continue
                key = (pdf.name, margin, frac)
                if key not in cache:
                    try:
                        cache[key] = _classify_one(pdf, thresholds)
                    except Exception as e:
                        # Don't silently drop a fixture from the sweep — print to
                        # stderr so the operator sees which file misbehaved at
                        # which (margin, fraction). The cache key is left unset
                        # so a future invocation retries.
                        print(f"sweep error  {pdf.name} (margin={margin}, fraction={frac}): {e}", file=sys.stderr)
                        continue
                use_ocr = cache[key]["use_ocr"]
                labeled += 1
                if use_ocr == label["should_ocr"]:
                    correct += 1
            pct = (100.0 * correct / labeled) if labeled else 0.0
            print(f"{margin:>7.3f}  {frac:>9.3f}  {correct:>8d}  {labeled:>8d}  {pct:>5.1f}")
            if correct > best[0]:
                best = (correct, (margin, frac))
    if best[1]:
        m, f = best[1]
        print(f"\nbest agreement: margin={m}, fraction={f} ({best[0]} correct)")


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR routing calibration (SHU-728)")
    parser.add_argument("path", help="PDF file or directory of PDFs")
    parser.add_argument("--labels", help="Path to labels JSON")
    parser.add_argument("--margin", type=float, default=0.125, help="page_margin_ratio (default: 0.125)")
    parser.add_argument("--fraction", type=float, default=0.5, help="text_page_fraction (default: 0.5)")
    parser.add_argument("--sweep", action="store_true", help="Sweep margin/fraction and report agreement")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print per-page signals")
    args = parser.parse_args()

    target = Path(args.path)
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return 1

    # Start from production runtime defaults (sample_size, sample_min_pages,
    # ambiguous_band) so this script exercises the same classifier shape as
    # the live system; override only the geometric thresholds the CLI exposes.
    from dataclasses import replace

    base = RoutingThresholds.from_settings()
    thresholds = replace(base, page_margin_ratio=args.margin, text_page_fraction=args.fraction)

    if target.is_file():
        result = _classify_one(target, thresholds)
        _print_single(target, result, args.verbose)
        return 0

    pdfs = [p for p in target.iterdir() if p.suffix.lower() == ".pdf"]
    if not pdfs:
        print(f"error: no .pdf files found in {target}", file=sys.stderr)
        return 1

    labels = _load_labels(Path(args.labels)) if args.labels else {}

    if args.sweep:
        if not labels:
            print("error: --sweep requires --labels", file=sys.stderr)
            return 1
        _sweep(sorted(pdfs), labels)
        return 0

    correct, labeled, mismatches = _evaluate(sorted(pdfs), labels, thresholds)
    if labeled:
        print(f"\nagreement: {correct}/{labeled} ({100.0 * correct / labeled:.1f}%)")
    if mismatches:
        print("\nmismatches:")
        for m in mismatches:
            print(f"  {m['name']}: expected_ocr={m['expected']}, got_ocr={m['actual']}")
            print(f"    label_reason: {m['label_reason']}")
            print(f"    classifier:   {m['classifier_reason']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
