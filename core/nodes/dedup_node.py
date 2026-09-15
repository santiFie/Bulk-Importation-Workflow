"""
Nodo Deduplicate — Paso 3 del pipeline.

Envía los dos CSVs en formato genérico al Deduplicador para detectar
posibles duplicados entre los ítems a importar y los ítems en SEDICI.
"""

from __future__ import annotations

import csv
import logging
from typing import Any

import pandas as pd
from langsmith import traceable

from core.clients.deduplicator_client import DeduplicatorClient
from core.nodes.crosswalk_nodes import _save_csv

logger = logging.getLogger(__name__)


@traceable(name="Deduplicate", run_type="chain")
def deduplicate(state: dict) -> dict[str, Any]:
    """
    Paso 3 — Deduplicación de ítems a importar contra SEDICI.

    El Deduplicador devuelve un CSV con los identificadores de los documentos
    del repositorio origen y sus posibles duplicados en SEDICI, junto con
    un porcentaje de similitud para cada detección.
    """
    source_name = state.get("source_name", "origen")
    logger.info(
        "[Deduplicate] Iniciando deduplicación para '%s'...", source_name
    )

    _log_csv_columns(state["generic_sedici_csv_path"], "SEDICI")
    _log_csv_columns(state["generic_source_csv_path"], "origen")

    client = DeduplicatorClient()
    csv_bytes = client.detect_duplicates(
        csv_file1_path=state["generic_sedici_csv_path"],
        csv_file2_path=state["generic_source_csv_path"],
        source_name=source_name,
    )
    result = _save_csv(csv_bytes, state["dedup_output_csv_path"])
    logger.info("[Deduplicate] %s", result)
    return {}


# ---------------------------------------------------------------------------
# Helper interno
# ---------------------------------------------------------------------------

def _log_csv_columns(csv_path: str, label: str) -> None:
    """Registra en el log las columnas del CSV para diagnóstico."""
    try:
        with open(csv_path, newline="", encoding="utf-8") as fh:
            fieldnames = csv.DictReader(fh).fieldnames
        logger.debug("[Deduplicate] Columnas CSV %s: %s", label, fieldnames)
    except Exception as exc:
        logger.warning("[Deduplicate] No se pudo leer columnas de '%s': %s", csv_path, exc)
