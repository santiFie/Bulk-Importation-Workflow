"""
RAG context retriever for the Planner — playbook-only, source-level aggregation.

Architecture
------------
Only ONE FAISS vector store is built, over ``playbooks/``.  The retrieval
strategy is **source-level aggregation**:

1. Run a standard similarity search to get the top-K *chunks* from the index.
2. Group chunks by their ``source`` metadata key (one entry per .md file).
3. Keep the best (lowest-distance) score for each unique playbook.
4. Select the top-N most-relevant playbooks.
5. Load the **complete** .md file for each selected playbook from disk.
6. Inject the full documents into the Planner's prompt.

This guarantees the Planner always receives intact, sequential workflow
procedures rather than isolated mid-document fragments.  It also naturally
handles multi-step requests that span two or more playbooks (e.g. "edit
DSpace metadata AND upload the result to MinIO").

Agent capabilities are NOT retrieved via RAG; they are embedded statically
in the Planner's system prompt inside ``graph_plan.py``.

Adding a new workflow: drop a ``<workflow_name>.md`` in ``playbooks/``.
No code changes needed.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from langchain_community.vectorstores import FAISS
from langchain_community.vectorstores.faiss import DistanceStrategy
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Directory paths
# ---------------------------------------------------------------------------
_MODULE_DIR   = Path(__file__).resolve().parent   # core/utils/
_PROJECT_ROOT = _MODULE_DIR.parent.parent          # Multi-Services Project/
PLAYBOOKS_DIR = _PROJECT_ROOT / "playbooks"

# ---------------------------------------------------------------------------
# Model config
# ---------------------------------------------------------------------------
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# ---------------------------------------------------------------------------
# Chunking / retrieval config
# ---------------------------------------------------------------------------
CHUNK_SIZE          = 800   # chars per chunk used for building the index
CHUNK_OVERLAP       = 100
CANDIDATE_K         = 15    # how many chunks to pull from FAISS initially
TOP_N_PLAYBOOKS     = 2     # max unique playbooks to inject into the prompt
# Maximum Euclidean distance for a playbook to be considered relevant.
# Set to None to disable threshold filtering (always inject TOP_N_PLAYBOOKS).
SCORE_THRESHOLD: float | None = 1.2


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_playbook_chunks(docs_dir: Path) -> list[Document]:
    """Load all ``*.md`` files in *docs_dir* and split them into chunks.

    Chunks are used exclusively to build the similarity index; the Planner
    receives the full source documents, not these chunks.
    """
    if not docs_dir.exists():
        raise FileNotFoundError(
            f"Playbooks directory not found at: {docs_dir}\n"
            f"Create it and add Markdown files there."
        )

    headers_to_split_on = [
        ("#",   "h1"),
        ("##",  "h2"),
        ("###", "h3"),
    ]
    md_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=headers_to_split_on,
        strip_headers=False,
    )
    char_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )

    all_docs: list[Document] = []

    for md_file in sorted(docs_dir.glob("*.md")):
        raw_text = md_file.read_text(encoding="utf-8")
        header_splits = md_splitter.split_text(raw_text)
        char_splits   = char_splitter.split_documents(header_splits)

        for doc in char_splits:
            doc.metadata["source"]   = md_file.stem
            doc.metadata["file"]     = md_file.name
            doc.metadata["doc_type"] = "playbook"

        all_docs.extend(char_splits)
        logger.debug("Indexed %d chunks from %s", len(char_splits), md_file.name)

    logger.info("Total playbook chunks indexed: %d", len(all_docs))
    return all_docs


@lru_cache(maxsize=1)
def _get_embeddings() -> HuggingFaceEmbeddings:
    """Build (and cache) the shared embedding model."""
    logger.info("Loading embedding model: %s", EMBEDDING_MODEL)
    return HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )


@lru_cache(maxsize=1)
def _build_playbook_vectorstore() -> FAISS:
    """Build and cache the FAISS index for workflow playbooks."""
    logger.info("Building playbook vector store from: %s", PLAYBOOKS_DIR)
    docs  = _load_playbook_chunks(PLAYBOOKS_DIR)
    store = FAISS.from_documents(
        docs,
        _get_embeddings(),
        distance_strategy=DistanceStrategy.EUCLIDEAN_DISTANCE,
    )
    logger.info("Playbook vector store ready (%d vectors).", store.index.ntotal)
    return store


def _select_top_playbooks(query: str) -> list[str]:
    """Return the stems of the most relevant playbook files for *query*.

    Steps
    -----
    1. Pull ``CANDIDATE_K`` chunks from FAISS (Euclidean distance).
    2. For each unique ``source``, keep the **minimum** distance score
       (lower = more similar).
    3. Sort sources by score ascending and keep the top ``TOP_N_PLAYBOOKS``
       whose score is below ``SCORE_THRESHOLD`` (if set).
    """
    store = _build_playbook_vectorstore()

    # similarity_search_with_score returns (Document, score) tuples.
    # With EUCLIDEAN_DISTANCE, lower score → closer → more relevant.
    candidates = store.similarity_search_with_score(query, k=CANDIDATE_K)

    if not candidates:
        logger.warning("FAISS returned no candidates for query: %s", query[:80])
        return []

    # Aggregate: best (min) score per source document
    best_score: dict[str, float] = defaultdict(lambda: float("inf"))
    for doc, score in candidates:
        src = doc.metadata.get("source", "unknown")
        if score < best_score[src]:
            best_score[src] = score

    # Sort by score (ascending = most relevant first)
    ranked = sorted(best_score.items(), key=lambda x: x[1])
    logger.debug("Ranked playbooks: %s", ranked)

    # Apply optional threshold filter
    if SCORE_THRESHOLD is not None:
        ranked = [(src, s) for src, s in ranked if s <= SCORE_THRESHOLD]

    selected = [src for src, _ in ranked[:TOP_N_PLAYBOOKS]]
    logger.info("Selected playbooks for injection: %s", selected)
    return selected


def _load_full_playbook(stem: str) -> Document | None:
    """Load the complete .md file for *stem* from PLAYBOOKS_DIR."""
    path = PLAYBOOKS_DIR / f"{stem}.md"
    if not path.exists():
        logger.error("Playbook file not found: %s", path)
        return None
    content = path.read_text(encoding="utf-8")
    return Document(
        page_content=content,
        metadata={"source": stem, "file": path.name, "doc_type": "playbook"},
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def retrieve_planner_context(query: str) -> str:
    """Return a playbook context string for the Planner.

    Selects the most relevant playbooks for *query* via source-level
    aggregation and returns their **full content** formatted as a Markdown
    section ready to be injected into the Planner's prompt.

    Returns an empty string if no relevant playbooks are found.
    """
    try:
        selected_stems = _select_top_playbooks(query)
    except Exception as exc:
        logger.error("Playbook RAG retrieval failed: %s", exc, exc_info=True)
        return ""

    if not selected_stems:
        logger.warning("No relevant playbooks found for query: %s", query[:80])
        return ""

    playbook_docs: list[Document] = []
    for stem in selected_stems:
        doc = _load_full_playbook(stem)
        if doc:
            playbook_docs.append(doc)

    if not playbook_docs:
        return ""

    # Format as a clearly delimited context block
    parts: list[str] = [
        "## Workflow Playbooks (retrieved from knowledge base)\n",
        f"*Matched playbooks: {', '.join(selected_stems)}*\n",
    ]

    for doc in playbook_docs:
        parts.append(f"\n---\n\n{doc.page_content.strip()}\n")

    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# CLI entry point (for manual testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    parser = argparse.ArgumentParser(
        description="Test the Planner Context Retriever (playbook-only, source-level)."
    )
    parser.add_argument("query", type=str, help="User query / task description.")
    parser.add_argument(
        "--top_n",
        type=int,
        default=TOP_N_PLAYBOOKS,
        help=f"Max playbooks to inject (default: {TOP_N_PLAYBOOKS}).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=SCORE_THRESHOLD,
        help=f"Max Euclidean distance for relevance (default: {SCORE_THRESHOLD}).",
    )
    args = parser.parse_args()

    # Allow CLI overrides
    import core.utils.rag_context as _self
    _self.TOP_N_PLAYBOOKS = args.top_n
    _self.SCORE_THRESHOLD = args.threshold

    print("\n" + "=" * 60)
    print(f"🔍 Query: '{args.query}'")
    print(f"📊 top_n={args.top_n} | threshold={args.threshold}")
    print("=" * 60 + "\n")

    result = retrieve_planner_context(args.query)

    if result:
        print(result)
    else:
        print("⚠️  No relevant playbooks found.")