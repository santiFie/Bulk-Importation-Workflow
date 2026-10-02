"""
Subgrafo de Crosswalk y Deduplicación.

Encapsula los pasos 1 a 4 del pipeline:
  Paso 1  — GenerateSourceCrosswalkConfig (agente LLM, si no viene de PDFs)
  Paso 2a — MapSourceToGeneric            (crosswalk fuente → genérico)
  Paso 2b — MapSediciToGeneric            (crosswalk SEDICI → genérico, paralelo)
  Paso 2c — EnrichmentSubgraph            (enriquecimiento opcional sobre formato genérico)
  Paso 3a — PreDedupSanitizer             (sanitización sobre genéricos enriquecidos)
  Paso 3b — Deduplicate                   (detección de duplicados sobre genéricos enriquecidos)
  Paso 3c — DedupRecoveryNode             (recuperación reactiva de errores de deduplicación)
  Paso 4  — MetadataReconciliation        (filtrado y join con metadatos originales)

Topología:
  START
    ├─ (crosswalk LLM) → GenerateSourceCrosswalkConfig → MapSourceToGeneric ──┐
    └─ (pdf minio)     → BypassSourceCrosswalk ───────────────────────────────┴→ EnrichmentSubgraph → MapSediciToGeneric → Deduplicate → MetadataReconciliation → END
"""

import shutil

from langgraph.graph import END, START, StateGraph
from langsmith import traceable

from core.state import State
from core.nodes.source_to_generic.node import generate_source_crosswalk_config
from core.nodes.pipeline_nodes import (
    deduplicate,
    map_sedici_to_generic,
    map_source_to_generic,
    metadata_reconciliation,
)
from core.nodes.sanitizer_node import pre_dedup_sanitizer
from core.agent.dedup_recovery.agent import dedup_recovery_node
from core.subgraphs.enrichment import build_enrichment_subgraph


def route_source_crosswalk(state: State) -> str:
    """Decide si se requiere generar un crosswalk mediante LLM para la fuente."""
    if state.get("input_source_type") == "pdf_minio":
        return "BypassSourceCrosswalk"
    return "GenerateSourceCrosswalkConfig"


def route_post_deduplicate(state: State) -> str:
    """
    Evalúa el resultado de la deduplicación.
    Si falló y aún no superó el reintento permitido (1), encamina a recuperación reactiva.
    """
    if state.get("node_errors", {}).get("Deduplicate"):
        if state.get("dedup_retry_count", 0) < 1:
            return "DedupRecoveryNode"
        return END
    return "MetadataReconciliation"


def route_post_recovery(state: State) -> str:
    """
    Evalúa el resultado del agente de recuperación.
    Si la anomalía fue subsanada, reintenta Deduplicate; caso contrario, finaliza.
    """
    if state.get("pipeline_status") == "failed":
        return END
    if not state.get("node_errors", {}).get("Deduplicate"):
        return "Deduplicate"
    return END


@traceable(name="BypassSourceCrosswalk", run_type="chain")
async def bypass_source_crosswalk(state: State) -> dict:
    """Nodo puente: Si el CSV ya está en formato genérico, simplemente lo copia."""
    source_path = state.get("curated_csv_path") or state["source_csv_path"]
    shutil.copy(source_path, state["generic_source_csv_path"])
    return {}


async def build_crosswalk_dedup_subgraph():
    """
    Construye y compila el subgrafo de crosswalk y deduplicación con enriquecimiento,
    sanitización preventiva y recuperación reactiva con memoria episódica.

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
    graph.add_node("PreDedupSanitizer",             pre_dedup_sanitizer)
    graph.add_node("Deduplicate",                   deduplicate)
    graph.add_node("DedupRecoveryNode",             dedup_recovery_node)
    graph.add_node("MetadataReconciliation",        metadata_reconciliation)

    # Rutas Condicionales para el flujo de la fuente
    graph.add_conditional_edges(START, route_source_crosswalk, {
        "GenerateSourceCrosswalkConfig": "GenerateSourceCrosswalkConfig",
        "BypassSourceCrosswalk": "BypassSourceCrosswalk"
    })
    
    graph.add_edge("GenerateSourceCrosswalkConfig","MapSourceToGeneric")

    # Ambas rutas de la fuente generan generic_source.csv y pasan por EnrichmentSubgraph
    graph.add_edge("MapSourceToGeneric",    "EnrichmentSubgraph")
    graph.add_edge("BypassSourceCrosswalk", "EnrichmentSubgraph")

    # Tras procesar y enriquecer la fuente, se mapea SEDICI
    graph.add_edge("EnrichmentSubgraph",    "MapSediciToGeneric")
    
    # Capa preventiva: sanitiza generic_source antes de la deduplicación
    graph.add_edge("MapSediciToGeneric",    "PreDedupSanitizer")
    graph.add_edge("PreDedupSanitizer",     "Deduplicate")

    # Borde condicional reactivo post-deduplicación
    graph.add_conditional_edges(
        "Deduplicate",
        route_post_deduplicate,
        {
            "MetadataReconciliation": "MetadataReconciliation",
            "DedupRecoveryNode":      "DedupRecoveryNode",
            END:                      END,
        },
    )

    # Borde condicional post-recuperación (reintento o abortar)
    graph.add_conditional_edges(
        "DedupRecoveryNode",
        route_post_recovery,
        {
            "Deduplicate": "Deduplicate",
            END:           END,
        },
    )

    graph.add_edge("MetadataReconciliation", END)

    return graph.compile(name="CrosswalkDedupSubgraph")
