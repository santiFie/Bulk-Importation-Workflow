"""
Subgrafo de Enriquecimiento de Metadatos.

Ejecutado después de la deduplicación. Consulta fuentes externas (Crossref,
OpenAlex) para completar los metadatos de los ítems a importar.

El enriquecimiento es opcional y se activa con state["enrichment_enabled"] = True.

Estrategia de enriquecimiento (árbol de decisión por ítem):
  Tiene DOI         → Crossref  (fuente autoritativa)
  Sin DOI, ISSN     → OpenAlex  (por ISSN)
  Sin DOI ni ISSN   → OpenAlex  (por título, baja confianza)
  Sin ningún campo  → omitir

Los campos enriquecidos se escriben como columnas adicionales en el CSV
reconciliado (in-place), manteniendo compatibilidad con el ExportSubgraph.

Topología:
  START
    ├─ (skip)   → END
    └─ (enrich) → EnrichMetadata → END
"""

from langgraph.graph import END, START, StateGraph

from core.state import State
from core.nodes.enrichment_nodes import enrich_metadata_node, route_enrichment


async def build_enrichment_subgraph():
    """
    Construye y compila el subgrafo de enriquecimiento de metadatos.

    Returns:
        Grafo compilado listo para ser añadido como nodo al grafo principal.
    """
    graph = StateGraph(State)

    graph.add_node("EnrichMetadata", enrich_metadata_node)

    graph.add_conditional_edges(
        START,
        route_enrichment,
        {
            "enrich": "EnrichMetadata",
            "skip":   END,
        },
    )
    graph.add_edge("EnrichMetadata", END)

    return graph.compile(name="EnrichmentSubgraph")
