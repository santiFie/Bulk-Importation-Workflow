"""
Funciones de soporte para el agente de generación de crosswalk hacia SEDICI.

Implementa:
  - Carga y validación contra el catálogo formal de metadatos de SEDICI.
  - Nivel 1: Composición determinista de campos genéricos conocidos hacia SEDICI.
  - Extracción y formateo de muestras para columnas remanentes (Nivel 2).
  - Nivel 3: Validación contra lista blanca (guardrail) y fusión de configuraciones.
"""

import csv
import itertools
import json
import logging
import os
import re
from typing import Any, Optional

from core.nodes.crosswalk_base.helpers import (
    build_mapping_entry,
    read_crosswalk_config,
    save_crosswalk_config_json,
)

logger = logging.getLogger(__name__)

# Ruta por defecto al catálogo de metadatos de SEDICI
SEDICI_SCHEMA_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "schemas", "sedici_metadata_schema.json")
)

# Mapeo canónico estándar: Formato Genérico (12 campos) → Metadato oficial de SEDICI
CANONICAL_GENERIC_TO_SEDICI: dict[str, str] = {
    "title": "dc.title[es]",
    "author": "sedici.creator.person[es]",
    "date": "dc.date.issued",
    "description": "dc.description.abstract[es]",
    "subject": "sedici.subject.materias[es]",
    "citation": "sedici.relation.journalTitle[es]",
    "doi": "sedici.identifier.doi",
    "issn": "sedici.identifier.issn",
    "isbn": "sedici.identifier.isbn",
    "type": "dc.type",
    "subtitle": "sedici.title.subtitle[es]",
}


def load_sedici_catalog(schema_path: Optional[str] = None) -> list[dict[str, Any]]:
    """Carga el catálogo JSON de metadatos oficiales de SEDICI."""
    path = schema_path or SEDICI_SCHEMA_PATH
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Catálogo de metadatos de SEDICI no encontrado en '{path}'")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_valid_sedici_fields(catalog: list[dict[str, Any]]) -> tuple[dict[str, dict], set[str], set[str]]:
    """
    Extrae estructuras de validación rápida a partir del catálogo:
      - schema_by_field: dict {field: schema_dict}
      - valid_base_fields: conjunto de nombres de campo base (sin calificadores de idioma)
      - allow_lang_fields: conjunto de campos que admiten calificadores como [es], [en]
    """
    schema_by_field = {entry["field"]: entry for entry in catalog}
    valid_base_fields = set(schema_by_field.keys())
    allow_lang_fields = {entry["field"] for entry in catalog if entry.get("allow_lang_qualifier", False)}
    return schema_by_field, valid_base_fields, allow_lang_fields




def map_generic_to_sedici_level1(
    reconciled_columns: list[str],
    source_crosswalk_cfg: Any,
) -> tuple[list[dict[str, Any]], set[str]]:
    """
    Nivel 1 determinista:
    Mapea automáticamente las columnas del CSV reconciliado hacia SEDICI
    utilizando el conocimiento adquirido en el Paso 1 (source -> generic)
    y las reglas canónicas de generic -> SEDICI.

    Returns:
        level1_mappings: Lista de dicts con mappings para SEDICI.
        covered_source_cols: Conjunto de columnas del CSV reconciliado que quedaron cubiertas.
    """
    level1_mappings: list[dict[str, Any]] = []
    covered_source_cols: set[str] = set()
    reconciled_set = set(reconciled_columns)

    mappings_p1, _ = read_crosswalk_config(source_crosswalk_cfg)

    # 1. Proyectar mappings previos (Paso 1: source -> generic -> SEDICI)
    for m in mappings_p1:
        left = m.get("left", "").strip()
        generic_field = m.get("replace", "").strip().lower()
        if not left or not generic_field:
            continue

        sedici_target = CANONICAL_GENERIC_TO_SEDICI.get(generic_field)
        if not sedici_target:
            continue

        # Columnas compuestas (soporta concatenaciones con '+')
        parts = {p.strip() for p in left.split("+")}
        if parts.issubset(reconciled_set):
            level1_mappings.append(
                build_mapping_entry(
                    left=left,
                    replace=sedici_target,
                    default=m.get("default", ""),
                    required=m.get("required", generic_field == "title"),
                    filter_rule=m.get("filter", "trim"),
                )
            )
            covered_source_cols.update(parts)

    # 2. Cobertura de columnas remanentes con nombre genérico directo (ej: 'doi', 'issn')
    for col in reconciled_columns:
        if col in covered_source_cols:
            continue

        sedici_target = CANONICAL_GENERIC_TO_SEDICI.get(col.strip().lower())
        if sedici_target:
            level1_mappings.append(
                build_mapping_entry(
                    left=col,
                    replace=sedici_target,
                    required=False,
                )
            )
            covered_source_cols.add(col)

    return level1_mappings, covered_source_cols



def identify_remnant_columns(
    reconciled_columns: list[str],
    covered_source_cols: set[str],
) -> list[str]:
    """Devuelve las columnas del CSV reconciliado que no fueron cubiertas en el Nivel 1."""
    return [col for col in reconciled_columns if col not in covered_source_cols]


def read_remnant_samples(csv_path: str, remnant_cols: list[str], max_rows: int = 3) -> str:
    """Lee las primeras filas de las columnas remanentes y las formatea para el prompt."""
    if not os.path.isfile(csv_path) or not remnant_cols:
        return "No hay columnas remanentes o archivo no encontrado."

    samples: dict[str, list[str]] = {col: [] for col in remnant_cols}
    try:
        with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                if i >= max_rows:
                    break
                for col in remnant_cols:
                    val = row.get(col, "")
                    if val is not None and str(val).strip():
                        samples[col].append(str(val).strip())
    except Exception as exc:
        return f"Error al leer muestras del CSV: {exc}"

    lines = []
    for col in remnant_cols:
        val_list = samples.get(col, [])
        sample_str = ", ".join(f"'{v[:80]}'" for v in val_list[:max_rows]) if val_list else "(sin valores)"
        lines.append(f"- Columna '{col}': Muestras -> [{sample_str}]")

    return "\n".join(lines)


def format_sedici_catalog_for_prompt(catalog: list[dict[str, Any]]) -> str:
    """Formatea el catálogo de metadatos de SEDICI de manera sintética y clara para el LLM."""
    lines = []
    for item in catalog:
        f = item["field"]
        lbl = item.get("label", "")
        desc = item.get("description", "")
        multival = " (multivalor)" if item.get("multivalue") else ""
        lang = " [admite calificador [es]/[en]]" if item.get("allow_lang_qualifier") else ""
        lines.append(f"- `{f}` ({lbl}){multival}{lang}: {desc}")
    return "\n".join(lines)


def validate_and_filter_target_mappings(
    raw_mappings: list[dict[str, Any]],
    catalog: list[dict[str, Any]],
    reconciled_columns: list[str],
) -> list[dict[str, Any]]:
    """
    Nivel 3 Guardrail Determinista:
    Verifica que cada mapeo propuesto por el LLM:
      1. Tenga un 'left' que exista en el CSV reconciliado.
      2. Tenga un 'replace' que sea un metadato formalmente válido de SEDICI.
      3. Ajusta o corrige automáticamente calificadores de idioma.
      4. Si el LLM inventa un campo inexistente pero relacionado a identificadores,
         lo redirige a 'sedici.identifier.other'. Si no es recuperable, lo descarta.
    """
    _, valid_base_fields, allow_lang_fields = get_valid_sedici_fields(catalog)
    validated: list[dict[str, Any]] = []

    for m in raw_mappings:
        left = m.get("left", "").strip()
        replace = m.get("replace", "").strip()
        if not left or not replace:
            continue

        # Validar que left exista en el CSV (o soporte '+' de columnas existentes)
        parts = [p.strip() for p in left.split("+")]
        if not all(p in reconciled_columns for p in parts):
            logger.warning(
                "[TargetCrosswalk Guardrail] Columna origen '%s' no existe en el CSV. Mapeo omitido.",
                left
            )
            continue

        # Analizar replace (puede contener calificador [es], [en])
        match = re.match(r"^([a-zA-Z0-9\._\-]+)(?:\[([a-zA-Z]{2})\])?$", replace)
        if not match:
            logger.warning("[TargetCrosswalk Guardrail] Formato de metadato inválido: '%s'", replace)
            continue

        base_field = match.group(1)
        lang_qual = match.group(2)

        # Validación contra lista blanca
        if base_field in valid_base_fields:
            if lang_qual and base_field not in allow_lang_fields:
                # El campo no admite calificador de idioma, se remueve
                replace_final = base_field
            else:
                replace_final = replace
        else:
            # Campo no reconocido: heurística de rescate o descarte
            if "identifier" in base_field or "id" in base_field.lower():
                replace_final = "sedici.identifier.other"
                logger.info(
                    "[TargetCrosswalk Guardrail] Campo '%s' no catalogado redirigido a 'sedici.identifier.other'",
                    replace
                )
            else:
                logger.warning(
                    "[TargetCrosswalk Guardrail] Campo '%s' no existe en el catálogo de SEDICI. Mapeo descartado.",
                    replace
                )
                continue

        validated.append(
            build_mapping_entry(
                left=left,
                replace=replace_final,
                default=m.get("default", ""),
                required=bool(m.get("required", False)),
                filter_rule=m.get("filter", "trim"),
            )
        )

    return validated


def build_and_save_target_config(
    level1_mappings: list[dict[str, Any]],
    level2_mappings: list[dict[str, Any]],
    source_config_path_or_dict: Any,
    output_path: str,
) -> str:
    """
    Fusiona mappings del Nivel 1 y Nivel 2, extrae opciones de separador y delimitador,
    y guarda el archivo JSON compatible con CrosswalkClient reutilizando la utilidad base.
    """
    merged_mappings: list[dict[str, Any]] = []
    seen_lefts: set[str] = set()

    # level1_mappings se procesa primero
    for m in itertools.chain(level1_mappings, level2_mappings):
        left = m["left"]
        if left not in seen_lefts:
            merged_mappings.append(m)
            seen_lefts.add(left)

    # Extraer opciones de separador
    _, source_opts = read_crosswalk_config(source_config_path_or_dict)
    options = {
        "original_separator": source_opts.get("original_separator", "||"),
        "replace_separator": source_opts.get("replace_separator", "||"),
        "file_delimiter": source_opts.get("file_delimiter", ","),
    }

    return save_crosswalk_config_json(
        mappings=merged_mappings,
        settings=options,
        output_path=output_path,
    )
