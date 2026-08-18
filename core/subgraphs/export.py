"""
Subgrafo de Exportación.

Encapsula los pasos 5 a 8 del pipeline original:
  Paso 5 — MapToSediciFormat    (crosswalk → formato final SEDICI)
  Paso 6 — MetadataCorrections  (correcciones programáticas por repositorio)
  Paso 7 — GenerateSafToImport  (generación del Simple Archive Format)
  Paso 8 — ImportToDspace       (importación a DSpace via Scripts API)

Es una secuencia lineal sin bifurcaciones. Recibe como entrada el
CSV reconciliado (potencialmente enriquecido por el EnrichmentSubgraph)
y produce la importación en DSpace.

Topología:
  START → MapToSediciFormat → MetadataCorrections
       → GenerateSafToImport → ImportToDspace → END
"""

from langgraph.graph import END, START, StateGraph

from core.state import State
from core.nodes.pipeline_nodes import (
    generate_saf_to_import,
    import_to_dspace,
    map_to_sedici_format,
    metadata_corrections,
)


async def build_export_subgraph():
    """
    Construye y compila el subgrafo de exportación e importación a DSpace.

    Returns:
        Grafo compilado listo para ser añadido como nodo al grafo principal.
    """
    graph = StateGraph(State)

    graph.add_node("MapToSediciFormat",   map_to_sedici_format)
    graph.add_node("MetadataCorrections", metadata_corrections)
    graph.add_node("GenerateSafToImport", generate_saf_to_import)
    graph.add_node("ImportToDspace",      import_to_dspace)

    graph.add_edge(START,                "MapToSediciFormat")
    graph.add_edge("MapToSediciFormat",  "MetadataCorrections")
    graph.add_edge("MetadataCorrections","GenerateSafToImport")
    graph.add_edge("GenerateSafToImport","ImportToDspace")
    graph.add_edge("ImportToDspace",     END)

    return graph.compile(name="ExportSubgraph")
