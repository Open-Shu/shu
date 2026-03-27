"""LLM-judged relevance assessment for cross-topic retrieval benchmark.

Collects candidate documents from both baseline and multi-surface search,
then judges each query-document pair for relevance. Produces BEIR-format
qrels that account for cross-topic semantic bridging — the kind of
retrieval that standard BEIR qrels penalize as false positives.

Usage:
    # Step 1: Collect candidates from both search strategies
    python -m tests.benchmark.relevance_judge collect \
        --dataset nfcorpus \
        --kb-id <kb-id> \
        --output candidates.jsonl \
        --limit 20 \
        --threshold 0.0

    # Step 2: Judge candidates (reads corpus, applies LLM judgment)
    python -m tests.benchmark.relevance_judge judge \
        --dataset nfcorpus \
        --candidates candidates.jsonl \
        --output shu_judged.tsv

    # Step 3: Run benchmark with custom qrels
    python -m tests.benchmark.run_benchmark --dataset nfcorpus \
        --reuse-kb <kb-id> --qrels-split shu_judged
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .beir_loader import BeirDataset

logger = logging.getLogger(__name__)


@dataclass
class CandidateDoc:
    """A candidate document for relevance judgment."""

    query_id: str
    query_text: str
    doc_id: str  # BEIR corpus ID
    doc_title: str
    doc_text: str
    source: str  # "baseline", "multi_surface", or "both"
    baseline_score: float = 0.0
    multi_surface_score: float = 0.0
    surface_scores: dict[str, float] = field(default_factory=dict)


@dataclass
class RelevanceJudgment:
    """A relevance judgment for a query-document pair."""

    query_id: str
    doc_id: str
    score: int  # 0 = not relevant, 1 = partially relevant, 2 = highly relevant
    justification: str = ""


class CandidateCollector:
    """Collects candidate documents from baseline and multi-surface search.

    Runs each query through both search strategies, maps results to BEIR
    corpus IDs, deduplicates, and produces a list of query-document pairs
    for relevance judgment.
    """

    def __init__(
        self,
        client: Any,  # httpx.AsyncClient
        kb_id: str,
        auth_headers: dict[str, str],
        id_map: dict[str, str],  # shu_doc_uuid -> beir_doc_id
    ):
        self.client = client
        self.kb_id = kb_id
        self.auth_headers = auth_headers
        self.id_map = id_map

    async def collect(
        self,
        queries: dict[str, Any],  # query_id -> BeirQuery
        dataset: BeirDataset,
        *,
        limit: int = 20,
        threshold: float = 0.0,
    ) -> list[CandidateDoc]:
        """Collect candidate documents from both search strategies.

        Args:
            queries: Dict of query_id -> BeirQuery to evaluate.
            dataset: Full BEIR dataset (for corpus text lookup).
            limit: Top-k results per search strategy.
            threshold: Minimum score to include a candidate.

        Returns:
            Deduplicated list of CandidateDoc with corpus text populated.
        """
        candidates: list[CandidateDoc] = []
        total_queries = len(queries)

        for i, (query_id, query) in enumerate(queries.items()):
            if (i + 1) % 50 == 0 or (i + 1) == total_queries:
                logger.info("Collecting candidates: %d / %d queries", i + 1, total_queries)

            # Collect from both strategies
            baseline_docs = await self._search_baseline(query.text, limit, threshold)
            ms_docs = await self._search_multi_surface(query.text, limit, threshold)

            # Merge and deduplicate by BEIR doc ID
            merged: dict[str, dict[str, Any]] = {}

            for shu_id, score in baseline_docs.items():
                beir_id = self.id_map.get(shu_id)
                if not beir_id:
                    continue
                if beir_id not in merged:
                    merged[beir_id] = {
                        "source": "baseline",
                        "baseline_score": score,
                        "multi_surface_score": 0.0,
                        "surface_scores": {},
                    }
                else:
                    merged[beir_id]["baseline_score"] = score
                    if merged[beir_id]["source"] == "multi_surface":
                        merged[beir_id]["source"] = "both"

            for shu_id, (score, surfaces) in ms_docs.items():
                beir_id = self.id_map.get(shu_id)
                if not beir_id:
                    continue
                if beir_id not in merged:
                    merged[beir_id] = {
                        "source": "multi_surface",
                        "baseline_score": 0.0,
                        "multi_surface_score": score,
                        "surface_scores": surfaces,
                    }
                else:
                    merged[beir_id]["multi_surface_score"] = score
                    merged[beir_id]["surface_scores"] = surfaces
                    if merged[beir_id]["source"] == "baseline":
                        merged[beir_id]["source"] = "both"

            # Build CandidateDoc with full corpus text
            for beir_id, info in merged.items():
                corpus_entry = dataset.corpus.get(beir_id)
                if not corpus_entry:
                    continue

                candidates.append(CandidateDoc(
                    query_id=query_id,
                    query_text=query.text,
                    doc_id=beir_id,
                    doc_title=corpus_entry.title,
                    doc_text=corpus_entry.text,
                    source=info["source"],
                    baseline_score=info["baseline_score"],
                    multi_surface_score=info["multi_surface_score"],
                    surface_scores=info["surface_scores"],
                ))

        logger.info(
            "Candidate collection complete: %d queries, %d candidates (avg %.1f per query)",
            total_queries,
            len(candidates),
            len(candidates) / total_queries if total_queries > 0 else 0,
        )
        return candidates

    async def _search_baseline(
        self, query_text: str, limit: int, threshold: float,
    ) -> dict[str, float]:
        """Run baseline similarity search, return {shu_doc_id: score}."""
        payload = {
            "query": query_text,
            "query_type": "similarity",
            "limit": limit,
            "similarity_threshold": threshold,
        }
        resp = await self.client.post(
            f"/api/v1/query/{self.kb_id}/search",
            json=payload,
            headers=self.auth_headers,
        )
        if resp.status_code != 200:
            return {}

        results = resp.json().get("data", {}).get("results", [])
        # Aggregate to document level (max score per doc)
        doc_scores: dict[str, float] = {}
        for r in results:
            doc_id = r.get("document_id", "")
            score = float(r.get("similarity_score", 0.0))
            if doc_id and (doc_id not in doc_scores or score > doc_scores[doc_id]):
                doc_scores[doc_id] = score
        return doc_scores

    async def _search_multi_surface(
        self, query_text: str, limit: int, threshold: float,
    ) -> dict[str, tuple[float, dict[str, float]]]:
        """Run multi-surface search, return {shu_doc_id: (final_score, surface_scores)}."""
        payload: dict[str, Any] = {
            "query": query_text,
            "query_type": "multi_surface",
            "limit": limit,
            "similarity_threshold": threshold,
        }
        resp = await self.client.post(
            f"/api/v1/query/{self.kb_id}/search",
            json=payload,
            headers=self.auth_headers,
        )
        if resp.status_code != 200:
            return {}

        results = resp.json().get("data", {}).get("multi_surface_results", [])
        doc_scores: dict[str, tuple[float, dict[str, float]]] = {}
        for r in results:
            doc_id = r.get("document_id", "")
            score = float(r.get("final_score", 0.0))
            surfaces = r.get("surface_scores", {})
            if doc_id:
                doc_scores[doc_id] = (score, surfaces)
        return doc_scores


def save_candidates(candidates: list[CandidateDoc], path: Path) -> None:
    """Save candidates to JSONL for reproducibility."""
    with open(path, "w", encoding="utf-8") as f:
        for c in candidates:
            f.write(json.dumps({
                "query_id": c.query_id,
                "query_text": c.query_text,
                "doc_id": c.doc_id,
                "doc_title": c.doc_title,
                "doc_text": c.doc_text,
                "source": c.source,
                "baseline_score": c.baseline_score,
                "multi_surface_score": c.multi_surface_score,
                "surface_scores": c.surface_scores,
            }) + "\n")
    logger.info("Saved %d candidates to %s", len(candidates), path)


def load_candidates(path: Path) -> list[CandidateDoc]:
    """Load candidates from JSONL."""
    candidates = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            data = json.loads(line)
            candidates.append(CandidateDoc(**data))
    logger.info("Loaded %d candidates from %s", len(candidates), path)
    return candidates


def save_judgments(judgments: list[RelevanceJudgment], qrels_path: Path, details_path: Path | None = None) -> None:
    """Save judgments as BEIR-format qrels TSV and optional detail JSONL.

    Args:
        judgments: List of relevance judgments.
        qrels_path: Path for BEIR-format TSV (query-id, corpus-id, score).
        details_path: Optional path for detailed JSONL with justifications.
    """
    # Write BEIR qrels TSV
    with open(qrels_path, "w", encoding="utf-8") as f:
        f.write("query-id\tcorpus-id\tscore\n")
        for j in judgments:
            if j.score > 0:  # Only include relevant documents in qrels
                f.write(f"{j.query_id}\t{j.doc_id}\t{j.score}\n")

    relevant_count = sum(1 for j in judgments if j.score > 0)
    logger.info(
        "Saved %d qrels (%d relevant of %d judged) to %s",
        relevant_count, relevant_count, len(judgments), qrels_path,
    )

    # Write detailed judgments with justifications
    if details_path:
        with open(details_path, "w", encoding="utf-8") as f:
            for j in judgments:
                f.write(json.dumps({
                    "query_id": j.query_id,
                    "doc_id": j.doc_id,
                    "score": j.score,
                    "justification": j.justification,
                }) + "\n")
        logger.info("Saved %d detailed judgments to %s", len(judgments), details_path)


def load_judgments(path: Path) -> list[RelevanceJudgment]:
    """Load judgments from detail JSONL."""
    judgments = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            data = json.loads(line)
            judgments.append(RelevanceJudgment(**data))
    logger.info("Loaded %d judgments from %s", len(judgments), path)
    return judgments


def comparison_summary(
    judgments: list[RelevanceJudgment],
    candidates: list[CandidateDoc],
    score_threshold: float = 0.3,
) -> str:
    """Generate a summary comparing baseline vs multi-surface discovery.

    Shows how many relevant documents each strategy found, threshold
    survival rates, and examples of cross-topic discoveries.

    Args:
        judgments: List of relevance judgments.
        candidates: List of candidate documents with scores.
        score_threshold: Score threshold for survival rate analysis.
    """
    # Build lookup
    candidate_map: dict[tuple[str, str], CandidateDoc] = {
        (c.query_id, c.doc_id): c for c in candidates
    }

    # Count relevant docs by source
    relevant_by_source = {"baseline": 0, "multi_surface": 0, "both": 0}
    cross_topic_examples: list[tuple[str, str, str, str]] = []

    # Threshold survival tracking
    baseline_total = 0
    baseline_above_threshold = 0
    ms_total = 0
    ms_above_threshold = 0

    for c in candidates:
        if c.baseline_score > 0:
            baseline_total += 1
            if c.baseline_score >= score_threshold:
                baseline_above_threshold += 1
        if c.multi_surface_score > 0:
            ms_total += 1
            if c.multi_surface_score >= score_threshold:
                ms_above_threshold += 1

    for j in judgments:
        if j.score == 0:
            continue
        c = candidate_map.get((j.query_id, j.doc_id))
        if not c:
            continue

        relevant_by_source[c.source] += 1

        if c.source == "multi_surface" and j.score >= 1 and len(cross_topic_examples) < 10:
            cross_topic_examples.append((
                c.query_text, c.doc_title, c.source, j.justification,
            ))

    lines = [
        "## Relevance Judgment Summary",
        "",
        f"Total candidates judged: {len(judgments)}",
        f"Total relevant (score > 0): {sum(1 for j in judgments if j.score > 0)}",
        f"  Highly relevant (score = 2): {sum(1 for j in judgments if j.score == 2)}",
        f"  Partially relevant (score = 1): {sum(1 for j in judgments if j.score == 1)}",
        "",
        "### Discovery by Search Strategy",
        "",
        f"Found by baseline only: {relevant_by_source['baseline']}",
        f"Found by multi-surface only: {relevant_by_source['multi_surface']}",
        f"Found by both: {relevant_by_source['both']}",
        "",
        f"### Threshold Survival (score >= {score_threshold})",
        "",
        f"Baseline: {baseline_above_threshold} / {baseline_total} results survive ({baseline_above_threshold / baseline_total * 100:.1f}%)" if baseline_total > 0 else "Baseline: no results",
        f"Multi-surface: {ms_above_threshold} / {ms_total} results survive ({ms_above_threshold / ms_total * 100:.1f}%)" if ms_total > 0 else "Multi-surface: no results",
        "",
    ]

    if cross_topic_examples:
        lines.append("### Cross-Topic Discoveries (multi-surface only)")
        lines.append("")
        for query, title, source, justification in cross_topic_examples:
            lines.append(f"- **Query**: {query}")
            lines.append(f"  **Document**: {title}")
            lines.append(f"  **Why relevant**: {justification}")
            lines.append("")

    return "\n".join(lines)
