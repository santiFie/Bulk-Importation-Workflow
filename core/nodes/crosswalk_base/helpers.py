"""
Utilidades comunes para generadores de crosswalk.
"""

import csv
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def save_crosswalk_config_json(
    mappings: list[dict[str, Any]],
    settings: dict[str, Any],
    output_path: str,
) -> str:
    """
    Construye y guarda el archivo JSON de configuración del crosswalk con formato [mappings, settings].

    Args:
        mappings: Lista de diccionarios de mapeo de columnas.
        settings: Diccionario de opciones de separadores y delimitadores.
        output_path: Ruta del archivo JSON a escribir.

    Returns:
        Ruta del archivo guardado.
    """
    config_data = [mappings, settings]
    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=2, ensure_ascii=False)

    logger.info("[CrosswalkBase] Configuración guardada en: '%s'", output_path)
    return output_path


def read_csv_headers(csv_path: str) -> list[str]:
    """
    Lee las cabeceras de un archivo CSV usando Sniffer o fallback estándar.

    Args:
        csv_path: Ruta al archivo CSV.

    Returns:
        Lista con los nombres de las cabeceras.
    """
    if not os.path.isfile(csv_path):
        raise FileNotFoundError(f"Archivo CSV no encontrado en '{csv_path}'")

    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample)
            delimiter = dialect.delimiter
        except Exception:
            delimiter = ","

        reader = csv.reader(f, delimiter=delimiter)
        try:
            headers = next(reader)
            return [h.strip() for h in headers if h is not None]
        except StopIteration:
            return []


def read_crosswalk_config(config_path_or_dict: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Carga una configuración de crosswalk desde un path, dict o lista devolviendo (mappings, options).

    Args:
        config_path_or_dict: Ruta al archivo JSON, o estructura ya cargada.

    Returns:
        Tupla (mappings, options).
    """
    if not config_path_or_dict:
        return [], {}

    data = None
    if isinstance(config_path_or_dict, dict):
        return config_path_or_dict.get("mappings", []), config_path_or_dict.get("options", {})
    elif isinstance(config_path_or_dict, list):
        data = config_path_or_dict
    elif isinstance(config_path_or_dict, str):
        if not os.path.isfile(config_path_or_dict):
            logger.warning("[CrosswalkBase] Config no encontrado en '%s'", config_path_or_dict)
            return [], {}
        try:
            with open(config_path_or_dict, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logger.error("[CrosswalkBase] Error al leer config '%s': %s", config_path_or_dict, exc)
            return [], {}

    if isinstance(data, list):
        if len(data) >= 2 and isinstance(data[0], list) and isinstance(data[1], dict):
            return data[0], data[1]
        elif len(data) >= 1 and isinstance(data[0], list):
            return data[0], {}
        elif len(data) >= 1 and isinstance(data[0], dict):
            return data, {}

    return [], {}


def validate_column_mappings_base(
    raw_mappings: list[dict[str, Any]],
    available_columns: list[str],
) -> list[dict[str, Any]]:
    """
    Guardrail base en memoria:
    Verifica que cada mapeo posea 'left' y 'replace' válidos y que las columnas 'left'
    existan en available_columns (incluyendo sintaxis 'ColA+ColB').

    Args:
        raw_mappings: Lista de mapeos propuestos por el LLM o deterministas.
        available_columns: Lista de cabeceras disponibles en el CSV de entrada.

    Returns:
        Lista de mapeos que superan el filtrado base.
    """
    validated: list[dict[str, Any]] = []
    columns_set = set(available_columns)

    for m in raw_mappings:
        left = m.get("left", "").strip()
        replace = m.get("replace", "").strip()
        if not left or not replace:
            continue

        # Validar existencia de columnas origen (soporta split con '+')
        parts = [p.strip() for p in left.split("+")]
        if not all(p in columns_set for p in parts if not p.endswith("*")):
            logger.warning(
                "[CrosswalkBase Guardrail] Columna origen '%s' no encontrada en el CSV. Mapeo omitido.",
                left,
            )
            continue

        validated.append({
            "left": left,
            "replace": replace,
            "required": bool(m.get("required", False)),
            "default": m.get("default", ""),
            "filter": m.get("filter", "trim"),
        })

    return validated
