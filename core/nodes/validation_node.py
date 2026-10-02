"""
Nodo ValidateInputCSVs — Validación temprana de integridad física y de esquema para CSVs de entrada.

Responsable de verificar que:
  1. El archivo `source_csv_path` exista, tenga tamaño > 0, sea legible y contenga datos.
  2. Tenga columna de título (o columna de DOI para enriquecimiento). Si no tiene ninguna, lanza ValueError temprano.
  3. Tenga columna identificadora ('id', 'doi', 'handle', etc.). Si no existe ninguna,
     genera una columna 'id' autoincremental (1, 2, 3...) y guarda el nuevo archivo en `workspace_dir/source_with_id.csv`.
  4. Emite advertencias (warnings) si faltan columnas habituales ('author', 'date', 'issn', 'type').
  5. Si `repository_csv_path` está definido y no vacío, valida existencia, contenido, columna de título SEDICI
     y columna de identificador SEDICI.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import pandas as pd

from core.state import State

logger = logging.getLogger(__name__)

# Columnas candidatas para Título (búsqueda insensible a mayúsculas y minúsculas)
TITLE_EXACT_CANDIDATES = {
    "title",
    "dc.title",
    "titulo",
    "título",
    "item title",
    "item_title",
    "document title",
    "document_title",
    "article title",
    "article_title",
}
TITLE_PREFIX_CANDIDATES = ("dc.title", "dcterms.title")

# Columnas candidatas para DOI
DOI_EXACT_CANDIDATES = {
    "doi",
    "item doi",
    "item_doi",
    "dc.identifier.doi",
    "article doi",
    "article_doi",
    "document doi",
    "document_doi",
}
DOI_PREFIX_CANDIDATES = ("dc.identifier.doi",)

# Columnas candidatas para Identificador (ID)
ID_EXACT_CANDIDATES = {
    "id",
    "handle",
    "uri",
    "url",
    "pmid",
    "dc.identifier.uri",
    "sedici.identifier.other",
    "identifier",
    "item id",
    "item_id",
    "document id",
    "document_id",
}
ID_PREFIX_CANDIDATES = ("dc.identifier.uri", "sedici.identifier.other")


def _is_valid_id_series(series: pd.Series) -> bool:
    """
    Verifica determinísticamente si una serie puede actuar como identificador único y no nulo.
    Condiciones:
      - No contiene valores nulos, vacíos o literales nulos ('nan', 'none').
      - Todos los valores son estrictamente únicos (sin repetidos).
    """
    if series.empty:
        return False
    clean_series = series.astype(str).str.strip()
    has_empty = series.isna() | (clean_series == "") | clean_series.str.lower().isin(["nan", "none"])
    if has_empty.any():
        return False
    return clean_series.is_unique


def _find_matching_column(
    columns: list[str] | pd.Index,
    exact_candidates: set[str],
    prefix_candidates: tuple[str, ...] = (),
) -> str | None:
    """Busca la primera columna que coincida de forma exacta o por prefijo (case-insensitive)."""
    for col in columns:
        col_clean = str(col).strip().lower()
        if col_clean in exact_candidates:
            return col
        if prefix_candidates and any(col_clean.startswith(p) for p in prefix_candidates):
            return col
    return None


def _fail(state: State, error_msg: str) -> None:
    """Registra el error en el estado y lanza ValueError (Fail-Fast)."""
    logger.error("[ValidateInputCSVs] %s", error_msg)
    state.setdefault("node_errors", {})["ValidateInputCSVs"] = error_msg
    raise ValueError(error_msg)


def validate_input_csvs_node(state: State) -> dict[str, Any]:
    """
    Valida la integridad física y estructural de los CSVs de entrada.

    Args:
        state: Estado compartido del grafo.

    Returns:
        dict con actualizaciones al estado si corresponde (ej. nueva ruta `source_csv_path`).

    Raises:
        ValueError: Si alguna validación de integridad o de esquema requerido falla.
    """
    # Si ya hubo un error en una etapa anterior de ingesta (ej. fallo MinIO en pdf_minio),
    # no enmascarar ni abortar con un error secundario de validación de CSV vacío
    if state.get("node_errors", {}).get("PDFIngest_errors"):
        logger.warning("[ValidateInputCSVs] Se detectaron errores previos en PDFIngest. Omitiendo validación.")
        return {}

    updates: dict[str, Any] = {}

    # ── 1. Integridad física de source_csv_path ───────────────────────────────
    source_csv_path = state.get("source_csv_path")
    if not source_csv_path:
        _fail(state, "El campo 'source_csv_path' no está definido en el estado o está vacío.")

    if not os.path.isfile(source_csv_path):
        _fail(state, f"El archivo source_csv_path no existe: '{source_csv_path}'")

    if os.path.getsize(source_csv_path) == 0:
        _fail(state, f"El archivo source_csv_path está vacío (0 bytes): '{source_csv_path}'")

    try:
        df_source = pd.read_csv(source_csv_path, dtype=str)
    except Exception as exc:
        _fail(state, f"Error al leer el archivo source_csv_path ('{source_csv_path}'): {exc}")

    if df_source.empty or len(df_source) < 1:
        _fail(state, f"El archivo source_csv_path ('{source_csv_path}') no contiene filas de datos.")

    columns = list(df_source.columns)

    # ── 2. Reglas de Título y DOI para source_csv_path ────────────────────────
    has_title = _find_matching_column(columns, TITLE_EXACT_CANDIDATES, TITLE_PREFIX_CANDIDATES) is not None
    has_doi = _find_matching_column(columns, DOI_EXACT_CANDIDATES, DOI_PREFIX_CANDIDATES) is not None

    if not has_title and not has_doi:
        _fail(
            state,
            f"El archivo source_csv_path ('{source_csv_path}') no contiene columna de título ni de DOI. "
            f"Columnas encontradas: {columns}",
        )
    elif not has_title and has_doi:
        logger.info(
            "[ValidateInputCSVs] El archivo '%s' no posee columna de título, pero sí columna de DOI. "
            "Se continúa la ejecución permitiendo enriquecer los títulos mediante DOI.",
            source_csv_path,
        )

    # ── 3. Reglas de Identificador ('id') para source_csv_path ─────────────────
    valid_id_col = None
    for col in columns:
        col_clean = str(col).strip().lower()
        if "doi" in col_clean:
            continue
        is_candidate = (
            col_clean in ID_EXACT_CANDIDATES
            or any(col_clean.startswith(p) for p in ID_PREFIX_CANDIDATES)
        )
        if is_candidate and _is_valid_id_series(df_source[col]):
            valid_id_col = col
            break

    if valid_id_col is None:
        workspace_dir = state.get("workspace_dir")
        if not workspace_dir:
            workspace_dir = os.path.dirname(os.path.abspath(source_csv_path))
        os.makedirs(workspace_dir, exist_ok=True)
        new_csv_path = os.path.join(workspace_dir, "source_with_id.csv")

        # Seleccionar nombre seguro para la columna sintética
        syn_col_name = "id" if "id" not in columns else "synthetic_id"

        logger.warning(
            "[ValidateInputCSVs] No se encontró ninguna columna candidata a identificador válida (única y sin nulos) en '%s'. "
            "Se genera automáticamente la columna '%s' autoincremental en '%s'.",
            source_csv_path,
            syn_col_name,
            new_csv_path,
        )

        df_with_id = df_source.copy()
        df_with_id.insert(0, syn_col_name, range(1, len(df_with_id) + 1))
        df_with_id.to_csv(new_csv_path, index=False)

        state["source_csv_path"] = new_csv_path
        updates["source_csv_path"] = new_csv_path
        state["synthetic_id_column"] = syn_col_name
        updates["synthetic_id_column"] = syn_col_name

    # ── 4. Advertencia de columnas habituales faltantes ─────────────────────────
    cols_lower = [str(c).strip().lower() for c in columns]
    habitual_checks = {
        "author": any(any(k in c for k in ("author", "autor", "creator", "contributor")) for c in cols_lower),
        "date": any(any(k in c for k in ("date", "fecha", "year", "año")) for c in cols_lower),
        "issn": any("issn" in c for c in cols_lower),
        "type": any(any(k in c for k in ("type", "tipo")) for c in cols_lower),
    }
    missing_habitual = [name for name, present in habitual_checks.items() if not present]
    if missing_habitual:
        logger.warning(
            "[ValidateInputCSVs] Columnas habituales ausentes en source CSV ('%s'): %s",
            source_csv_path,
            missing_habitual,
        )

    # ── 5. Reglas para repository_csv_path (SEDICI) ───────────────────────────
    repo_csv_path = state.get("repository_csv_path")
    if repo_csv_path:
        if not os.path.isfile(repo_csv_path):
            _fail(state, f"El archivo repository_csv_path no existe: '{repo_csv_path}'")

        if os.path.getsize(repo_csv_path) == 0:
            _fail(state, f"El archivo repository_csv_path está vacío (0 bytes): '{repo_csv_path}'")

        try:
            df_repo = pd.read_csv(repo_csv_path, dtype=str)
        except Exception as exc:
            _fail(state, f"Error al leer el archivo repository_csv_path ('{repo_csv_path}'): {exc}")

        if df_repo.empty or len(df_repo) < 1:
            _fail(state, f"El archivo repository_csv_path ('{repo_csv_path}') no contiene filas de datos.")

        repo_cols_lower = [str(c).strip().lower() for c in df_repo.columns]

        has_sedici_title = any(
            c in {"dc.title", "title"} or c.startswith("dc.title[") or c.startswith("dc.title.")
            for c in repo_cols_lower
        )
        if not has_sedici_title:
            _fail(
                state,
                f"El archivo repository_csv_path ('{repo_csv_path}') no contiene ninguna columna de título SEDICI "
                f"('dc.title', 'dc.title[...]', 'title'). Columnas encontradas: {list(df_repo.columns)}",
            )

        has_sedici_id = any(
            c in {"dc.identifier.uri", "dc.identifier.uri[]", "sedici.identifier.other"}
            or c.startswith("dc.identifier.uri")
            or c.startswith("sedici.identifier.other")
            for c in repo_cols_lower
        )
        if not has_sedici_id:
            _fail(
                state,
                f"El archivo repository_csv_path ('{repo_csv_path}') no contiene ninguna columna de identificador SEDICI "
                f"('dc.identifier.uri', 'dc.identifier.uri[]', 'sedici.identifier.other'). "
                f"Columnas encontradas: {list(df_repo.columns)}",
            )

    return updates
