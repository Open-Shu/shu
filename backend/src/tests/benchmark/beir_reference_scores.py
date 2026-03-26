"""Published BEIR benchmark reference scores and methodology descriptions.

NDCG@10 scores from the original BEIR paper (Thakur et al., 2021) Table 2.
Source: https://ar5iv.labs.arxiv.org/html/2104.08663
Scores retrieved: 2026-03-19

These are static reference data used to contextualize our benchmark results
against published retrieval systems. Scores and methodology descriptions
should be updated when new leaderboard data is published.
"""

from __future__ import annotations

from dataclasses import dataclass

# Date these reference scores were last verified against published sources
SCORES_RETRIEVED_DATE = "2026-03-19"
SCORES_SOURCE = "BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models (Thakur et al., NeurIPS 2021), Table 2"
SCORES_URL = "https://ar5iv.labs.arxiv.org/html/2104.08663"


@dataclass(frozen=True)
class ReferenceModel:
    name: str
    ndcg10: float
    model_type: str  # lexical, dense, late_interaction, reranker, document_expansion
    methodology: str  # how the system works — key for differentiation


# --- Model Methodologies ---
# These describe how each system retrieves documents, enabling comparison
# with Shu's multi-surface approach.

METHODOLOGIES = {
    "BM25": (
        "Lexical term-matching using TF-IDF weighting. No semantic understanding — "
        "relies entirely on exact and partial keyword overlap between query and document. "
        "The universal baseline for IR evaluation."
    ),
    "BM25+CE": (
        "Two-stage: BM25 retrieves candidate documents, then a cross-encoder (BERT-based) "
        "re-ranks them by jointly encoding query and document. High quality but expensive at "
        "query time — cross-encoder inference scales linearly with candidate count."
    ),
    "DPR": (
        "Dense Passage Retrieval. Dual-encoder architecture with separate BERT encoders for "
        "queries and passages, trained on Natural Questions. Retrieval via cosine similarity "
        "of pre-computed embeddings. Poor zero-shot transfer to out-of-domain corpora."
    ),
    "ANCE": (
        "Approximate Nearest Neighbor Negative Contrastive Estimation. Dense retriever that "
        "uses hard negatives from an ANN index during training to improve embedding quality. "
        "Better than DPR on zero-shot but still a single-vector dense retriever."
    ),
    "TAS-B": (
        "Topic-Aware Sampling with Balanced training. Dense bi-encoder distilled from a "
        "cross-encoder teacher using topic-aware sampling of training pairs. Single embedding "
        "per document — no document-level understanding beyond what the embedding captures."
    ),
    "GenQ": (
        "Generates synthetic queries from passages using a T5 model, then fine-tunes a "
        "bi-encoder on the (query, passage) pairs. Query generation happens at training time "
        "to create training data — queries are discarded after training. The resulting model "
        "is corpus-specific: new corpus requires retraining. Shu's query_match surface uses "
        "a similar concept but stores queries as persistent, searchable artifacts at ingestion "
        "time — no model retraining needed, and new documents are immediately searchable."
    ),
    "ColBERT": (
        "Late interaction model that encodes queries and documents into multiple token-level "
        "embeddings and computes relevance via MaxSim (maximum similarity between each query "
        "token and all document tokens). Higher quality than single-vector but requires storing "
        "per-token embeddings for every document, increasing storage 100-200x."
    ),
    "docT5query": (
        "Document expansion: a T5 model generates predicted queries for each document, which "
        "are appended to the document text before indexing with BM25. Similar ingestion-time "
        "intelligence concept to Shu but limited to augmenting lexical retrieval rather than "
        "creating independent retrieval surfaces."
    ),
    "DeepCT": (
        "Deep Contextualized Term weighting. Uses BERT to estimate term importance for each "
        "passage, replacing raw term frequency in the BM25 formula. Enhances lexical retrieval "
        "with learned term weights but still fundamentally keyword-based."
    ),
    "SPARTA": (
        "Sparse Transformer Matching. Learns sparse representations from transformer encoders "
        "for efficient retrieval. Balances between dense and sparse approaches."
    ),
}


def _model(name: str, ndcg10: float, model_type: str) -> ReferenceModel:
    """Create a ReferenceModel with methodology lookup."""
    return ReferenceModel(
        name=name,
        ndcg10=ndcg10,
        model_type=model_type,
        methodology=METHODOLOGIES.get(name, ""),
    )


# NFCorpus NDCG@10 from BEIR Table 2
NFCORPUS: dict[str, ReferenceModel] = {
    "BM25": _model("BM25", 0.325, "lexical"),
    "BM25+CE": _model("BM25+CE", 0.350, "reranker"),
    "docT5query": _model("docT5query", 0.328, "document_expansion"),
    "TAS-B": _model("TAS-B", 0.319, "dense"),
    "GenQ": _model("GenQ", 0.319, "dense"),
    "ColBERT": _model("ColBERT", 0.305, "late_interaction"),
    "ANCE": _model("ANCE", 0.237, "dense"),
    "DPR": _model("DPR", 0.189, "dense"),
    "DeepCT": _model("DeepCT", 0.283, "lexical"),
    "SPARTA": _model("SPARTA", 0.301, "dense"),
}

# SciFact NDCG@10 from BEIR Table 2
SCIFACT: dict[str, ReferenceModel] = {
    "BM25": _model("BM25", 0.665, "lexical"),
    "BM25+CE": _model("BM25+CE", 0.693, "reranker"),
    "docT5query": _model("docT5query", 0.675, "document_expansion"),
    "TAS-B": _model("TAS-B", 0.643, "dense"),
    "GenQ": _model("GenQ", 0.644, "dense"),
    "ColBERT": _model("ColBERT", 0.671, "late_interaction"),
    "ANCE": _model("ANCE", 0.507, "dense"),
    "DPR": _model("DPR", 0.318, "dense"),
}

# FiQA NDCG@10 from BEIR Table 2
FIQA: dict[str, ReferenceModel] = {
    "BM25": _model("BM25", 0.236, "lexical"),
    "BM25+CE": _model("BM25+CE", 0.344, "reranker"),
    "docT5query": _model("docT5query", 0.250, "document_expansion"),
    "TAS-B": _model("TAS-B", 0.300, "dense"),
    "GenQ": _model("GenQ", 0.308, "dense"),
    "ColBERT": _model("ColBERT", 0.317, "late_interaction"),
    "ANCE": _model("ANCE", 0.295, "dense"),
    "DPR": _model("DPR", 0.112, "dense"),
}

# Map dataset names to reference scores
REFERENCE_SCORES: dict[str, dict[str, ReferenceModel]] = {
    "nfcorpus": NFCORPUS,
    "scifact": SCIFACT,
    "fiqa": FIQA,
}


def get_reference_scores(dataset_name: str) -> dict[str, ReferenceModel] | None:
    """Get published reference scores for a dataset, if available."""
    return REFERENCE_SCORES.get(dataset_name.lower())


def best_published_improvement(dataset_name: str) -> tuple[str, float] | None:
    """Find the largest published relative improvement over BM25 for a dataset.

    Returns:
        Tuple of (model_name, improvement_pct) or None if no reference data.
    """
    refs = get_reference_scores(dataset_name)
    if not refs or "BM25" not in refs:
        return None

    bm25 = refs["BM25"].ndcg10
    if bm25 <= 0:
        return None

    best_name = ""
    best_pct = 0.0
    for name, model in refs.items():
        if name == "BM25":
            continue
        pct = (model.ndcg10 - bm25) / bm25 * 100
        if pct > best_pct:
            best_pct = pct
            best_name = model.name
    return (best_name, best_pct) if best_name else None
