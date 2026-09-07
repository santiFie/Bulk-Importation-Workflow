"""
Nodos del pipeline de importación a SEDICI.

Este módulo contiene todos los nodos del grafo que implementan los pasos
secuenciales del pipeline, desde el crosswalk hasta la importación en DSpace:

  - Paso 2a (crosswalk): map_source_to_generic
  - Paso 2b:             map_sedici_to_generic
  - Paso 3:              deduplicate
  - Paso 4:              metadata_reconciliation
  - Paso 5:              map_to_sedici_format
  - Paso 6:              metadata_corrections
  - Paso 7:              get_pdfs
  - Paso 8:              generate_saf_to_import
  - Paso 9:              import_to_dspace
"""

import csv
import json
import os
import sys
import tempfile
from typing import Any
from langsmith import traceable

from core.clients.crosswalk_client import CrosswalkClient, CrosswalkApiError

# ---------------------------------------------------------------------------
from core.clients.deduplicator_client import DeduplicatorClient

deduplicator_client = DeduplicatorClient()


# ---------------------------------------------------------------------------
# Helper interno: runner de crosswalk
# ---------------------------------------------------------------------------

def _run_crosswalk(csv_input_path: str, config_json_path: str, csv_output_path: str) -> str:
    """
    Aplica el crosswalk definido en config_json_path al CSV de entrada
    via la API REST del backend, y guarda el resultado en csv_output_path.

    Returns:
        Mensaje de éxito o error.
    """
    try:
        client = CrosswalkClient()
        result_bytes = client.run_crosswalk(
            csv_path=csv_input_path,
            config_path=config_json_path,
        )

        output_dir = os.path.dirname(csv_output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(csv_output_path, "wb") as fh:
            fh.write(result_bytes)

        return f"Éxito: Mapeo realizado correctamente. Archivo guardado en '{csv_output_path}'"

    except CrosswalkApiError as exc:
        return f"Error de API durante el crosswalk: {exc}"
    except Exception as exc:
        return f"Error durante la ejecución del crosswalk: {repr(exc)}"


def _save_csv(csv_bytes: bytes, output_path: str) -> dict[str, Any]:
    """Escribe csv_bytes en output_path y devuelve un dict de estado."""
    try:
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "wb") as fh:
            fh.write(csv_bytes)
        row_count = max(0, len(csv_bytes.split(b"\n")) - 2)
        return {"status": "ok", "csv_path": output_path, "row_count": row_count}
    except OSError as exc:
        return {"error": f"No se pudo escribir el archivo de salida: {exc}"}



# ---------------------------------------------------------------------------
# Paso 0 — setup_workspace
# ---------------------------------------------------------------------------
@traceable(name="SetupWorkspace", run_type="chain")
def setup_workspace(state: dict) -> dict[str, Any]:
    """
    Paso 0 — Inicialización del espacio de trabajo (workspace).

    Asegura que exista un directorio de lote único f"{source_name}_{fecha}_{cant}"
    y completa cualquier ruta o configuración opcional con su valor por defecto.
    """
    from datetime import datetime
    from core.state import DEFAULT_CONFIGS_DIR

    source_name = state.get("source_name", "default_source")
    fecha_actual = datetime.now().strftime("%Y%m%d")

    workspace_dir = state.get("workspace_dir")
    if not workspace_dir:
        base_runs_dir = os.path.abspath("runs")
        cant_run = 1
        if os.path.exists(base_runs_dir):
            existing_runs = [
                d for d in os.listdir(base_runs_dir)
                if d.startswith(f"{source_name}_{fecha_actual}_")
            ]
            cant_run = len(existing_runs) + 1
        workspace_dir = os.path.join(base_runs_dir, f"{source_name}_{fecha_actual}_{cant_run}")

    os.makedirs(workspace_dir, exist_ok=True)

    default_sedici_config = os.path.join(DEFAULT_CONFIGS_DIR, "export_10915_crosswalkconfig.json")
    default_sedici_target_config = os.path.join(DEFAULT_CONFIGS_DIR, "config_romero_to_sedici.json")

    updates = {
        "workspace_dir": workspace_dir,
        "sedici_crosswalk_config": state.get("sedici_crosswalk_config") or default_sedici_config,
        "sedici_target_crosswalk_config": state.get("sedici_target_crosswalk_config") or default_sedici_target_config,
        "generic_source_csv_path": state.get("generic_source_csv_path") or os.path.join(workspace_dir, "generic_source.csv"),
        "generic_sedici_csv_path": state.get("generic_sedici_csv_path") or os.path.join(workspace_dir, "generic_sedici.csv"),
        "dedup_output_csv_path": state.get("dedup_output_csv_path") or os.path.join(workspace_dir, "dedup.csv"),
        "reconciled_csv_path": state.get("reconciled_csv_path") or os.path.join(workspace_dir, "reconciled.csv"),
        "sedici_ready_csv_path": state.get("sedici_ready_csv_path") or os.path.join(workspace_dir, "sedici_ready.csv"),
        "umbral_seguro": state.get("umbral_seguro") or 10,
        "umbral_revision": state.get("umbral_revision") or 30,
        "saf_output_path": state.get("saf_output_path") or os.path.join(workspace_dir, "saf_output"),
        "import_mapfile_path": state.get("import_mapfile_path") or os.path.join(workspace_dir, "mapfile.txt"),
    }

    print(f"[setup_workspace] Workspace listo en '{workspace_dir}'")
    return updates


# ---------------------------------------------------------------------------
# Paso 2a (crosswalk) — map_source_to_generic
# ---------------------------------------------------------------------------
@traceable(name="MapSourceToGeneric", run_type="chain")
def map_source_to_generic(state: dict) -> dict[str, Any]:
    """
    Paso 2a (crosswalk) — Aplica el crosswalk config (generado por el
    agente o preexistente) al CSV del repositorio origen para mapearlo
    al formato genérico entendido por el Deduplicador.
    """
    result = _run_crosswalk(
        csv_input_path=state["source_csv_path"],
        config_json_path=state["source_crosswalk_config"],
        csv_output_path=state["generic_source_csv_path"],
    )
    print(f"[map_source_to_generic] {result}")
    return {}


# ---------------------------------------------------------------------------
# Paso 2b — map_sedici_to_generic
# ---------------------------------------------------------------------------
@traceable(name="MapSediciToGeneric", run_type="chain")
def map_sedici_to_generic(state: dict) -> dict[str, Any]:
    """
    Paso 2b — Mapea el CSV exportado de SEDICI al formato genérico
    entendido por el Deduplicador.
    Usa sedici_crosswalk_config como configuración de mapeo.
    """
    result = _run_crosswalk(
        csv_input_path=state["repository_csv_path"],
        config_json_path=state["sedici_crosswalk_config"],
        csv_output_path=state["generic_sedici_csv_path"],
    )
    print(f"[map_sedici_to_generic] {result}")
    return {}


# ---------------------------------------------------------------------------
# Paso 3 — deduplicate
# ---------------------------------------------------------------------------
@traceable(name="Deduplicate", run_type="chain")
def deduplicate(state: dict) -> dict[str, Any]:
    """
    Paso 3 — Envía los dos CSVs en formato genérico al Deduplicador
    para detectar posibles duplicados entre los ítems a importar y SEDICI.

    El Deduplicador devuelve un CSV con los identificadores de los documentos
    del repositorio origen y sus posibles duplicados en SEDICI, junto con
    un porcentaje de seguridad para cada detección.
    """
    print(f"[deduplicate] Iniciando deduplicación para '{state.get('source_name', 'origen')}' usando DeduplicatorClient real...")

    with open(state["generic_sedici_csv_path"], "r") as f:
        reader = csv.DictReader(f)
        print(f"[deduplicate] Columnas csv SEDICI: {reader.fieldnames}")
    with open(state["generic_source_csv_path"], "r") as f:
        reader = csv.DictReader(f)
        print(f"[deduplicate] Columnas csv origen: {reader.fieldnames}")

    csv_bytes = deduplicator_client.detect_duplicates(
        csv_file1_path=state["generic_sedici_csv_path"],   # SEDICI en formato genérico
        csv_file2_path=state["generic_source_csv_path"],   # Origen en formato genérico
        source_name=state["source_name"],
    )
    result = _save_csv(csv_bytes, state["dedup_output_csv_path"])
    print(f"[deduplicate] {result}")
    return {}


# ---------------------------------------------------------------------------
# Paso 4 — metadata_reconciliation
# ---------------------------------------------------------------------------
@traceable(name="MetadataReconciliation", run_type="chain")
def metadata_reconciliation(state: dict) -> dict[str, Any]:
    """
    Paso 4 — Reconciliación de metadatos.

    Con el CSV de resultado del deduplicador, filtra los ítems del repositorio
    origen que no son duplicados (o que están dentro del umbral aceptable) y
    realiza un JOIN con el CSV original (metadatos completos sin mapear)
    usando el identificador de cada documento.

    Genera un CSV reconciliado con los metadatos originales completos de los
    ítems seleccionados para importar.
    """
    import pandas as pd

    umbral_seguro = state.get("umbral_seguro") or 10

    df_dedup = pd.read_csv(state["dedup_output_csv_path"])
    source_path = state.get("curated_csv_path") or state["source_csv_path"]
    df_source = pd.read_csv(source_path)
    id_col_dedup = "id"

    # Detectar formato de columnas del Deduplicador
    if "similarity" in df_dedup.columns and "id_document1" in df_dedup.columns:
        # Formato Backend REST: reporta posibles duplicados entre documentos
        def _parse_similarity(val):
            if pd.isna(val) or str(val).strip().upper() in ("NO_DUPLICATE", "NONE", ""):
                return 0.0
            try:
                score = float(val)
                if score <= 1.0 and umbral_seguro > 1:
                    score *= 100.0
                return score
            except (ValueError, TypeError):
                return 100.0

        scores = df_dedup["similarity"].apply(_parse_similarity)
        # Identificar IDs de la fuente (id_document2) que son duplicados por encima del umbral
        duplicate_mask = scores >= umbral_seguro
        duplicate_source_ids = set(
            df_dedup.loc[duplicate_mask, "id_document2"].dropna().astype(str).unique()
        )

        id_col_source = None
        for candidate in ["id", "sedici.identifier.other", "dc.identifier.uri"]:
            if candidate in df_source.columns:
                id_col_source = candidate
                break

        if id_col_source and duplicate_source_ids:
            df_reconciled = df_source[~df_source[id_col_source].astype(str).isin(duplicate_source_ids)].copy()
        else:
            df_reconciled = df_source.copy()

    else:
        # Formato legacy / mock (FakeDeduplicatorClient)
        score_col = "total" if "total" in df_dedup.columns else df_dedup.columns[-1]
        df_to_import = df_dedup[pd.to_numeric(df_dedup[score_col], errors="coerce").fillna(0) < umbral_seguro]
        id_col_dedup = "id" if "id" in df_dedup.columns else df_dedup.columns[0]

        id_col_source = None
        for candidate in ["id", "sedici.identifier.other", "dc.identifier.uri"]:
            if candidate in df_source.columns:
                id_col_source = candidate
                break

        if id_col_source is None:
            # Fallback: usar el índice para el join
            df_reconciled = df_source.iloc[df_to_import.index].copy()
        else:
            df_reconciled = df_source[df_source[id_col_source].isin(df_to_import[id_col_dedup])].copy()

    # Si el enriquecimiento estuvo habilitado, propagar columnas enriquecidas del CSV genérico
    generic_csv_path = state.get("generic_source_csv_path")
    if state.get("enrichment_enabled", False) and generic_csv_path and os.path.isfile(generic_csv_path):
        try:
            df_generic = pd.read_csv(generic_csv_path)
            if id_col_dedup in df_generic.columns and id_col_source and id_col_source in df_reconciled.columns:
                df_generic_map = df_generic.set_index(id_col_dedup)
                candidate_cols = ["author", "date", "doi", "issn", "isbn", "citation", "subject", "type"]
                for col in candidate_cols:
                    if col in df_generic_map.columns:
                        mapped_series = df_reconciled[id_col_source].map(df_generic_map[col])
                        if col not in df_reconciled.columns:
                            df_reconciled[col] = mapped_series
                        else:
                            mask_empty = df_reconciled[col].isna() | df_reconciled[col].astype(str).str.strip().isin(["", "nan", "None"])
                            df_reconciled.loc[mask_empty, col] = mapped_series[mask_empty]
        except Exception as exc:
            print(f"[metadata_reconciliation] Advertencia al propagar metadatos enriquecidos: {exc}")

    output_dir = os.path.dirname(state["reconciled_csv_path"])
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    df_reconciled.to_csv(state["reconciled_csv_path"], index=False)

    print(f"[metadata_reconciliation] {len(df_reconciled)} ítems seleccionados → '{state['reconciled_csv_path']}'")
    return {}


# ---------------------------------------------------------------------------
# Paso 5 — map_to_sedici_format
# ---------------------------------------------------------------------------
@traceable(name="MapToSediciFormat", run_type="chain")
def map_to_sedici_format(state: dict) -> dict[str, Any]:
    """
    Paso 5 — Mapeo final al formato esperado por SEDICI.

    Aplica el crosswalk del repositorio origen al formato de metadatos
    de SEDICI sobre el CSV reconciliado (metadatos originales completos).
    Genera el CSV final listo para importar a SEDICI.
    """
    result = _run_crosswalk(
        csv_input_path=state["reconciled_csv_path"],
        config_json_path=state["sedici_target_crosswalk_config"],
        csv_output_path=state["sedici_ready_csv_path"],
    )
    print(f"[map_to_sedici_format] {result}")
    return {}


# ---------------------------------------------------------------------------
# Paso 6 — metadata_corrections
# ---------------------------------------------------------------------------

def metadata_corrections(state: dict) -> dict[str, Any]:
    """
    Paso 6 — Corrección programática de metadatos.

    Lee el CSV en formato SEDICI generado por el Paso 5 (sedici_ready_csv_path)
    y aplica un conjunto de correcciones específicas según el repositorio origen
    (source_name), devolviendo el mismo archivo corregido en su lugar.

    Correcciones genéricas (todos los repositorios):
      - Normalización de separadores a ||
      - Normalización de dc.language a códigos de dos letras (es, en, pt)

    Correcciones específicas de SCOPUS:
      - Generación de mods.originInfo.place[es] desde autores_unlp_nombre
      - Filtrado de autores: si hay más de 30, conservar solo los de la UNLP

    Entrada:  state["sedici_ready_csv_path"]  — CSV en formato SEDICI (Paso 5)
    Salida:   mismo archivo sobreescrito con las correcciones aplicadas
    """
    import pandas as pd
    from core.scripts.metadata_corrections import get_corrector

    csv_path = state["sedici_ready_csv_path"]
    source_name = state.get("source_name", "")

    if not os.path.isfile(csv_path):
        raise FileNotFoundError(f"[metadata_corrections] Error: CSV no encontrado: {csv_path}")

    df = pd.read_csv(csv_path)
    corrector = get_corrector(source_name)
    df_corrected = corrector.correct(df)

    df_corrected.to_csv(csv_path, index=False)

    return {}


# ---------------------------------------------------------------------------
# Paso 7 — get_pdfs
# ---------------------------------------------------------------------------

def get_pdfs(state: dict):
    """
    Paso 7 — Obtención de los PDFs.

    (Pendiente de implementación)
    """
    pass


# ---------------------------------------------------------------------------
# Paso 8 — generate_saf_to_import
# ---------------------------------------------------------------------------

def generate_saf_to_import(state: dict) -> dict[str, Any]:
    """
    Paso 8 — Generación del SAF a importar.

    Lee el CSV listo para SEDICI (sedici_ready_csv_path) y genera un
    Simple Archive Format (SAF) en saf_output_path usando el script
    dspace-csv-archive.

    El SAF generado contiene, por cada ítem:
      - item_001/ , item_002/ , …
          - dublin_core.xml  (metadatos en XML de DSpace)
          - contents         (lista de bitstreams)
          - bitstreams       (PDFs, etc. — cuando el paso 7 esté implementado)

    El formato de CSV que espera dspace-csv-archive es:
      - Primera columna:  files  (paths separados por ||, relativos al CSV)
      - Columnas siguientes:  schema.element.qualifier  (opcionalmente [lang])
      - El ItemFactory transforma los encabezados:
          dc.title [en]  →  dc.title#lang#en
          dc.contributor.author  →  dc.contributor.author
    """
    import pandas as pd
    import sys as _sys
    import importlib.util as _util

    csv_path = state["sedici_ready_csv_path"]
    saf_output_path = state["saf_output_path"]

    output_dir = os.path.dirname(saf_output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    if not os.path.isfile(csv_path):
        print(f"[generate_saf_to_import] Error: CSV no encontrado: {csv_path}")
        return {}

    # El DspaceArchive exige una columna 'files' (aunque esté vacía)
    df = pd.read_csv(csv_path)
    if "files" not in df.columns:
        df.insert(0, "files", "")

    # El CSV temporal se escribe en el mismo directorio de salida para que
    # los paths relativos de 'files' se resuelvan correctamente.
    temp_csv = os.path.join(output_dir, "_saf_input.csv")
    df.to_csv(temp_csv, index=False)

    # Importar DspaceArchive desde el script — el directorio tiene guiones
    # en el nombre, así que usamos importlib en lugar de import directo.
    script_dir = os.path.join(
        os.path.dirname(__file__), "..", "..", "scripts", "dspace-csv-archive-master"
    )
    _sys.path.insert(0, script_dir)
    spec = _util.spec_from_file_location(
        "dspacearchive", os.path.join(script_dir, "dspacearchive.py")
    )
    dspace_mod = _util.module_from_spec(spec)
    spec.loader.exec_module(dspace_mod)
    _sys.path.remove(script_dir)
    DspaceArchive = dspace_mod.DspaceArchive

    try:
        archive = DspaceArchive(temp_csv)
        # write() espera bytes (internamente hace os.path.join con bytes)
        archive.write(saf_output_path.encode("utf-8"))
        os.remove(temp_csv)
        print(f"[generate_saf_to_import] SAF generado en '{saf_output_path}'")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"[generate_saf_to_import] Error generando SAF: {repr(exc)}")

    return {}


# ---------------------------------------------------------------------------
# Paso 9 — import_to_dspace
# ---------------------------------------------------------------------------

def import_to_dspace(state: dict) -> dict[str, Any]:
    """
    Paso 9 — Importación a DSpace/SEDICI via Scripts API.

    Toma el SAF generado en el Paso 8 y lo importa directamente a SEDICI
    usando la REST API de DSpace (endpoint /api/system/scripts/import/processes).
    La operación es programática: no usa ningún agente LLM.

    Flujo interno:
      1. Zipa el directorio SAF en memoria.
      2. Lanza un proceso de importación en DSpace (POST multipart).
      3. Hace polling hasta COMPLETED o FAILED.
      4. Descarga el mapfile generado y lo guarda en import_mapfile_path.

    Entrada:
      state["saf_output_path"]     — directorio SAF (salida Paso 8)
      state["dspace_collection"]   — handle o ID de colección destino
      state["import_mapfile_path"] — donde guardar el mapfile
      state["import_validate_only"] — si True, dry-run sin importar (opcional)

    Salida:
      Actualiza state["import_mapfile_path"] con el mapfile guardado en disco.
    """
    import sys as _sys

    saf_dir = state["saf_output_path"]
    collection = state["dspace_collection"]
    mapfile_path = state.get("import_mapfile_path", "")
    validate_only = state.get("import_validate_only") or False
    exclude_bitstreams = state.get("import_exclude_bitstreams") or True

    if not os.path.isdir(saf_dir):
        print(f"[import_to_dspace] Error: directorio SAF no encontrado: {saf_dir}")
        return {}

    if not collection:
        print("[import_to_dspace] Error: dspace_collection no está definido en el estado.")
        return {}

    from mcps.dspace_mcp.src.dspace_client import DSpaceClient
    from mcps.dspace_mcp.src.tools.import_tools import (
        _zip_saf_directory,
        _launch_import_process,
        _poll_until_done,
        _get_process_files,
        _download_file_by_type,
    )
    from mcps.dspace_mcp.src.config import BASE_URL as dspace_base_url, EMAIL as dspace_email, PASSWORD as dspace_password

    if dspace_base_url:
        dspace_base_url = dspace_base_url.replace("host.docker.internal", "localhost")

    if not dspace_email or not dspace_password:
        print(
            "[import_to_dspace] Error: DSPACE_EMAIL y DSPACE_PASSWORD "
            "deben estar definidos en el entorno o en el archivo .env del MCP."
        )
        return {}

    dspace = DSpaceClient(dspace_base_url, dspace_email, dspace_password)
    try:
        dspace.login()
    except Exception as exc:
        print(f"[import_to_dspace] Error al autenticar en DSpace: {exc}")
        return {}

    # Construir parámetros del script import
    zip_filename = "saf_import.zip"
    parameters = [
        {"name": "-a", "value": ""},
        {"name": "-z", "value": zip_filename},
        {"name": "-c", "value": collection},
    ]
    if validate_only:
        parameters.append({"name": "-v", "value": ""})
    if exclude_bitstreams:
        parameters.append({"name": "-x", "value": ""})

    # Zip del SAF
    print(f"[import_to_dspace] Zipeando SAF: {saf_dir}")
    try:
        zip_bytes = _zip_saf_directory(saf_dir)
    except Exception as exc:
        print(f"[import_to_dspace] Error al zipear SAF: {exc}")
        return {}
    print(f"[import_to_dspace] Zip generado: {len(zip_bytes):,} bytes")

    # Lanzar proceso de importación
    print(f"[import_to_dspace] Lanzando proceso de importación (colección: {collection})...")
    try:
        import requests as _requests
        proc_data = _launch_import_process(dspace, zip_bytes, zip_filename, parameters)
    except _requests.HTTPError as exc:
        print(
            f"[import_to_dspace] Error al iniciar proceso: "
            f"{exc.response.status_code} — {exc.response.text}"
        )
        return {}

    process_id = str(proc_data.get("processId", ""))
    if not process_id:
        print(f"[import_to_dspace] Error: no se obtuvo processId. Respuesta: {proc_data}")
        return {}
    print(f"[import_to_dspace] Proceso iniciado con id={process_id}")

    # Polling
    final_proc = _poll_until_done(dspace, process_id, timeout=300)
    if "error" in final_proc:
        print(f"[import_to_dspace] {final_proc['error']}")
        return {}

    final_status = final_proc.get("processStatus", "UNKNOWN")
    print(f"[import_to_dspace] Proceso {process_id} finalizado con estado: {final_status}")

    # Descargar mapfile y log
    output_files = _get_process_files(dspace, process_id)
    mapfile_content = _download_file_by_type(dspace, output_files, "mapfile")
    log_content = _download_file_by_type(dspace, output_files, "log")

    if mapfile_content and mapfile_path:
        os.makedirs(os.path.dirname(mapfile_path) or ".", exist_ok=True)
        with open(mapfile_path, "w", encoding="utf-8") as fh:
            fh.write(mapfile_content)
        print(f"[import_to_dspace] Mapfile guardado en: {mapfile_path}")
    elif mapfile_content:
        print(f"[import_to_dspace] Mapfile obtenido pero import_mapfile_path no definido.")
        print(mapfile_content[:500])

    if final_status == "FAILED":
        if log_content:
            lines = log_content.splitlines()
            excerpt = "\n".join(lines[-20:]) if len(lines) > 20 else log_content
            print(f"[import_to_dspace] Últimas líneas del log:\n{excerpt}")
        print(f"[import_to_dspace] La importación falló.")
        return {}

    items_imported = len(mapfile_content.splitlines()) if mapfile_content else 0
    print(
        f"[import_to_dspace] Importación completada. "
        f"{items_imported} ítem(s) importados a la colección '{collection}'."
    )
    return {}
