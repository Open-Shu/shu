"""BEIR dataset loader for Shu RAG benchmarks.

Parses standard BEIR-format datasets (corpus.jsonl, queries.jsonl, qrels/*.tsv)
without depending on the heavy `beir` pip package. Uses only stdlib json/csv.

BEIR format reference: https://github.com/beir-cellar/beir
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BeirCorpusEntry:
    """A single document from a BEIR corpus."""

    doc_id: str
    title: str
    text: str


@dataclass(frozen=True)
class BeirQuery:
    """A single query from a BEIR query set."""

    query_id: str
    text: str


@dataclass
class BeirDataset:
    """A loaded BEIR dataset with corpus, queries, and relevance judgments."""

    name: str
    corpus: dict[str, BeirCorpusEntry] = field(default_factory=dict)
    queries: dict[str, BeirQuery] = field(default_factory=dict)
    qrels: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def corpus_size(self) -> int:
        return len(self.corpus)

    @property
    def query_count(self) -> int:
        return len(self.queries)

    @property
    def total_judgments(self) -> int:
        return sum(len(docs) for docs in self.qrels.values())

    def relevant_doc_ids(self) -> set[str]:
        """Return all document IDs that appear in any qrel with relevance > 0."""
        doc_ids: set[str] = set()
        for docs in self.qrels.values():
            for doc_id, relevance in docs.items():
                if relevance > 0:
                    doc_ids.add(doc_id)
        return doc_ids

    def subset(self, query_ids: set[str] | None = None, doc_ids: set[str] | None = None) -> BeirDataset:
        """Extract a subset of the dataset by query and/or document IDs.

        If query_ids is provided, only those queries and their qrels are kept.
        If doc_ids is provided, only those documents and matching qrels are kept.
        If both are provided, both filters are applied.
        """
        filtered_queries = self.queries
        filtered_qrels = self.qrels
        filtered_corpus = self.corpus

        if query_ids is not None:
            filtered_queries = {qid: q for qid, q in self.queries.items() if qid in query_ids}
            filtered_qrels = {qid: docs for qid, docs in self.qrels.items() if qid in query_ids}

        if doc_ids is not None:
            filtered_corpus = {did: d for did, d in self.corpus.items() if did in doc_ids}
            filtered_qrels = {
                qid: {did: rel for did, rel in docs.items() if did in doc_ids}
                for qid, docs in filtered_qrels.items()
            }
            # Remove queries with no remaining qrels
            filtered_qrels = {qid: docs for qid, docs in filtered_qrels.items() if docs}

        return BeirDataset(
            name=f"{self.name}_subset",
            corpus=filtered_corpus,
            queries=filtered_queries,
            qrels=filtered_qrels,
        )

    def summary(self) -> str:
        """Human-readable summary of the dataset."""
        relevant = self.relevant_doc_ids()
        return (
            f"Dataset: {self.name}\n"
            f"  Corpus: {self.corpus_size} documents\n"
            f"  Queries: {self.query_count}\n"
            f"  Judgments: {self.total_judgments} (across {len(relevant)} relevant docs)"
        )


class BeirLoader:
    """Load BEIR-format datasets from a directory.

    Expected directory structure:
        dataset_dir/
        ├── corpus.jsonl      # {"_id": "...", "title": "...", "text": "..."}
        ├── queries.jsonl     # {"_id": "...", "text": "..."}
        └── qrels/
            └── test.tsv      # query-id<TAB>corpus-id<TAB>score
    """

    def __init__(self, dataset_dir: Path, name: str | None = None):
        self.dataset_dir = Path(dataset_dir)
        self.name = name or self.dataset_dir.name

    def load(self, qrels_split: str = "test") -> BeirDataset:
        """Load the full dataset (corpus, queries, qrels).

        Args:
            qrels_split: Which qrels split to load (default "test").

        Returns:
            A BeirDataset with all components loaded.

        Raises:
            FileNotFoundError: If required files are missing.
        """
        corpus = self._load_corpus()
        queries = self._load_queries()
        qrels = self._load_qrels(qrels_split)

        # Validate qrels reference existing corpus/query IDs
        orphan_queries = set(qrels.keys()) - set(queries.keys())
        if orphan_queries:
            logger.warning("qrels reference %d query IDs not in queries.jsonl", len(orphan_queries))

        all_qrel_doc_ids = {did for docs in qrels.values() for did in docs}
        orphan_docs = all_qrel_doc_ids - set(corpus.keys())
        if orphan_docs:
            logger.warning("qrels reference %d doc IDs not in corpus.jsonl", len(orphan_docs))

        dataset = BeirDataset(name=self.name, corpus=corpus, queries=queries, qrels=qrels)
        logger.info("Loaded BEIR dataset: %s", dataset.summary())
        return dataset

    def _load_corpus(self) -> dict[str, BeirCorpusEntry]:
        """Parse corpus.jsonl."""
        corpus_path = self.dataset_dir / "corpus.jsonl"
        if not corpus_path.exists():
            raise FileNotFoundError(f"Corpus file not found: {corpus_path}")

        corpus: dict[str, BeirCorpusEntry] = {}
        with open(corpus_path, encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    doc_id = str(obj["_id"])
                    corpus[doc_id] = BeirCorpusEntry(
                        doc_id=doc_id,
                        title=obj.get("title", ""),
                        text=obj.get("text", ""),
                    )
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning("Skipping malformed corpus line %d: %s", line_num, e)

        logger.debug("Loaded %d corpus entries from %s", len(corpus), corpus_path)
        return corpus

    def _load_queries(self) -> dict[str, BeirQuery]:
        """Parse queries.jsonl."""
        queries_path = self.dataset_dir / "queries.jsonl"
        if not queries_path.exists():
            raise FileNotFoundError(f"Queries file not found: {queries_path}")

        queries: dict[str, BeirQuery] = {}
        with open(queries_path, encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    query_id = str(obj["_id"])
                    queries[query_id] = BeirQuery(
                        query_id=query_id,
                        text=obj["text"],
                    )
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning("Skipping malformed query line %d: %s", line_num, e)

        logger.debug("Loaded %d queries from %s", len(queries), queries_path)
        return queries

    def _load_qrels(self, split: str = "test") -> dict[str, dict[str, int]]:
        """Parse qrels TSV file.

        BEIR qrels format: query-id<TAB>corpus-id<TAB>score
        Some datasets include a header row; we detect and skip it.
        """
        qrels_path = self.dataset_dir / "qrels" / f"{split}.tsv"
        if not qrels_path.exists():
            raise FileNotFoundError(f"Qrels file not found: {qrels_path}")

        qrels: dict[str, dict[str, int]] = {}
        with open(qrels_path, encoding="utf-8", newline="") as f:
            reader = csv.reader(f, delimiter="\t")
            for row_num, row in enumerate(reader, 1):
                if len(row) < 3:
                    continue
                # Skip header row if present
                if row_num == 1 and row[0].lower() in ("query-id", "query_id", "qid"):
                    continue
                try:
                    query_id = str(row[0])
                    doc_id = str(row[1])
                    relevance = int(row[2])
                    if query_id not in qrels:
                        qrels[query_id] = {}
                    qrels[query_id][doc_id] = relevance
                except (ValueError, IndexError) as e:
                    logger.warning("Skipping malformed qrels row %d: %s", row_num, e)

        logger.debug("Loaded %d qrels entries from %s", sum(len(d) for d in qrels.values()), qrels_path)
        return qrels
