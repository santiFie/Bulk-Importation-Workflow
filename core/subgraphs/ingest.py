"""
Subgrafo de Ingesta.

Normaliza las distintas fuentes de entrada a un CSV de metadatos
unificado para que el resto del pipeline pueda consumirlo de forma
transparente.

Flujos:
  - "csv":       state["source_csv_path"] ya existe → pasa directo (END).
  - "pdf_minio": descarga PDFs desde MinIO y extrae metadatos → genera CSV.

Topología:
  START
    ├─ (csv)       → END
    └─ (pdf_minio) → PDFIngest → END
"""

from langgraph.graph import END, START, StateGraph

from core.state import State
from core.nodes.ingest_nodes import pdf_ingest_node, route_input_source


async def build_ingest_subgraph():
    """
    Construye y compila el subgrafo de ingesta.

    Returns:
        Grafo compilado listo para ser añadido como nodo al grafo principal.
    """
    graph = StateGraph(State)

    graph.add_node("PDFIngest", pdf_ingest_node)

    # El enrutamiento se hace directamente desde START con conditional_edges.
    # Para el flujo "csv" no hay ningún nodo que ejecutar; va directo a END.
    graph.add_conditional_edges(
        START,
        route_input_source,
        {
            "csv":       END,
            "pdf_minio": "PDFIngest",
        },
    )
    graph.add_edge("PDFIngest", END)

    return graph.compile(name="IngestSubgraph")
