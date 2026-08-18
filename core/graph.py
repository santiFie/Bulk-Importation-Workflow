"""
Punto de entrada del grafo LangGraph del pipeline de importación a SEDICI.

Ensambla el grafo principal componiendo los cuatro subgrafos especializados
en una secuencia lineal de fases. Cada subgrafo encapsula una responsabilidad
bien definida y puede ser testeado e invocado de forma independiente.

Pipeline:
  START
    → SetupWorkspace        (inicialización del workspace y paths)
    → IngestSubgraph        (normaliza la fuente: CSV directo o PDFs en MinIO)
    → CrosswalkDedupSubgraph (crosswalk + deduplicación + reconciliación)
    → EnrichmentSubgraph    (enriquecimiento opcional: Crossref / OpenAlex)
    → ExportSubgraph        (SAF + importación a DSpace)
  END

Fuentes de entrada soportadas (via state["input_source_type"]):
  - "csv":       state["source_csv_path"] ya contiene el CSV listo.
  - "pdf_minio": PDFs almacenados en MinIO; el IngestSubgraph los procesa
                 y genera el CSV automáticamente.

Enriquecimiento opcional (via state["enrichment_enabled"]):
  - False (default): el EnrichmentSubgraph pasa directamente a END.
  - True:            consulta Crossref (por DOI) u OpenAlex (por ISBN/título).
"""

import asyncio
from typing import Any

from langgraph.graph import END, START, StateGraph

from core.nodes.pipeline_nodes import setup_workspace
from core.subgraphs.crosswalk_dedup import build_crosswalk_dedup_subgraph
from core.subgraphs.enrichment import build_enrichment_subgraph
from core.subgraphs.export import build_export_subgraph
from core.subgraphs.ingest import build_ingest_subgraph

# ---------------------------------------------------------------------------
# Re-exportaciones: mantienen compatibilidad con tests e importaciones existentes
# ---------------------------------------------------------------------------
from core.state import State  # noqa: F401
from core.nodes.crosswalk_agent.node import (  # noqa: F401
    generate_source_crosswalk_config,
    _generate_fallback_config,
)
from core.nodes.crosswalk_agent.helpers import (  # noqa: F401
    _read_csv_head,
    _format_csv_head_for_prompt,
    _create_validation_sample,
    GENERIC_COLUMNS,
    _build_generic_columns_description,
    _validate_separator_with_llm,
)
from core.nodes.pipeline_nodes import (  # noqa: F401
    _run_crosswalk,
    _save_csv,
    setup_workspace,
    map_source_to_generic,
    map_sedici_to_generic,
    deduplicate,
    metadata_reconciliation,
    map_to_sedici_format,
    metadata_corrections,
    get_pdfs,
    generate_saf_to_import,
    import_to_dspace,
)


# ---------------------------------------------------------------------------
# Registro ordenado de fases del pipeline (para tests y scripts de evaluación)
# ---------------------------------------------------------------------------

PIPELINE_STEPS: list[tuple[str, str]] = [
    ("Fase 0 - SetupWorkspace",        "SetupWorkspace"),
    ("Fase 1 - IngestSubgraph",        "IngestSubgraph"),
    ("Fase 2 - CrosswalkDedupSubgraph","CrosswalkDedupSubgraph"),
    ("Fase 3 - EnrichmentSubgraph",    "EnrichmentSubgraph"),
    ("Fase 4 - ExportSubgraph",        "ExportSubgraph"),
]
"""
Registro ordenado de las fases del pipeline con su nombre legible
y el nombre del nodo correspondiente en el StateGraph.
Se usa en tests y scripts de evaluación para ejecutar el pipeline
hasta un punto determinado.
"""


def get_step_node_names() -> list[str]:
    """Devuelve la lista ordenada de nombres de nodos del pipeline."""
    return [node_name for _, node_name in PIPELINE_STEPS]


def get_step_label(node_name: str) -> str:
    """Devuelve la etiqueta legible de una fase dado su nombre de nodo."""
    for label, name in PIPELINE_STEPS:
        if name == node_name:
            return label
    return node_name


# ---------------------------------------------------------------------------
# Construcción del grafo principal
# ---------------------------------------------------------------------------

async def create_graph(persistence_saver):
    """
    Crea el grafo principal del pipeline de importación en masa.

    Compila los cuatro subgrafos especializados y los compone en una
    secuencia lineal. Cada subgrafo es un nodo en el grafo principal
    con responsabilidades bien delimitadas.

    Args:
        persistence_saver: Checkpointer de LangGraph para persistencia de estado.

    Returns:
        Grafo compilado listo para ser invocado o expuesto en LangGraph Studio.
    """
    # Compilar subgrafos de forma paralela
    ingest_sg, crosswalk_dedup_sg, enrichment_sg, export_sg = await asyncio.gather(
        build_ingest_subgraph(),
        build_crosswalk_dedup_subgraph(),
        build_enrichment_subgraph(),
        build_export_subgraph(),
    )

    graph = StateGraph(State)

    # ── Nodos ──────────────────────────────────────────────────────────────
    graph.add_node("SetupWorkspace",        setup_workspace)
    graph.add_node("IngestSubgraph",        ingest_sg)
    graph.add_node("CrosswalkDedupSubgraph",crosswalk_dedup_sg)
    graph.add_node("EnrichmentSubgraph",    enrichment_sg)
    graph.add_node("ExportSubgraph",        export_sg)

    # ── Aristas (secuencia lineal de fases) ────────────────────────────────
    graph.add_edge(START,                   "SetupWorkspace")
    graph.add_edge("SetupWorkspace",        "IngestSubgraph")
    graph.add_edge("IngestSubgraph",        "CrosswalkDedupSubgraph")
    graph.add_edge("CrosswalkDedupSubgraph","EnrichmentSubgraph")
    graph.add_edge("EnrichmentSubgraph",    "ExportSubgraph")
    graph.add_edge("ExportSubgraph",        END)

    return graph.compile(
        checkpointer=persistence_saver,
        name="ImportPipelineGraph",
    )


# ---------------------------------------------------------------------------
# Helpers para carga sincrónica (compatibilidad con LangGraph Studio)
# ---------------------------------------------------------------------------

def _get_graph(persistence_saver=None):
    """Carga el grafo de forma sincrónica usando un event loop."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(create_graph(persistence_saver))


def load_graph():
    """Punto de entrada público para cargar el grafo principal."""
    try:
        return _get_graph(None)
    except Exception as exc:
        raise RuntimeError("No se pudo crear el grafo del pipeline.", exc)


graph = load_graph()