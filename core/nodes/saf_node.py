"""
Nodo GenerateSafToImport — Paso 8 del pipeline.

Lee el CSV listo para SEDICI y genera un Simple Archive Format (SAF)
en saf_output_path usando el script dspace-csv-archive.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# Columna requerida por DspaceArchive (puede estar vacía si no hay bitstreams)
_SAF_FILES_COLUMN = "files"

# Path relativo al directorio del script dspace-csv-archive
_SAF_SCRIPT_SUBPATH = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "dspace-csv-archive-master"
)


def generate_saf_to_import(state: dict) -> dict[str, Any]:
    """
    Paso 8 — Generación del SAF a importar.

    Lee el CSV listo para SEDICI (sedici_ready_csv_path) y genera un
    Simple Archive Format (SAF) en saf_output_path usando el script
    dspace-csv-archive.

    El SAF generado contiene, por cada ítem:
      - item_001/, item_002/, ...
          - dublin_core.xml  (metadatos en XML de DSpace)
          - contents         (lista de bitstreams)
    """
    csv_path = state["sedici_ready_csv_path"]
    saf_output_path = state["saf_output_path"]

    if not os.path.isfile(csv_path):
        logger.error("[GenerateSAF] CSV no encontrado: '%s'", csv_path)
        return {}

    output_dir = os.path.dirname(saf_output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    df = _prepare_dataframe(csv_path)
    temp_csv = os.path.join(output_dir or ".", "_saf_input.csv")
    df.to_csv(temp_csv, index=False)

    _write_saf(temp_csv, saf_output_path)
    return {}


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _prepare_dataframe(csv_path: str) -> pd.DataFrame:
    """
    Lee el CSV y asegura que exista la columna 'files' requerida por DspaceArchive.

    El DspaceArchive exige una columna 'files' (aunque esté vacía) como primera
    columna del CSV, que lista los bitstreams de cada ítem.
    """
    df = pd.read_csv(csv_path)
    if _SAF_FILES_COLUMN not in df.columns:
        df.insert(0, _SAF_FILES_COLUMN, "")
    return df


def _load_dspace_archive_module():
    """
    Carga el módulo `dspacearchive` via importlib.

    El directorio del script tiene guiones en el nombre, por lo que no puede
    importarse directamente con `import`. Se usa importlib para cargarlo desde
    su path absoluto.
    """
    script_dir = os.path.abspath(_SAF_SCRIPT_SUBPATH)
    module_path = os.path.join(script_dir, "dspacearchive.py")

    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    spec = importlib.util.spec_from_file_location("dspacearchive", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Limpiar sys.path después de cargar
    if script_dir in sys.path:
        sys.path.remove(script_dir)

    return module


def _write_saf(temp_csv: str, saf_output_path: str) -> None:
    """Invoca DspaceArchive para generar el SAF desde el CSV temporal."""
    try:
        dspace_mod = _load_dspace_archive_module()
        archive = dspace_mod.DspaceArchive(temp_csv)
        # write() espera bytes (internamente usa os.path.join con bytes)
        archive.write(saf_output_path.encode("utf-8"))
        logger.info("[GenerateSAF] SAF generado en '%s'", saf_output_path)
    except Exception as exc:
        import traceback
        logger.error("[GenerateSAF] Error generando SAF: %r\n%s", exc, traceback.format_exc())
    finally:
        if os.path.isfile(temp_csv):
            os.remove(temp_csv)
