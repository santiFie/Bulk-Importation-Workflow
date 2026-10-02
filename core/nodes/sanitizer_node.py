"""
Nodo PreDedupSanitizer — Capa preventiva temprana (0 tokens de inferencia).

Normaliza columnas críticas (especialmente fechas e identificadores) en el
CSV en formato genérico (`generic_source_csv_path`) antes de enviarlo
al backend de deduplicación.

Registra todas las mutaciones realizadas en `state["applied_corrections"]`
para que las etapas posteriores (reconciliación y mapeo SEDICI) conserven
la coherencia del dato.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import pandas as pd
from langsmith import traceable

logger = logging.getLogger(__name__)

# Diccionario canónico de meses (Inglés y Español) -> ordinal de dos dígitos
MONTH_MAP = {
    # Inglés
    "january": "01", "jan": "01",
    "february": "02", "feb": "02",
    "march": "03", "mar": "03",
    "april": "04", "apr": "04",
    "may": "05",
    "june": "06", "jun": "06",
    "july": "07", "jul": "07",
    "august": "08", "aug": "08",
    "september": "09", "sep": "09", "sept": "09",
    "october": "10", "oct": "10",
    "november": "11", "nov": "11",
    "december": "12", "dec": "12",
    # Español
    "enero": "01", "ene": "01",
    "febrero": "02",
    "marzo": "03",
    "abril": "04", "abr": "04",
    "mayo": "05",
    "junio": "06",
    "julio": "07",
    "agosto": "08", "ago": "08",
    "septiembre": "09", "setiembre": "09",
    "octubre": "10",
    "noviembre": "11",
    "diciembre": "12", "dic": "12",
}

# Regex para año canónico de 4 dígitos (siglos XIX a XXI)
_YEAR_REGEX = re.compile(r"\b(18\d\d|19\d\d|20\d\d)\b")


def clean_date_value(raw_val: Any) -> tuple[str, bool]:
    """
    Normaliza determinísticamente una cadena de fecha a formato numérico/ISO.

    Args:
        raw_val: Valor original presente en la columna date.

    Returns:
        Tupla (valor_limpio, modificado_bool).
    """
    if raw_val is None or pd.isna(raw_val):
        return "", False

    val_orig = str(raw_val)
    val_str = val_orig.strip()
    if not val_str:
        return "", False

    # 1. Si ya es año numérico puro (ej. "1999"), retornar intacto
    if re.fullmatch(r"^\d{4}$", val_str):
        return val_str, val_str != val_orig

    # 2. Si ya es fecha ISO estándar (ej. "1999-06" o "1999-06-25"), retornar intacto
    if re.fullmatch(r"^\d{4}-\d{2}(-\d{2})?$", val_str):
        return val_str, val_str != val_orig

    # 3. Detectar si contiene un año de 4 dígitos
    year_match = _YEAR_REGEX.search(val_str)
    year = year_match.group(1) if year_match else None

    # Detectar si hay nombres de mes
    found_month_num = None
    lower_val = val_str.lower()
    
    # Detectar rangos bimensuales (ej. "June-July" o "Junio - Julio")
    is_range = bool(re.search(r"[a-záéíóúüñ]+\s*[-/]\s*[a-záéíóúüñ]+", lower_val))

    if not is_range:
        for month_name, month_num in MONTH_MAP.items():
            # Buscar el nombre del mes delimitado por bordes de palabra
            if re.search(rf"\b{re.escape(month_name)}\b", lower_val):
                found_month_num = month_num
                break

    # 4. Formular el valor limpio según los componentes encontrados
    cleaned = val_str
    if year and found_month_num:
        # Formato YYYY-MM
        cleaned = f"{year}-{found_month_num}"
    elif year:
        # Si había rango de meses ("June-July 1999") o solo texto con año ("1999."), conservar el año
        cleaned = year
    else:
        # Si no hay año identificable, recortar puntuación residual
        cleaned = re.sub(r"^[^\w]+|[^\w]+$", "", val_str)

    modified = cleaned != val_str
    return cleaned, modified


def infer_missing_type(row: Any) -> tuple[str, bool]:
    """
    Infiere determinísticamente el tipo de documento a partir de evidencia contextual.

    Args:
        row: Serie o dict de una fila de datos.

    Returns:
        Tupla (tipo_inferido, modificado_bool).
    """
    raw_type = str(row.get("type", "") or "").strip()
    if raw_type and raw_type.lower() not in ("nan", "none"):
        return raw_type, False

    # 1. Si contiene ISSN o indicios de revista en citación -> Articulo
    issn = str(row.get("issn", "") or "").strip()
    citation = str(row.get("citation", "") or "").strip().lower()
    if (issn and issn.lower() not in ("nan", "none")) or any(
        k in citation for k in ["vol", "num", "issn", "revist", "journal"]
    ):
        return "Articulo", True

    # 2. Si contiene ISBN -> Libro
    isbn = str(row.get("isbn", "") or "").strip()
    if isbn and isbn.lower() not in ("nan", "none"):
        return "Libro", True

    # 3. Indicios en el título
    title = str(row.get("title", "") or "").lower()
    if any(k in title for k in ["tesis doctoral", "tesis de maestria", "tesis de grado", "master thesis", "doctoral thesis"]):
        return "Tesis", True

    return "", False


def infer_missing_date(row: Any) -> tuple[str, bool]:
    """
    Infiere determinísticamente una fecha (año de 4 dígitos) a partir de campos contextuales.

    Args:
        row: Serie o dict de una fila de datos.

    Returns:
        Tupla (fecha_inferida, modificado_bool).
    """
    raw_date = str(row.get("date", "") or "").strip()
    if raw_date and raw_date.lower() not in ("nan", "none"):
        return clean_date_value(raw_date)

    # Buscar año de 4 dígitos en citation, title o description
    for field in ["citation", "title", "description"]:
        val = str(row.get(field, "") or "").strip()
        if val and val.lower() not in ("nan", "none"):
            match = _YEAR_REGEX.search(val)
            if match:
                return match.group(1), True

    return "", False


@traceable(name="PreDedupSanitizer", run_type="chain")
def pre_dedup_sanitizer(state: dict) -> dict[str, Any]:
    """
    Paso Preventivo — Sanitiza columnas críticas antes de la deduplicación.

    Entrada: state["generic_source_csv_path"]
    Salida: sobrescribe el archivo si hubo cambios y registra en state["applied_corrections"].
    """
    csv_path = state.get("generic_source_csv_path")
    if not csv_path or not os.path.isfile(csv_path):
        logger.warning("[PreDedupSanitizer] Archivo genérico '%s' no encontrado.", csv_path)
        return {}

    try:
        df = pd.read_csv(csv_path, dtype=str, index_col=False)
    except Exception as exc:
        logger.error("[PreDedupSanitizer] Error leyendo '%s': %s", csv_path, exc)
        return {}

    applied_corrections: list[dict[str, Any]] = []
    has_changes = False

    # 1. Normalizar e inferir columna 'date'
    if "date" in df.columns:
        id_col = "id" if "id" in df.columns else df.columns[0]
        cleaned_dates = []
        for idx, row in df.iterrows():
            raw_date = row["date"]
            row_id = str(row.get(id_col, idx))
            cleaned, modified = clean_date_value(raw_date)
            if not cleaned:
                cleaned, modified = infer_missing_date(row)

            cleaned_dates.append(cleaned)

            if modified:
                has_changes = True
                applied_corrections.append({
                    "stage": "pre_dedup_sanitizer",
                    "column": "date",
                    "row_id": row_id,
                    "original": str(raw_date or ""),
                    "cleaned": cleaned,
                })

        df["date"] = cleaned_dates

    # 2. Inferir columna 'type' si está ausente o vacía
    if "type" not in df.columns:
        df["type"] = ""
        has_changes = True

    id_col = "id" if "id" in df.columns else df.columns[0]
    cleaned_types = []
    for idx, row in df.iterrows():
        raw_type = str(row.get("type", "") or "").strip()
        row_id = str(row.get(id_col, idx))
        if not raw_type or raw_type.lower() in ("nan", "none"):
            inferred_type, modified = infer_missing_type(row)
            cleaned_types.append(inferred_type)
            if modified:
                has_changes = True
                applied_corrections.append({
                    "stage": "pre_dedup_sanitizer",
                    "column": "type",
                    "row_id": row_id,
                    "original": raw_type,
                    "cleaned": inferred_type,
                })
        else:
            cleaned_types.append(raw_type)

    df["type"] = cleaned_types

    # 3. Trim de espacios en columnas 'id' y 'title'
    for col in ["id", "title"]:
        if col in df.columns:
            trimmed = df[col].astype(str).str.strip()
            # Reemplazar representaciones literales de 'nan'
            trimmed = trimmed.replace({"nan": "", "None": ""})
            if not trimmed.equals(df[col]):
                df[col] = trimmed
                has_changes = True

    # 4. Garantizar identificadores únicos y no vacíos en 'id'
    if "id" not in df.columns:
        df["id"] = ""
        has_changes = True

    synthetic_ids_assigned: dict[int, str] = {}
    for idx in df.index:
        val = df.loc[idx, "id"]
        if pd.isna(val) or not str(val).strip() or str(val).strip().lower() in ("nan", "none"):
            new_id = f"item_{idx + 1}"
            df.loc[idx, "id"] = new_id
            synthetic_ids_assigned[idx] = new_id
            has_changes = True
            applied_corrections.append({
                "stage": "pre_dedup_sanitizer",
                "column": "id",
                "row_id": new_id,
                "original": str(val or ""),
                "cleaned": new_id,
            })

    # Sincronizar IDs sintéticos con el archivo fuente para MetadataReconciliation
    source_path = state.get("curated_csv_path") or state.get("source_csv_path")
    if synthetic_ids_assigned and source_path and os.path.isfile(source_path):
        try:
            df_source = pd.read_csv(source_path, dtype=str)
            config_path = state.get("source_crosswalk_config")
            source_id_col = None
            if config_path and os.path.isfile(config_path):
                from core.nodes.reconciliation_node import load_crosswalk_mappings
                _, generic_to_source = load_crosswalk_mappings(config_path)
                source_id_col = generic_to_source.get("id")

            if source_id_col and source_id_col in df_source.columns:
                for row_idx, syn_id in synthetic_ids_assigned.items():
                    if row_idx < len(df_source):
                        cur_val = df_source.loc[row_idx, source_id_col]
                        if pd.isna(cur_val) or not str(cur_val).strip() or str(cur_val).strip().lower() in ("nan", "none"):
                            df_source.loc[row_idx, source_id_col] = syn_id
            else:
                if "id" not in df_source.columns:
                    df_source["id"] = df["id"].copy()
                else:
                    for row_idx, syn_id in synthetic_ids_assigned.items():
                        if row_idx < len(df_source):
                            df_source.loc[row_idx, "id"] = syn_id

            df_source.to_csv(source_path, index=False)
            logger.info(
                "[PreDedupSanitizer] Sincronizados %d IDs sintéticos con '%s'.",
                len(synthetic_ids_assigned),
                source_path,
            )
        except Exception as exc:
            logger.warning(
                "[PreDedupSanitizer] No se pudo sincronizar IDs con fuente '%s': %s",
                source_path,
                exc,
            )

    # 5. Fallback preventivo de 'author': evitar campos obligatorios vacíos
    if "author" in df.columns:
        for idx in df.index:
            val = df.loc[idx, "author"]
            if pd.isna(val) or not str(val).strip() or str(val).strip().lower() in ("nan", "none"):
                row_id = str(df.loc[idx, "id"])
                df.loc[idx, "author"] = "Desconocido"
                has_changes = True
                applied_corrections.append({
                    "stage": "pre_dedup_sanitizer",
                    "column": "author",
                    "row_id": row_id,
                    "original": str(val or ""),
                    "cleaned": "Desconocido",
                })

    # 6. Segregación a cuarentena (pending_to_review.csv) para registros con date o type nulos
    is_empty_date = (
        df["date"].isna()
        | (df["date"].str.strip() == "")
        | (df["date"].str.strip().str.lower().isin(["nan", "none"]))
        if "date" in df.columns
        else pd.Series(True, index=df.index)
    )
    is_empty_type = (
        df["type"].isna()
        | (df["type"].str.strip() == "")
        | (df["type"].str.strip().str.lower().isin(["nan", "none"]))
        if "type" in df.columns
        else pd.Series(True, index=df.index)
    )
    quarantine_mask = is_empty_date | is_empty_type

    pending_csv = None
    if quarantine_mask.any():
        df_quarantine = df[quarantine_mask].copy()
        reasons = []
        for idx_q in df_quarantine.index:
            d_err = is_empty_date.loc[idx_q]
            t_err = is_empty_type.loc[idx_q]
            if d_err and t_err:
                reasons.append("Campos obligatorios 'date' y 'type' ausentes")
            elif d_err:
                reasons.append("Campo obligatorio 'date' ausente")
            else:
                reasons.append("Campo obligatorio 'type' ausente")

        df_quarantine["quarantine_reason"] = reasons

        workspace_dir = state.get("workspace_dir") or os.path.dirname(csv_path) or "."
        pending_csv = state.get("pending_to_review_csv_path") or os.path.join(
            workspace_dir, "pending_to_review.csv"
        )

        # Si el CSV fuente existe, guardar los registros de cuarentena con sus columnas originales
        if source_path and os.path.isfile(source_path):
            try:
                df_src_full = pd.read_csv(source_path, dtype=str)
                valid_src_indices = [i for i in df_quarantine.index if i < len(df_src_full)]
                df_pending_source = df_src_full.loc[valid_src_indices].copy()
                df_pending_source["quarantine_reason"] = [
                    reasons[list(df_quarantine.index).index(i)] for i in valid_src_indices
                ]
                df_pending_source.to_csv(pending_csv, index=False)

                # Excluir las filas en cuarentena del CSV fuente para MetadataReconciliation
                df_src_clean = df_src_full.drop(index=valid_src_indices)
                df_src_clean.to_csv(source_path, index=False)
            except Exception as exc:
                logger.warning(
                    "[PreDedupSanitizer] Error al escribir pending_to_review desde fuente: %s",
                    exc,
                )
                df_quarantine.to_csv(pending_csv, index=False)
        else:
            df_quarantine.to_csv(pending_csv, index=False)

        logger.warning(
            "[PreDedupSanitizer] ⚠️ %d registro(s) con campos críticos ausentes fueron segregados a '%s'.",
            len(df_quarantine),
            pending_csv,
        )

        # Excluir las filas en cuarentena del DataFrame genérico
        df = df[~quarantine_mask].copy()
        has_changes = True

    # 7. Guardar cambios si hubo correcciones o exclusiones
    if has_changes:
        df.to_csv(csv_path, index=False)
        logger.info(
            "[PreDedupSanitizer] Se aplicaron %d correcciones preventivas sobre '%s'.",
            len(applied_corrections),
            csv_path,
        )

    existing = list(state.get("applied_corrections", []))
    existing.extend(applied_corrections)

    updates: dict[str, Any] = {"applied_corrections": existing}
    if pending_csv:
        updates["pending_to_review_csv_path"] = pending_csv

    if len(df) == 0:
        node_errors = dict(state.get("node_errors", {}))
        err_msg = "Todos los registros del lote fueron segregados a cuarentena por campos críticos ausentes."
        node_errors["PreDedupSanitizer"] = err_msg
        logger.error("[PreDedupSanitizer] %s", err_msg)
        updates["node_errors"] = node_errors

    return updates
