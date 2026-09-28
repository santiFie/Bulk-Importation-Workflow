"""
Herramientas seguras y deterministas para el Agente de Recuperación de Deduplicación.

Proporcionan operaciones acotadas de inspección, normalización y validación
sobre CSVs, evitando la ejecución de código libre por parte del LLM.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import pandas as pd

from core.agent.dedup_recovery.models import StrategyResult, ValidationResult
from core.nodes.sanitizer_node import clean_date_value

logger = logging.getLogger(__name__)


def apply_value_mapping(
    csv_path: str,
    column: str,
    mapping: dict[str, str],
) -> StrategyResult:
    """
    Aplica un diccionario de reemplazos directos sobre los valores de una columna.

    Args:
        csv_path: Ruta al archivo CSV.
        column: Nombre de la columna a transformar.
        mapping: Diccionario {antiguo_valor: nuevo_valor}.

    Returns:
        StrategyResult con el resultado tipado, modificaciones y detalle de cambios.
    """
    if not os.path.isfile(csv_path):
        return StrategyResult(
            status="error",
            error_message=f"Archivo no encontrado: {csv_path}",
            is_applicable=False,
        )

    try:
        df = pd.read_csv(csv_path, dtype=str, index_col=False)
    except Exception as exc:
        return StrategyResult(
            status="error",
            error_message=f"Error al leer CSV: {exc}",
            is_applicable=False,
        )

    if column not in df.columns:
        return StrategyResult(
            status="error",
            error_message=f"Columna '{column}' no existe en el CSV.",
            is_applicable=False,
        )

    id_col = "id" if "id" in df.columns else df.columns[0]
    modified_count = 0
    corrections_applied: list[dict[str, Any]] = []

    for idx, row in df.iterrows():
        val = row[column]
        if pd.isna(val):
            continue
        val_str = str(val).strip()
        row_id = str(row.get(id_col, idx))

        new_val = val_str
        # 1. Búsqueda exacta de celda
        if val_str in mapping:
            new_val = mapping[val_str]
        else:
            # 2. Reemplazo de subcadenas si coincide alguna clave del mapping
            for old_token, target_token in mapping.items():
                if old_token.lower() in new_val.lower():
                    pattern = re.compile(re.escape(old_token), re.IGNORECASE)
                    new_val = pattern.sub(target_token, new_val)

        if new_val != val_str:
            df.at[idx, column] = new_val
            modified_count += 1
            corrections_applied.append({
                "column": column,
                "row_id": row_id,
                "original": val_str,
                "cleaned": new_val,
            })

    if modified_count > 0:
        df.to_csv(csv_path, index=False)
        logger.info(
            "[RecoveryTools] apply_value_mapping modificó %d celdas en '%s' (columna '%s').",
            modified_count,
            csv_path,
            column,
        )

    return StrategyResult(
        status="ok",
        is_applicable=True,
        modified_count=modified_count,
        corrections=corrections_applied,
    )


def apply_regex_extraction(
    csv_path: str,
    column: str,
    pattern: str,
    group: int = 1,
) -> StrategyResult:
    """
    Extrae una subcadena coincidente mediante regex y reemplaza el valor de la celda.

    Args:
        csv_path: Ruta al archivo CSV.
        column: Nombre de la columna a transformar.
        pattern: Expresión regular con al menos un grupo de captura.
        group: Índice del grupo a capturar (por defecto 1).

    Returns:
        StrategyResult con el resultado tipado y cantidad de modificaciones.
    """
    if not os.path.isfile(csv_path):
        return StrategyResult(
            status="error",
            error_message=f"Archivo no encontrado: {csv_path}",
            is_applicable=False,
        )

    try:
        compiled_regex = re.compile(pattern)
    except re.error as exc:
        return StrategyResult(
            status="error",
            error_message=f"Expresión regular inválida: {exc}",
            is_applicable=False,
        )

    try:
        df = pd.read_csv(csv_path, dtype=str, index_col=False)
    except Exception as exc:
        return StrategyResult(
            status="error",
            error_message=f"Error al leer CSV: {exc}",
            is_applicable=False,
        )

    if column not in df.columns:
        return StrategyResult(
            status="error",
            error_message=f"Columna '{column}' no existe en el CSV.",
            is_applicable=False,
        )

    id_col = "id" if "id" in df.columns else df.columns[0]
    modified_count = 0
    corrections_applied: list[dict[str, Any]] = []

    for idx, row in df.iterrows():
        val = row[column]
        if pd.isna(val):
            continue
        val_str = str(val).strip()
        row_id = str(row.get(id_col, idx))

        match = compiled_regex.search(val_str)
        if match and match.lastindex and group <= match.lastindex:
            extracted = match.group(group).strip()
            if extracted != val_str:
                df.at[idx, column] = extracted
                modified_count += 1
                corrections_applied.append({
                    "column": column,
                    "row_id": row_id,
                    "original": val_str,
                    "cleaned": extracted,
                })

    if modified_count > 0:
        df.to_csv(csv_path, index=False)

    return StrategyResult(
        status="ok",
        is_applicable=True,
        modified_count=modified_count,
        corrections=corrections_applied,
    )


def apply_date_normalization(
    csv_path: str,
    column: str = "date",
) -> StrategyResult:
    """
    Aplica la normalización canónica de fechas sobre una columna usando clean_date_value.

    Args:
        csv_path: Ruta al archivo CSV.
        column: Columna que contiene las fechas (default: 'date').

    Returns:
        StrategyResult con el resultado tipado y cantidad de modificaciones.
    """
    if not os.path.isfile(csv_path):
        return StrategyResult(
            status="error",
            error_message=f"Archivo no encontrado: {csv_path}",
            is_applicable=False,
        )

    try:
        df = pd.read_csv(csv_path, dtype=str, index_col=False)
    except Exception as exc:
        return StrategyResult(
            status="error",
            error_message=f"Error al leer CSV: {exc}",
            is_applicable=False,
        )

    if column not in df.columns:
        return StrategyResult(
            status="error",
            error_message=f"Columna '{column}' no existe en el CSV.",
            is_applicable=False,
        )

    id_col = "id" if "id" in df.columns else df.columns[0]
    modified_count = 0
    corrections_applied: list[dict[str, Any]] = []

    for idx, row in df.iterrows():
        val = row[column]
        row_id = str(row.get(id_col, idx))
        cleaned, modified = clean_date_value(val)
        if modified:
            df.at[idx, column] = cleaned
            modified_count += 1
            corrections_applied.append({
                "column": column,
                "row_id": row_id,
                "original": str(val),
                "cleaned": cleaned,
            })

    if modified_count > 0:
        df.to_csv(csv_path, index=False)

    return StrategyResult(
        status="ok",
        is_applicable=True,
        modified_count=modified_count,
        corrections=corrections_applied,
    )


def validate_csv_structure(
    original_csv: str,
    modified_csv: str,
) -> ValidationResult:
    """
    Guardrail que compara el CSV modificado contra el original para garantizar integridad.

    Verifica que:
      1. La cantidad total de filas no haya disminuido.
      2. El conjunto de IDs primarios se mantenga exactamente igual.
      3. Todas las columnas originales sigan presentes.

    Returns:
        ValidationResult con 'is_valid' (bool) y 'errors' (list[str]).
    """
    errors: list[str] = []

    if not os.path.isfile(original_csv):
        return ValidationResult(is_valid=False, errors=[f"Original no encontrado: {original_csv}"])
    if not os.path.isfile(modified_csv):
        return ValidationResult(is_valid=False, errors=[f"Modificado no encontrado: {modified_csv}"])

    try:
        df_orig = pd.read_csv(original_csv, dtype=str, index_col=False)
        df_mod = pd.read_csv(modified_csv, dtype=str, index_col=False)
    except Exception as exc:
        return ValidationResult(is_valid=False, errors=[f"Error al leer CSVs para validación: {exc}"])

    if len(df_mod) != len(df_orig):
        errors.append(
            f"Discrepancia en cantidad de filas: original={len(df_orig)}, modificado={len(df_mod)}"
        )

    orig_cols = set(df_orig.columns)
    mod_cols = set(df_mod.columns)
    missing_cols = orig_cols - mod_cols
    if missing_cols:
        errors.append(f"Se perdieron columnas en el archivo modificado: {missing_cols}")

    id_col = "id" if "id" in df_orig.columns else (df_orig.columns[0] if len(df_orig.columns) else None)
    if id_col and id_col in df_mod.columns:
        orig_ids = set(df_orig[id_col].dropna().astype(str).str.strip())
        mod_ids = set(df_mod[id_col].dropna().astype(str).str.strip())
        diff_ids = orig_ids - mod_ids
        if diff_ids:
            errors.append(f"Se perdieron {len(diff_ids)} IDs primarios durante la modificación.")

    return ValidationResult(
        is_valid=len(errors) == 0,
        errors=errors,
    )
