"""
Subgrafo de Crosswalk y Deduplicación.

Encapsula los pasos 1 a 4 del pipeline original:
  Paso 1 — GenerateSourceCrosswalkConfig (agente LLM)
  Paso 2a — MapSourceToGeneric           (crosswalk fuente → genérico)
  Paso 2b — MapSediciToGeneric           (crosswalk SEDICI → genérico, paralelo)
  Paso 3  — Deduplicate                  (detección de duplicados)
  Paso 4  — MetadataReconciliation       (filtrado y join con metadatos originales)

La ejecución de los pasos 2a y 2b es paralela (ambos dependen de SetupWorkspace
pero son independientes entre sí) y convergen en Deduplicate.

Topología:
  START
    → GenerateSourceCrosswalkConfig
        → MapSourceToGeneric ──┐
    → MapSediciToGeneric ──────┴→ Deduplicate → MetadataReconciliation → END
"""

from langgraph.graph import END, START, StateGraph

from core.state import State
from core.nodes.crosswalk_agent.node import generate_source_crosswalk_config
from core.nodes.pipeline_nodes import (
    deduplicate,
    map_sedici_to_generic,
    map_source_to_generic,
    metadata_reconciliation,
)


async def build_crosswalk_dedup_subgraph():
    """
    Construye y compila el subgrafo de crosswalk y deduplicación.

    Returns:
        Grafo compilado listo para ser añadido como nodo al grafo principal.
    """
    graph = StateGraph(State)

    graph.add_node("GenerateSourceCrosswalkConfig", generate_source_crosswalk_config)
    graph.add_node("MapSourceToGeneric",            map_source_to_generic)
    graph.add_node("MapSediciToGeneric",            map_sedici_to_generic)
    graph.add_node("Deduplicate",                   deduplicate)
    graph.add_node("MetadataReconciliation",        metadata_reconciliation)

    # Pasos 2a y 2b se ejecutan en paralelo desde START
    graph.add_edge(START,                          "GenerateSourceCrosswalkConfig")
    graph.add_edge(START,                          "MapSediciToGeneric")
    graph.add_edge("GenerateSourceCrosswalkConfig","MapSourceToGeneric")

    # Ambos convergen en Deduplicate (LangGraph espera a ambos automáticamente)
    graph.add_edge("MapSourceToGeneric", "Deduplicate")
    graph.add_edge("MapSediciToGeneric", "Deduplicate")

    graph.add_edge("Deduplicate",            "MetadataReconciliation")
    graph.add_edge("MetadataReconciliation", END)

    return graph.compile(name="CrosswalkDedupSubgraph")
