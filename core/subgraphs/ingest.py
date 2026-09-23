"""
Subgrafo de Ingesta.

Normaliza las distintas fuentes de entrada a un CSV de metadatos
unificado para que el resto del pipeline pueda consumirlo de forma
transparente.

Flujos:
  - "csv":       state["source_csv_path"] ya existe → valida estructura/integridad con ValidateInputCSVs → END.
  - "pdf_minio": descarga PDFs desde MinIO, extrae metadatos, cura el CSV y valida con ValidateInputCSVs.

Topología:
  START
    ├─ (csv)       → ValidateInputCSVs → END
    └─ (pdf_minio) → PDFIngest → CurateMetadata → ValidateInputCSVs → END

La curación solo se aplica cuando los metadatos fueron generados
automáticamente desde PDFs, ya que los CSV provistos por el usuario
se asumen correctos.
"""

from langgraph.graph import END, START, StateGraph

from core.state import State
from core.nodes.ingest_nodes import pdf_ingest_node, route_input_source
from core.nodes.curation_nodes import curate_metadata_node
from core.nodes.validation_node import validate_input_csvs_node


async def build_ingest_subgraph():
    """
    Construye y compila el subgrafo de ingesta con validación temprana de CSVs.

    Returns:
        Grafo compilado listo para ser añadido como nodo al grafo principal.
    """
    graph = StateGraph(State)

    graph.add_node("PDFIngest", pdf_ingest_node)
    graph.add_node("CurateMetadata", curate_metadata_node)
    graph.add_node("ValidateInputCSVs", validate_input_csvs_node)

    # El enrutamiento se hace directamente desde START con conditional_edges.
    graph.add_conditional_edges(
        START,
        route_input_source,
        {
            "csv":       "ValidateInputCSVs",
            "pdf_minio": "PDFIngest",
        },
    )
    graph.add_edge("PDFIngest", "CurateMetadata")
    graph.add_edge("CurateMetadata", "ValidateInputCSVs")
    graph.add_edge("ValidateInputCSVs", END)

    return graph.compile(name="IngestSubgraph")
