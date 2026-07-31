"""
Definición del State compartido del grafo LangGraph.

Este módulo centraliza el TypedDict State para evitar importaciones
circulares entre los nodos del grafo y facilitar su reutilización.
"""

from typing import Annotated, Optional, TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class State(TypedDict):
    """
    Estado compartido del pipeline de importación a SEDICI.

    Cada campo representa un artefacto producido o consumido por un
    nodo del grafo. Los pasos están numerados según el orden del pipeline.
    """

    messages: Annotated[list[BaseMessage], add_messages]

    # Paso 1 — Inputs
    repository_csv_path: str          # CSV exportado de SEDICI (export_10915_all.csv)
    source_csv_path: str              # CSV de ítems a importar (result-14531-Romero.csv)
    source_name: str                  # Nombre del repositorio origen, e.g. "SEDICI"

    # Paso 2 — Crosswalk configs y outputs intermedios
    source_crosswalk_config: str       # JSON de crosswalk para el repositorio origen → formato genérico
    sedici_crosswalk_config: str      # JSON de crosswalk para SEDICI → formato genérico
    generic_source_csv_path: str      # CSV del repositorio origen en formato genérico
    generic_sedici_csv_path: str      # CSV de SEDICI en formato genérico

    # Paso 3 — Deduplicación
    dedup_output_csv_path: str        # CSV de resultado del deduplicador

    # Paso 4 — Reconciliación de metadatos
    reconciled_csv_path: str          # CSV con ítems a importar reconciliados

    # Paso 5 — Mapeo a formato SEDICI
    sedici_target_crosswalk_config: str  # JSON de crosswalk origen → formato SEDICI
    sedici_ready_csv_path: str           # CSV final listo para importar a SEDICI

    # Umbrales de aceptación para deduplicación (Paso 3)
    umbral_seguro: Optional[int]      # Porcentaje por debajo del cual se considera seguro (no duplicado)
    umbral_revision: Optional[int]    # Porcentaje por encima del cual se descarta

    # Paso 8 — SAF output
    saf_output_path: str              # Directorio donde se generará el SAF (Simple Archive Format)

    # Paso 9 — Importación a DSpace
    dspace_collection: str            # Handle o ID de la colección destino en SEDICI (e.g. "123456789/5")
    import_mapfile_path: str          # Path local donde se guardará el mapfile generado por DSpace
    import_validate_only: Optional[bool]  # Si True, solo valida sin importar realmente
