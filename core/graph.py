"""
Punto de entrada del grafo LangGraph del pipeline de importación a SEDICI.

Este módulo ensambla el StateGraph con los nodos definidos en core/nodes/
y expone el objeto `graph` que LangGraph Studio utiliza para ejecutar
y visualizar el pipeline.

Pipeline:
  START
    → GenerateSourceCrosswalkConfig  (Paso 1)
    → MapSourceToGeneric             (Paso 2a crosswalk)
    → MapSediciToGeneric             (Paso 2b — en paralelo con el anterior)
    → Deduplicate                    (Paso 3)
    → MetadataReconciliation         (Paso 4)
    → MapToSediciFormat              (Paso 5)
    → MetadataCorrections            (Paso 6)
    → GenerateSafToImport            (Paso 8)
    → ImportToDspace                 (Paso 9)
  END
"""

import asyncio
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.graph import END, START, StateGraph

from core.utils.config import config
from core.agent.dspace_agent import build_dspace_agent_workflow

# Re-exportaciones: permiten que los tests existentes importen desde core.graph
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
# Nodo del agente de DSpace (conexión lazy al MCP)
# ---------------------------------------------------------------------------

async def dspace_agent_node(state: State) -> dict[str, Any]:
    """
    Nodo DspaceAgent — conecta al MCP de DSpace de forma lazy (en tiempo de
    ejecución) para evitar fallos de DNS durante la carga del módulo.
    """
    dspace_client = MultiServerMCPClient(
        {
            "DspaceMCP": {
                "url": config.DSPACE_MCP_URL,
                "transport": "sse",
            }
        }
    )
    dspace_tools = await dspace_client.get_tools(server_name="DspaceMCP")
    dspace_graph = await build_dspace_agent_workflow(dspace_tools)
    return await dspace_graph.ainvoke(state)


# ---------------------------------------------------------------------------
# Registro ordenado de pasos del pipeline
# ---------------------------------------------------------------------------

PIPELINE_STEPS: list[tuple[str, str]] = [
    # (nombre_paso, nombre_nodo_en_el_grafo)
    ("Paso 1 - GenerateSourceCrosswalkConfig", "GenerateSourceCrosswalkConfig"),
    ("Paso 2a - MapSourceToGeneric",           "MapSourceToGeneric"),
    ("Paso 2b - MapSediciToGeneric",           "MapSediciToGeneric"),
    ("Paso 3 - Deduplicate",                   "Deduplicate"),
    ("Paso 4 - MetadataReconciliation",        "MetadataReconciliation"),
    ("Paso 5 - MapToSediciFormat",             "MapToSediciFormat"),
    ("Paso 6 - MetadataCorrections",           "MetadataCorrections"),
    ("Paso 7 - GenerateSafToImport",           "GenerateSafToImport"),
    ("Paso 8 - ImportToDspace",                "ImportToDspace"),
]
"""
Registro ordenado de los pasos del pipeline con su nombre legible
y el nombre del nodo correspondiente en el StateGraph.

Se usa en tests y scripts de evaluación para poder ejecutar el
pipeline hasta un paso determinado.
"""


def get_step_node_names() -> list[str]:
    """Devuelve la lista ordenada de nombres de nodos del pipeline."""
    return [node_name for _, node_name in PIPELINE_STEPS]


def get_step_label(node_name: str) -> str:
    """Devuelve la etiqueta legible de un paso dado su nombre de nodo."""
    for label, name in PIPELINE_STEPS:
        if name == node_name:
            return label
    return node_name


# ---------------------------------------------------------------------------
# Mapa de nodos → funciones (para ejecución parcial)
# ---------------------------------------------------------------------------

_NODE_FUNCTIONS: dict[str, Any] = {
    "GenerateSourceCrosswalkConfig": generate_source_crosswalk_config,
    "MapSourceToGeneric":            map_source_to_generic,
    "MapSediciToGeneric":            map_sedici_to_generic,
    "Deduplicate":                   deduplicate,
    "MetadataReconciliation":        metadata_reconciliation,
    "MapToSediciFormat":             map_to_sedici_format,
    "MetadataCorrections":           metadata_corrections,
    "GenerateSafToImport":           generate_saf_to_import,
    "ImportToDspace":                import_to_dspace,
}
"""
Mapeo de nombre de nodo a la función que lo implementa.
Se utiliza para la ejecución secuencial paso a paso en los tests
de evaluación.
"""


def run_pipeline_until_step(state: dict, stop_after: str) -> dict[str, dict]:
    """
    Ejecuta el pipeline secuencialmente hasta el paso indicado (inclusive).

    A diferencia de compilar un subgrafo, esta función ejecuta las funciones
    de los nodos directamente en orden, lo cual es más simple y predecible
    para tests de evaluación.

    Args:
        state:      diccionario con el estado inicial del pipeline.
        stop_after: nombre del nodo en el que se detiene (inclusive).
                    Debe coincidir con una clave de PIPELINE_STEPS.

    Returns:
        Diccionario ``{nombre_nodo: resultado_dict}`` con el resultado
        devuelto por cada nodo ejecutado. Si un nodo lanza una excepción,
        se captura y se almacena en la clave ``"__error__"`` del resultado.

    Raises:
        ValueError: si *stop_after* no es un nombre de nodo válido.
    """
    ordered_nodes = get_step_node_names()
    if stop_after not in ordered_nodes:
        valid = ", ".join(ordered_nodes)
        raise ValueError(
            f"Paso '{stop_after}' no reconocido. Valores válidos: {valid}"
        )

    results: dict[str, dict] = {}
    for node_name in ordered_nodes:
        fn = _NODE_FUNCTIONS[node_name]
        try:
            result = fn(state)
            results[node_name] = result if isinstance(result, dict) else {}
            # Mergear el resultado al state para que los pasos siguientes lo vean
            if isinstance(result, dict):
                state.update(result)
        except Exception as exc:
            results[node_name] = {"__error__": repr(exc)}
            break  # Detenemos la ejecución en caso de error

        if node_name == stop_after:
            break

    return results


# ---------------------------------------------------------------------------
# Construcción del grafo
# ---------------------------------------------------------------------------

async def create_graph(persistence_saver):
    """
    Crea el grafo supervisor que coordina el pipeline de importación
    para detectar duplicados e importar ítems a SEDICI.

    Pasos:
      START
        → generate_source_crosswalk_config
          (Paso 1 agente: analiza CSV fuente y genera crosswalk config)
        → map_source_to_generic
          (Paso 2a crosswalk: origen → genérico usando el config generado)
        → map_sedici_to_generic   (Paso 2b: crosswalk SEDICI → genérico, paralelo)
        → deduplicate             (Paso 3: detección de duplicados)
        → metadata_reconciliation (Paso 4: join con metadatos originales)
        → map_to_sedici_format    (Paso 5: crosswalk origen → formato SEDICI)
        → metadata_corrections    (Paso 6: correcciones programáticas por repositorio)
        → generate_saf_to_import  (Paso 8: generación del SAF)
      END

    Nota: La conexión al MCP de DSpace se realiza de forma lazy dentro del
    nodo DspaceAgent para evitar errores de resolución DNS durante la carga
    del módulo cuando el servidor MCP no está disponible.
    """
    graph = StateGraph(State)

    # ── Nodos ──────────────────────────────────────────────────────────────────
    graph.add_node("DspaceAgent", dspace_agent_node)              # Lazy MCP connection
    graph.add_node("GenerateSourceCrosswalkConfig", generate_source_crosswalk_config)  # Paso 1 (agente opcional)
    graph.add_node("MapSourceToGeneric", map_source_to_generic)   # Paso 2a (crosswalk)
    graph.add_node("MapSediciToGeneric", map_sedici_to_generic)   # Paso 2b
    graph.add_node("Deduplicate", deduplicate)                    # Paso 3
    graph.add_node("MetadataReconciliation", metadata_reconciliation)  # Paso 4
    graph.add_node("MapToSediciFormat", map_to_sedici_format)    # Paso 5
    graph.add_node("MetadataCorrections", metadata_corrections)  # Paso 6
    graph.add_node("GenerateSafToImport", generate_saf_to_import)  # Paso 7
    graph.add_node("ImportToDspace", import_to_dspace)           # Paso 8

    # ── Aristas ────────────────────────────────────────────────────────────────
    # GenerateSourceCrosswalkConfig analiza el CSV fuente y genera el config JSON.
    # MapSourceToGeneric depende del config generado, por eso va después.
    graph.add_edge(START, "GenerateSourceCrosswalkConfig")
    graph.add_edge("GenerateSourceCrosswalkConfig", "MapSourceToGeneric")

    # MapSediciToGeneric es independiente y corre en paralelo con el agente
    graph.add_edge(START, "MapSediciToGeneric")

    # Una vez que ambos CSVs genéricos están listos, se ejecuta la deduplicación
    graph.add_edge("MapSourceToGeneric", "Deduplicate")
    graph.add_edge("MapSediciToGeneric", "Deduplicate")

    # Secuencia posterior
    graph.add_edge("Deduplicate", "MetadataReconciliation")
    graph.add_edge("MetadataReconciliation", "MapToSediciFormat")
    graph.add_edge("MapToSediciFormat", "MetadataCorrections")   # Paso 6
    graph.add_edge("MetadataCorrections", "GenerateSafToImport")
    graph.add_edge("GenerateSafToImport", "ImportToDspace")      # Paso 8
    graph.add_edge("ImportToDspace", END)

    return graph.compile(checkpointer=persistence_saver, name="ImportPipelineGraph")


def _get_graph(persistence_saver=None):
    """Devuelve el grafo del pipeline para uso sincrónico."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(create_graph(persistence_saver))


def load_graph():
    try:
        return _get_graph(None)
    except Exception as exc:
        raise RuntimeError("Failed to create the supervisor graph", exc)


graph = load_graph()