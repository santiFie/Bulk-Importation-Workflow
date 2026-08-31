"""
Definición del State compartido del grafo LangGraph.

Este módulo centraliza el TypedDict State para evitar importaciones
circulares entre los nodos del grafo y facilitar su reutilización.
"""

import os
from datetime import datetime
from typing import Annotated, Literal, Optional, TypedDict
try:
    from typing import NotRequired
except ImportError:
    from typing_extensions import NotRequired

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


# Ruta por defecto para configuraciones de crosswalk
DEFAULT_CONFIGS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "scripts", "crosswalk", "configs")
)


class State(TypedDict):
    """
    Estado compartido del pipeline de importación a SEDICI.

    Cada campo representa un artefacto producido o consumido por un
    nodo del grafo. Los campos obligatorios se definen directamente
    y los opcionales se marcan como NotRequired.
    """

    messages: Annotated[list[BaseMessage], add_messages]

    # --- Paso 1: Inputs Obligatorios (Required) ---
    repository_csv_path: str          # CSV exportado de SEDICI (ej. export_10915_all.csv)
    source_csv_path: str              # CSV de ítems a importar (ej. result-14531-Romero.csv)
    source_name: str                  # Nombre del repositorio origen, ej. "unlp_doaj"
    dspace_collection: str            # Handle o ID de la colección destino en SEDICI (ej. "123456789/5")
    import_validate_only: bool        # Si es True, solo valida la importación sin efectuar cambios permanentes
    input_source_type: Literal["csv", "pdf_minio"]  # Tipo de fuente: CSV directo o PDFs en MinIO

    # --- MinIO ---
    minio_bucket: NotRequired[str]    # Bucket de MinIO donde están los PDFs
    minio_prefix: NotRequired[str]    # Prefijo (carpeta) dentro del bucket

    # --- Enriquecimiento de Metadatos ---
    enrichment_enabled: NotRequired[bool]         # Habilita el enriquecimiento post-deduplicación
    enrichment_stats: NotRequired[dict]           # Estadísticas: {total, enriched, skipped, errors} TODO: Revisar si esto es necesario

    # --- Control del Pipeline ---
    pipeline_status: NotRequired[Literal["running", "paused_for_review", "failed", "completed"]]
    node_errors: NotRequired[dict[str, str]]      # {nombre_nodo: descripción_del_error}

    # --- Directorio de Ejecución (Workspace) ---
    workspace_dir: NotRequired[str]   # Ruta de la carpeta del lote: runs/{source_name}_{fecha}_{cant}

    # --- Paso 2: Crosswalk configs y outputs intermedios (Opcionales / Derivados) ---
    source_crosswalk_config: NotRequired[str]        # JSON de crosswalk para el repositorio origen → formato genérico (puede ser generado por agente)
    sedici_crosswalk_config: NotRequired[str]        # JSON de crosswalk para SEDICI → formato genérico
    sedici_target_crosswalk_config: NotRequired[str] # JSON de crosswalk origen → formato SEDICI

    generic_source_csv_path: NotRequired[str]       # CSV del repositorio origen en formato genérico
    generic_sedici_csv_path: NotRequired[str]       # CSV de SEDICI en formato genérico

    # --- Paso 3: Deduplicación ---
    dedup_output_csv_path: NotRequired[str]         # CSV de resultado del deduplicador

    # --- Paso 4: Reconciliación de metadatos ---
    reconciled_csv_path: NotRequired[str]           # CSV con ítems a importar reconciliados

    # --- Paso 5: Mapeo a formato SEDICI ---
    sedici_ready_csv_path: NotRequired[str]          # CSV final listo para importar a SEDICI

    # --- Umbrales de aceptación para deduplicación ---
    umbral_seguro: NotRequired[int]                 # Porcentaje por debajo del cual se considera seguro (default: 10)
    umbral_revision: NotRequired[int]               # Porcentaje por encima del cual se descarta (default: 30)

    # --- Paso 8: SAF output ---
    saf_output_path: NotRequired[str]               # Directorio donde se generará el SAF (Simple Archive Format)

    # --- Paso 9: Importación a DSpace ---
    import_mapfile_path: NotRequired[str]           # Path local donde se guardará el mapfile generado por DSpace
    import_exclude_bitstreams: NotRequired[bool]    # Si es True, excluye bitstreams del proceso de importación

    # --- Paso 1b: Curación de Metadatos PDF ---
    curated_csv_path: NotRequired[str]              # CSV curado generado por CurateMetadata (no sobreescribe source_csv_path)
    curation_stats: NotRequired[dict]               # Estadísticas del proceso de curación: {total, limpias, curadas, marcadas, errores}