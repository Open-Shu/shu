"""Download BEIR benchmark datasets for evaluation.

Usage:
    python -m tests.benchmark.download_datasets --dataset nfcorpus
    python -m tests.benchmark.download_datasets --dataset scifact
    python -m tests.benchmark.download_datasets --dataset all
"""

from __future__ import annotations

import argparse
import logging
import sys
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

DATASETS_DIR = Path(__file__).parent / ".datasets"

BEIR_DATASETS = {
    "nfcorpus": {
        "url": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/nfcorpus.zip",
        "description": "NFCorpus: 3,633 docs, 323 queries (biomedical)",
    },
    "scifact": {
        "url": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip",
        "description": "SciFact: 5,183 docs, 300 queries (scientific claims)",
    },
    "fiqa": {
        "url": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/fiqa.zip",
        "description": "FiQA: 57,638 docs, 648 queries (financial QA)",
    },
    "trec-covid": {
        "url": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/trec-covid.zip",
        "description": "TREC-COVID: 171,332 docs, 50 queries (scientific papers, human-judged)",
    },
}


def download_dataset(name: str, target_dir: Path | None = None) -> Path:
    """Download and extract a BEIR dataset.

    Args:
        name: Dataset name (e.g., "nfcorpus", "scifact").
        target_dir: Directory to extract into. Defaults to datasets/<name>/.

    Returns:
        Path to the extracted dataset directory.

    Raises:
        ValueError: If dataset name is not recognized.
        RuntimeError: If download or extraction fails.
    """
    if name not in BEIR_DATASETS:
        raise ValueError(f"Unknown dataset '{name}'. Available: {list(BEIR_DATASETS.keys())}")

    dataset_info = BEIR_DATASETS[name]
    url = dataset_info["url"]

    if target_dir is None:
        target_dir = DATASETS_DIR / name

    # Check if already downloaded
    if _is_valid_dataset(target_dir):
        logger.info("Dataset '%s' already exists at %s — skipping download", name, target_dir)
        return target_dir

    logger.info("Downloading %s: %s", name, dataset_info["description"])
    logger.info("URL: %s", url)

    zip_path = DATASETS_DIR / f"{name}.zip"
    try:
        _download_file(url, zip_path)
        _extract_zip(zip_path, DATASETS_DIR)

        if not _is_valid_dataset(target_dir):
            raise RuntimeError(
                f"Extraction completed but expected files not found in {target_dir}. "
                f"Expected: corpus.jsonl, queries.jsonl, qrels/test.tsv"
            )

        logger.info("Dataset '%s' ready at %s", name, target_dir)
        return target_dir

    finally:
        # Clean up zip file
        if zip_path.exists():
            zip_path.unlink()


def _download_file(url: str, dest: Path) -> None:
    """Download a file with progress reporting."""
    import ssl

    logger.info("Downloading to %s ...", dest)

    # Create SSL context — try system certs first, fall back to unverified
    # if the system cert store is incomplete (common on macOS with Python.org builds)
    ssl_context: ssl.SSLContext | None = None
    try:
        import certifi

        ssl_context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        try:
            ssl_context = ssl.create_default_context()
            # Test if default context works by attempting connection
        except Exception:
            pass

    if ssl_context is None:
        logger.warning("Using unverified SSL — install certifi for proper cert verification")
        ssl_context = ssl._create_unverified_context()

    def _progress_hook(block_num: int, block_size: int, total_size: int) -> None:
        if total_size > 0:
            downloaded = block_num * block_size
            percent = min(100.0, downloaded * 100.0 / total_size)
            mb_downloaded = downloaded / (1024 * 1024)
            mb_total = total_size / (1024 * 1024)
            if block_num % 100 == 0:
                logger.info("  %.1f / %.1f MB (%.0f%%)", mb_downloaded, mb_total, percent)

    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context))
    urllib.request.install_opener(opener)
    urllib.request.urlretrieve(url, dest, reporthook=_progress_hook)  # noqa: S310
    logger.info("Download complete: %s", dest)


def _extract_zip(zip_path: Path, extract_to: Path) -> None:
    """Extract a zip file."""
    logger.info("Extracting %s to %s ...", zip_path, extract_to)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_to)
    logger.info("Extraction complete")


def _is_valid_dataset(dataset_dir: Path) -> bool:
    """Check if a dataset directory has the expected BEIR structure."""
    return (
        dataset_dir.is_dir()
        and (dataset_dir / "corpus.jsonl").exists()
        and (dataset_dir / "queries.jsonl").exists()
        and (dataset_dir / "qrels").is_dir()
    )


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Download BEIR benchmark datasets")
    parser.add_argument(
        "--dataset",
        required=True,
        choices=list(BEIR_DATASETS.keys()) + ["all"],
        help="Dataset to download",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=None,
        help="Custom target directory (default: datasets/<name>/)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    datasets_to_download = list(BEIR_DATASETS.keys()) if args.dataset == "all" else [args.dataset]

    for name in datasets_to_download:
        try:
            path = download_dataset(name, args.target_dir)
            print(f"  {name}: {path}")
        except Exception as e:
            logger.error("Failed to download '%s': %s", name, e)
            sys.exit(1)


if __name__ == "__main__":
    main()
