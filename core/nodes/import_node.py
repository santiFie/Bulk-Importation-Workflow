"""
Nodo ImportToDspace — Paso 9 del pipeline.

Toma el SAF generado en el Paso 8 e importa directamente a SEDICI
usando la REST API de DSpace (endpoint /api/system/scripts/import/processes).
La operación es programática: no usa ningún agente LLM.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from langsmith import traceable

logger = logging.getLogger(__name__)

# Parámetros del script de importación de DSpace
_PARAM_ADD              = {"name": "-a", "value": ""}
_PARAM_VALIDATE_ONLY    = {"name": "-v", "value": ""}
_PARAM_EXCLUDE_BITSTREAMS = {"name": "-x", "value": ""}

# Nombre del ZIP que se envía al servidor
_ZIP_FILENAME = "saf_import.zip"

# Timeout del polling de importación en segundos
_IMPORT_POLL_TIMEOUT = 300

# Líneas finales del log a mostrar cuando la importación falla
_LOG_EXCERPT_LINES = 20


@dataclass
class DSpaceImportConfig:
    """Configuración inmutable para una operación de importación a DSpace."""
    saf_dir: str
    collection: str
    mapfile_path: str
    validate_only: bool = False
    exclude_bitstreams: bool = True

    def build_parameters(self) -> list[dict]:
        """Construye la lista de parámetros del script de importación."""
        params = [
            _PARAM_ADD,
            {"name": "-z", "value": _ZIP_FILENAME},
            {"name": "-c", "value": self.collection},
        ]
        if self.validate_only:
            params.append(_PARAM_VALIDATE_ONLY)
        if self.exclude_bitstreams:
            params.append(_PARAM_EXCLUDE_BITSTREAMS)
        return params


@traceable(name="ImportToDspace", run_type="chain")
def import_to_dspace(state: dict) -> dict[str, Any]:
    """
    Paso 9 — Importación a DSpace/SEDICI via Scripts API.

    Flujo interno:
      1. Valida las precondiciones (directorio SAF, colección, credenciales).
      2. Autentica contra DSpace.
      3. Zipa el directorio SAF en memoria.
      4. Lanza un proceso de importación en DSpace (POST multipart).
      5. Hace polling hasta COMPLETED o FAILED.
      6. Descarga el mapfile generado y lo guarda en import_mapfile_path.

    Entrada:
      state["saf_output_path"]     — directorio SAF (salida Paso 8)
      state["dspace_collection"]   — handle o ID de colección destino
      state["import_mapfile_path"] — donde guardar el mapfile
      state["import_validate_only"] — si True, dry-run sin importar (opcional)

    Salida:
      Sin cambios en el state (el mapfile se escribe en disco).
    """
    import_cfg = DSpaceImportConfig(
        saf_dir=state["saf_output_path"],
        collection=state["dspace_collection"],
        mapfile_path=state.get("import_mapfile_path", ""),
        validate_only=state.get("import_validate_only") or False,
        exclude_bitstreams=state.get("import_exclude_bitstreams") or True,
    )

    if not _validate_preconditions(import_cfg):
        return {}

    dspace = _authenticate_dspace()
    if dspace is None:
        return {}

    zip_bytes = _zip_saf(import_cfg.saf_dir)
    if zip_bytes is None:
        return {}

    proc_data = _launch_import(dspace, zip_bytes, import_cfg)
    if proc_data is None:
        return {}

    process_id = str(proc_data.get("processId", ""))
    if not process_id:
        logger.error("[ImportToDSpace] No se obtuvo processId. Respuesta: %s", proc_data)
        return {}

    logger.info("[ImportToDSpace] Proceso iniciado con id=%s", process_id)

    _poll_and_finalize(dspace, process_id, import_cfg)
    return {}


# ---------------------------------------------------------------------------
# Helpers de validación y autenticación
# ---------------------------------------------------------------------------

def _validate_preconditions(cfg: DSpaceImportConfig) -> bool:
    """Verifica que el directorio SAF y la colección estén configurados."""
    if not os.path.isdir(cfg.saf_dir):
        logger.error("[ImportToDSpace] Directorio SAF no encontrado: '%s'", cfg.saf_dir)
        return False
    if not cfg.collection:
        logger.error("[ImportToDSpace] 'dspace_collection' no está definido en el estado.")
        return False
    return True


def _authenticate_dspace():
    """Instancia y autentica un DSpaceClient. Retorna None si falla."""
    from mcps.dspace_mcp.src.dspace_client import DSpaceClient
    from mcps.dspace_mcp.src.config import (
        BASE_URL as dspace_base_url,
        EMAIL as dspace_email,
        PASSWORD as dspace_password,
    )

    base_url = (dspace_base_url or "").replace("host.docker.internal", "localhost")

    if not dspace_email or not dspace_password:
        logger.error(
            "[ImportToDSpace] DSPACE_EMAIL y DSPACE_PASSWORD deben estar definidos "
            "en el entorno o en el archivo .env del MCP."
        )
        return None

    dspace = DSpaceClient(base_url, dspace_email, dspace_password)
    try:
        dspace.login()
    except Exception as exc:
        logger.error("[ImportToDSpace] Error al autenticar en DSpace: %s", exc)
        return None

    return dspace


# ---------------------------------------------------------------------------
# Helpers de importación
# ---------------------------------------------------------------------------

def _zip_saf(saf_dir: str) -> Optional[bytes]:
    """Comprime el directorio SAF en memoria. Retorna None si falla."""
    from mcps.dspace_mcp.src.tools.import_tools import _zip_saf_directory

    logger.info("[ImportToDSpace] Comprimiendo SAF: '%s'", saf_dir)
    try:
        zip_bytes = _zip_saf_directory(saf_dir)
        logger.info("[ImportToDSpace] ZIP generado: %d bytes", len(zip_bytes))
        return zip_bytes
    except Exception as exc:
        logger.error("[ImportToDSpace] Error al comprimir SAF: %s", exc)
        return None


def _launch_import(dspace: Any, zip_bytes: bytes, cfg: DSpaceImportConfig) -> Optional[dict]:
    """Lanza el proceso de importación en DSpace. Retorna None si falla."""
    import requests as _requests
    from mcps.dspace_mcp.src.tools.import_tools import _launch_import_process

    logger.info("[ImportToDSpace] Lanzando importación (colección: '%s')...", cfg.collection)
    parameters = cfg.build_parameters()
    try:
        return _launch_import_process(dspace, zip_bytes, _ZIP_FILENAME, parameters)
    except _requests.HTTPError as exc:
        logger.error(
            "[ImportToDSpace] Error al iniciar proceso: %s — %s",
            exc.response.status_code, exc.response.text,
        )
        return None


def _poll_and_finalize(dspace: Any, process_id: str, cfg: DSpaceImportConfig) -> None:
    """Hace polling, descarga el mapfile y el log cuando el proceso termina."""
    from mcps.dspace_mcp.src.tools.import_tools import (
        _poll_until_done,
        _get_process_files,
        _download_file_by_type,
    )

    final_proc = _poll_until_done(dspace, process_id, timeout=_IMPORT_POLL_TIMEOUT)
    if "error" in final_proc:
        logger.error("[ImportToDSpace] %s", final_proc["error"])
        return

    final_status = final_proc.get("processStatus", "UNKNOWN")
    logger.info("[ImportToDSpace] Proceso %s finalizado con estado: %s", process_id, final_status)

    output_files = _get_process_files(dspace, process_id)
    mapfile_content = _download_file_by_type(dspace, output_files, "mapfile")
    log_content     = _download_file_by_type(dspace, output_files, "log")

    _save_mapfile(mapfile_content, cfg.mapfile_path)

    if final_status == "FAILED":
        _log_failure_excerpt(log_content)
        return

    items_imported = len(mapfile_content.splitlines()) if mapfile_content else 0
    logger.info(
        "[ImportToDSpace] Importación completada. %d ítem(s) importados a '%s'.",
        items_imported, cfg.collection,
    )


def _save_mapfile(content: Optional[str], mapfile_path: str) -> None:
    """Guarda el mapfile en disco si hay contenido y un path definido."""
    if content and mapfile_path:
        os.makedirs(os.path.dirname(mapfile_path) or ".", exist_ok=True)
        with open(mapfile_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        logger.info("[ImportToDSpace] Mapfile guardado en: '%s'", mapfile_path)
    elif content:
        logger.info("[ImportToDSpace] Mapfile obtenido pero import_mapfile_path no definido.")
        logger.debug(content[:500])


def _log_failure_excerpt(log_content: Optional[str]) -> None:
    """Registra las últimas líneas del log de un proceso fallido."""
    logger.error("[ImportToDSpace] La importación falló.")
    if log_content:
        lines = log_content.splitlines()
        excerpt_lines = lines[-_LOG_EXCERPT_LINES:] if len(lines) > _LOG_EXCERPT_LINES else lines
        excerpt = "\n".join(excerpt_lines)
        logger.error("[ImportToDSpace] Últimas líneas del log:\n%s", excerpt)
