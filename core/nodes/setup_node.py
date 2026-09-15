"""
Nodo SetupWorkspace — Paso 0 del pipeline.

Inicializa el espacio de trabajo del lote y completa todos los paths y
configuraciones opcionales con sus valores por defecto.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from langsmith import traceable

from core.state import DEFAULT_CONFIGS_DIR

logger = logging.getLogger(__name__)


@traceable(name="SetupWorkspace", run_type="chain")
def setup_workspace(state: dict) -> dict[str, Any]:
    """
    Paso 0 — Inicialización del espacio de trabajo (workspace).

    Asegura que exista un directorio de lote único `{source_name}_{fecha}_{cant}`
    y completa cualquier ruta o configuración opcional con su valor por defecto.
    """
    source_name = state.get("source_name", "default_source")
    fecha_actual = datetime.now().strftime("%Y%m%d")

    workspace_dir = state.get("workspace_dir")
    if not workspace_dir:
        workspace_dir = _build_workspace_dir(source_name, fecha_actual)

    os.makedirs(workspace_dir, exist_ok=True)

    updates = _build_defaults(state, workspace_dir)
    logger.info("[SetupWorkspace] Workspace listo en '%s'", workspace_dir)
    return updates


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _build_workspace_dir(source_name: str, fecha: str) -> str:
    """Genera el path del directorio de ejecución del lote."""
    base_runs_dir = os.path.abspath("runs")
    cant_run = 1
    if os.path.exists(base_runs_dir):
        existing_runs = [
            d for d in os.listdir(base_runs_dir)
            if d.startswith(f"{source_name}_{fecha}_")
        ]
        cant_run = len(existing_runs) + 1
    return os.path.join(base_runs_dir, f"{source_name}_{fecha}_{cant_run}")


def _build_defaults(state: dict, workspace_dir: str) -> dict[str, Any]:
    """Construye el dict de updates con todos los paths y umbrales por defecto."""
    default_sedici_config = os.path.join(DEFAULT_CONFIGS_DIR, "export_10915_crosswalkconfig.json")
    default_sedici_target_config = os.path.join(DEFAULT_CONFIGS_DIR, "config_romero_to_sedici.json")

    def _path(key: str, filename: str) -> str:
        return state.get(key) or os.path.join(workspace_dir, filename)

    return {
        "workspace_dir":                workspace_dir,
        "sedici_crosswalk_config":       state.get("sedici_crosswalk_config")        or default_sedici_config,
        "sedici_target_crosswalk_config":state.get("sedici_target_crosswalk_config") or default_sedici_target_config,
        "generic_source_csv_path":       _path("generic_source_csv_path",  "generic_source.csv"),
        "generic_sedici_csv_path":       _path("generic_sedici_csv_path",  "generic_sedici.csv"),
        "dedup_output_csv_path":         _path("dedup_output_csv_path",    "dedup.csv"),
        "reconciled_csv_path":           _path("reconciled_csv_path",      "reconciled.csv"),
        "sedici_ready_csv_path":         _path("sedici_ready_csv_path",    "sedici_ready.csv"),
        "saf_output_path":               _path("saf_output_path",          "saf_output"),
        "import_mapfile_path":           _path("import_mapfile_path",      "mapfile.txt"),
        "umbral_seguro":                 state.get("umbral_seguro")  or 10,
        "umbral_revision":               state.get("umbral_revision") or 30,
    }
