"""
Subgrafo de Crosswalk y Deduplicación.

Encapsula los pasos 1 a 4 del pipeline:
  Paso 1  — GenerateSourceCrosswalkConfig (agente LLM, si no viene de PDFs)
  Paso 2a — MapSourceToGeneric           (crosswalk fuente → genérico)
  Paso 2b — MapSediciToGeneric           (crosswalk SEDICI → genérico, paralelo)
  Paso 2c — EnrichmentSubgraph          (enriquecimiento opcional sobre formato genérico)
  Paso 3  — Deduplicate                  (detección de duplicados sobre genéricos enriquecidos)
  Paso 4  — MetadataReconciliation       (filtrado y join con metadatos originales)

Topología:
  START
    ├─ (crosswalk LLM) → GenerateSourceCrosswalkConfig → MapSourceToGeneric ──┐
    ├─ (pdf minio)     → BypassSourceCrosswalk ───────────────────────────────┴→ EnrichmentSubgraph ──┐
    └─ MapSediciToGeneric ────────────────────────────────────────────────────────────────────────────┴→ Deduplicate → MetadataReconciliation → END
"""

import shutil

from langgraph.graph import END, START, StateGraph

from core.state import State
from core.nodes.crosswalk_agent.node import generate_source_crosswalk_config
from core.nodes.pipeline_nodes import (
    deduplicate,
    map_sedici_to_generic,
    map_source_to_generic,
    metadata_reconciliation,
)
from core.subgraphs.enrichment import build_enrichment_subgraph


def route_source_crosswalk(state: State) -> str:
    """Decide si se requiere generar un crosswalk mediante LLM para la fuente."""
    if state.get("input_source_type") == "pdf_minio":
        return "BypassSourceCrosswalk"
    return "GenerateSourceCrosswalkConfig"


async def bypass_source_crosswalk(state: State) -> dict:
    """Nodo puente: Si el CSV ya está en formato genérico, simplemente lo copia."""
    source_path = state.get("curated_csv_path") or state["source_csv_path"]
    shutil.copy(source_path, state["generic_source_csv_path"])
    return {}


async def build_crosswalk_dedup_subgraph():
    """
    Construye y compila el subgrafo de crosswalk y deduplicación con enriquecimiento.

    Returns:
        Grafo compilado listo para ser añadido como nodo al grafo principal.
    """
    enrichment_sg = await build_enrichment_subgraph()

    graph = StateGraph(State)

    graph.add_node("GenerateSourceCrosswalkConfig", generate_source_crosswalk_config)
    graph.add_node("MapSourceToGeneric",            map_source_to_generic)
    graph.add_node("BypassSourceCrosswalk",         bypass_source_crosswalk)
    graph.add_node("EnrichmentSubgraph",            enrichment_sg)
    graph.add_node("MapSediciToGeneric",            map_sedici_to_generic)
    graph.add_node("Deduplicate",                   deduplicate)
    graph.add_node("MetadataReconciliation",        metadata_reconciliation)

    # Rutas Condicionales para el flujo de la fuente
    graph.add_conditional_edges(START, route_source_crosswalk, {
        "GenerateSourceCrosswalkConfig": "GenerateSourceCrosswalkConfig",
        "BypassSourceCrosswalk": "BypassSourceCrosswalk"
    })
    
    # Flujo de SEDICI
    graph.add_edge(START,                          "MapSediciToGeneric")
    
    graph.add_edge("GenerateSourceCrosswalkConfig","MapSourceToGeneric")

    # Ambas rutas de la fuente generan generic_source.csv y pasan por EnrichmentSubgraph
    graph.add_edge("MapSourceToGeneric",    "EnrichmentSubgraph")
    graph.add_edge("BypassSourceCrosswalk", "EnrichmentSubgraph")

    # EnrichmentSubgraph y MapSediciToGeneric convergen en Deduplicate
    graph.add_edge("EnrichmentSubgraph",    "Deduplicate")
    graph.add_edge("MapSediciToGeneric",    "Deduplicate")

    graph.add_edge("Deduplicate",            "MetadataReconciliation")
    graph.add_edge("MetadataReconciliation", END)

    return graph.compile(name="CrosswalkDedupSubgraph")
