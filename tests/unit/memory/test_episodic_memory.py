"""
Tests unitarios para el sistema de Memoria Episódica (CBR).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import pytest

from core.memory.episodic_memory.models import (
    Episode,
    EpisodeMetadata,
    EpisodePayload,
)
from core.memory.episodic_memory.store import EpisodicMemoryStore
from core.memory.episodic_memory.retriever import (
    extract_error_context,
    build_search_query,
    retrieve_relevant_episodes,
)


@pytest.fixture
def temp_memory_store():
    with tempfile.TemporaryDirectory() as tmp_dir:
        store = EpisodicMemoryStore(persist_directory=tmp_dir)
        yield store


def test_episode_models():
    """Valida la instanciación y serialización de modelos de episodios."""
    metadata = EpisodeMetadata(
        tool="deduplicator",
        source_name="unlp_doaj",
        error_class="ValueError",
        affected_column="date",
        resolved_by="autonomous",
    )
    payload = EpisodePayload(
        error_summary="Error de mes en inglés",
        culprit_sample="1999, June",
        transformation_strategy="value_mapping",
        solution_applied={"June": "06"},
    )
    episode = Episode(
        id="test_ep_001",
        metadata=metadata,
        embedding_content="Tool: deduplicator. Error: ValueError invalid int June",
        payload=payload,
    )
    assert episode.id == "test_ep_001"
    assert episode.metadata.tool == "deduplicator"
    assert episode.payload.solution_applied == {"June": "06"}


def test_store_seed_and_search(temp_memory_store: EpisodicMemoryStore):
    """Valida el sembrado inicial y la búsqueda vectorial filtrada."""
    assert temp_memory_store.count() == 0

    inserted = temp_memory_store.seed_default_episodes()
    assert inserted >= 3
    assert temp_memory_store.count() == inserted

    # Búsqueda semántica de un error con meses en inglés
    results = temp_memory_store.search_episodes(
        query_text="Tool: deduplicator. Error: invalid literal for int with base 10: 'June'. Column: date",
        filter_metadata={"tool": "deduplicator"},
        top_k=2,
    )
    assert len(results) > 0
    top = results[0]
    assert top.similarity_score > 0.50
    assert top.episode.metadata.tool == "deduplicator"
    assert "June" in top.episode.embedding_content or "english_month" in top.episode.id


def test_extract_error_context_pure_text():
    """Valida extracción determinista de clase de error y tokens citados."""
    msg = "Deduplicator error: ValueError: invalid literal for int() with base 10: 'June'"
    ctx = extract_error_context(msg)
    assert ctx["error_class"] == "ValueError"
    assert "June" in ctx["sample_tokens"]
    assert ctx["affected_column"] == "date"


def test_extract_error_context_with_csv(tmp_path: Path):
    """Valida escaneo del CSV para identificar la columna afectada."""
    csv_file = tmp_path / "sample.csv"
    csv_file.write_text("id,title,date,author\n1,Un paper,1999 June,Perez J.\n", encoding="utf-8")

    msg = "Error parsing '1999 June'"
    ctx = extract_error_context(msg, str(csv_file))
    assert ctx["affected_column"] == "date"


def test_retrieve_relevant_episodes_hierarchical(temp_memory_store: EpisodicMemoryStore):
    """Valida que el recuperador devuelva episodios con similitud adecuada."""
    error_msg = "Error al comparar fechas: 2004, Agosto — invalid literal for int() with base 10: 'Agosto'"
    results = retrieve_relevant_episodes(
        tool="deduplicator",
        source_name="unlp_doaj",
        error_msg=error_msg,
        store=temp_memory_store,
        min_similarity=0.40,
    )
    assert len(results) > 0
    assert results[0].episode.metadata.affected_column == "date"
