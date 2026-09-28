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

    # 1. Normalizar columna 'date'
    if "date" in df.columns:
        id_col = "id" if "id" in df.columns else df.columns[0]
        cleaned_dates = []
        for idx, row in df.iterrows():
            raw_date = row["date"]
            row_id = str(row.get(id_col, idx))
            cleaned, modified = clean_date_value(raw_date)
            cleaned_dates.append(cleaned)

            if modified:
                has_changes = True
                applied_corrections.append({
                    "stage": "pre_dedup_sanitizer",
                    "column": "date",
                    "row_id": row_id,
                    "original": str(raw_date),
                    "cleaned": cleaned,
                })

        df["date"] = cleaned_dates

    # 2. Trim de espacios en columnas 'id' y 'title'
    for col in ["id", "title"]:
        if col in df.columns:
            trimmed = df[col].astype(str).str.strip()
            # Reemplazar representaciones literales de 'nan'
            trimmed = trimmed.replace({"nan": "", "None": ""})
            if not trimmed.equals(df[col]):
                df[col] = trimmed
                has_changes = True

    # 3. Guardar cambios si hubo correcciones
    if has_changes:
        df.to_csv(csv_path, index=False)
        logger.info(
            "[PreDedupSanitizer] Se aplicaron %d correcciones preventivas sobre '%s'.",
            len(applied_corrections),
            csv_path,
        )
        existing = list(state.get("applied_corrections", []))
        existing.extend(applied_corrections)
        return {"applied_corrections": existing}

    logger.debug("[PreDedupSanitizer] CSV '%s' ya se encuentra normalizado.", csv_path)
    return {}
