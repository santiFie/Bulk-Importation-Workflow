"""
Helpers del agente generador de crosswalk config.

Este módulo agrupa las funciones de soporte utilizadas por el nodo
`generate_source_crosswalk_config`:

  - Lectura y formateo de muestras del CSV fuente.
  - Ejecución del crosswalk sobre un subconjunto de filas para validación
    (vía API REST, usando CrosswalkClient).
  - Descripción de las columnas del formato genérico destino.
  - Detección determinista de separadores multivalor (sin LLM).
  - Validación determinista del config generado (sin LLM).
  - [Legado] Validación del separador con LLM secundario (deprecado).
"""

import io
import os
import re
import sys
import json
import logging
import tempfile
import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

from langchain_core.language_models.chat_models import BaseChatModel

from core.utils.config import config
from core.utils.get_local_model import FallbackLLM
from core.utils.prompt_loader import load_agent_prompt
from core.clients.crosswalk_client import CrosswalkClient, CrosswalkApiError


# ---------------------------------------------------------------------------
# Lectura y formateo de muestras del CSV
# ---------------------------------------------------------------------------

def _read_csv_head(csv_path: str, n: int = 5) -> list[dict[str, str]]:
    """
    Lee las primeras n filas del CSV fuente y las devuelve
    como lista de dicts. Infiere el delimiter automáticamente.
    Seleccionamos basándonos en la cantidad de caracteres, aplicándolo a columnas 
    que probablemente sean de autores para evitar falsos positivos con títulos largos.
    """
    import csv as _csv
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            sample = f.read(4096)
            f.seek(0)
            try:
                dialect = _csv.Sniffer().sniff(sample)
            except _csv.Error:
                dialect = _csv.excel()
            reader = _csv.DictReader(f, delimiter=dialect.delimiter)
            rows = []
            
            # Heurística: seleccionar los que tengan más de N caracteres
            # (con N aprox más de un autor) y leer los primeros X caracteres (a lo sumo 5 autores).
            MIN_CHARS = 35
            MAX_CHARS = 100
            
            fallback_rows = []
            for row in reader:
                if len(rows) >= n:
                    break
                
                has_long_author = False
                processed_row = {}
                for k, v in row.items():
                    if v:
                        # Buscamos columnas que puedan ser autores para aplicar el criterio de longitud
                        is_author_col = k and any(x in k.lower() for x in ["author", "autor", "creator", "person"])
                        if is_author_col and len(v) >= MIN_CHARS:
                            has_long_author = True
                        
                        # Truncamos los primeros X caracteres
                        processed_row[k] = v[:MAX_CHARS]
                    else:
                        processed_row[k] = v
                
                if len(fallback_rows) < n:
                    fallback_rows.append(processed_row)
                    
                if has_long_author:
                    rows.append(processed_row)
            
            # Si no hay suficientes filas que cumplan la condición, rellenamos
            if len(rows) < n:
                for row in fallback_rows:
                    if len(rows) >= n:
                        break
                    if row not in rows:
                        rows.append(row)
                        
        return rows
    except Exception as exc:
        print(f"[_read_csv_head] Error: {repr(exc)}")
        return []


def _format_csv_head_for_prompt(head_rows: list[dict]) -> str:
    """
    Formatea header y primeras filas del CSV como texto estructurado
    para incluir en el prompt del LLM.
    """
    if not head_rows:
        return "(CSV vacío o no se pudo leer)"

    columns = list(head_rows[0].keys())
    lines = [
        f"Columnas ({len(columns)}): {', '.join(columns)}",
        "",
        "Muestras de valores por columna (primeras 3 filas no vacías):",
    ]
    MAX_SAMPLE_CHARS = 30
    for col in columns:
        values = [row.get(col, "") for row in head_rows]
        non_empty = [v for v in values if v.strip()]
        sample = [v[:MAX_SAMPLE_CHARS] for v in non_empty[:3]] if non_empty else ["(vacío)"]
        lines.append(f"  - {col}: {sample}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Validación del crosswalk sobre una muestra de filas
# ---------------------------------------------------------------------------

def _create_validation_sample(
    csv_path: str, config_path: str, n: int = 3
) -> list[dict]:
    """
    Ejecuta el crosswalk sobre las primeras n filas del CSV via API REST.

    Escribe las primeras n filas en un CSV temporal, lo envía junto con el
    config al endpoint POST /api/tool/crosswalk/ a través de CrosswalkClient,
    y parsea el CSV resultante devuelto por la API.

    Args:
        csv_path:    Path al CSV fuente completo.
        config_path: Path al JSON de crosswalk config generado.
        n:           Cantidad de filas a usar en la muestra de validación.

    Returns:
        Lista de dicts con las filas transformadas, o lista vacía si falla.
    """
    import csv as _csv_mod

    head = _read_csv_head(csv_path, n=n)
    if not head:
        return []

    columns = list(head[0].keys())

    # Escribir la muestra en un CSV temporal para enviarlo a la API
    tf_in = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8"
    )
    writer = _csv_mod.DictWriter(tf_in, fieldnames=columns)
    writer.writeheader()
    writer.writerows(head)
    tf_in.close()

    try:
        client = CrosswalkClient()
        result_bytes = client.run_crosswalk(
            csv_path=tf_in.name,
            config_path=config_path,
            description={"source": "validation_sample", "rows": n},
        )
        # Parsear el CSV resultante desde los bytes devueltos
        reader = _csv_mod.DictReader(io.StringIO(result_bytes.decode("utf-8")))
        return list(reader)
    except CrosswalkApiError as exc:
        print(f"[_create_validation_sample] Error de API: {repr(exc)}")
        return []
    except Exception as exc:
        print(f"[_create_validation_sample] Error inesperado: {repr(exc)}")
        return []
    finally:
        try:
            os.unlink(tf_in.name)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Descripción de columnas del formato genérico destino
# ---------------------------------------------------------------------------

GENERIC_COLUMNS = {
    "id": "Identificador único del documento (URI, handle, URL, etc.). OBLIGATORIO. Si no hay columna de URL, usá el DOI como identificador.",
    "title": "Título del documento (OBLIGATORIO para la deduplicación)",
    "author": "Autor/es",
    "date": "Fecha de publicación (generalmente año)",
    "description": "Resumen o abstract",
    "subject": "Palabras clave o materias",
    "type": "Tipo de documento (artículo, libro, tesis, etc.)",
    "subtitle": "Subtítulo",
    "issn": "ISSN",
    "doi": "DOI",
    "isbn": "ISBN",
    "citation": "Representa el nombre de la revista o el título del libro en el que se publicó",
}


def _build_generic_columns_description() -> str:
    """Genera la descripción de columnas destino para el prompt."""
    lines = []
    for col, desc in GENERIC_COLUMNS.items():
        lines.append(f"  - {col}: {desc}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Detección determinista de separador (sin LLM)
# ---------------------------------------------------------------------------

# Estrategias conocidas: (patrón_compilado, tipo, valor_canónico)
_REGEX_STRATEGIES: list[tuple] = [
    # Autores concatenados sin separador: "G. AadE. AakvaagB. Abbott"
    # (re.compile(r"(?<=[a-z])(?=[A-Z])"), "regex", r"(?<=[a-z])(?=[A-Z])"),
    # Autores separados por punto+espacio+mayúscula: "Doe J. Smith A."
    (re.compile(r"\.\s+(?=[A-Z])"), "regex", r"\.\s+(?=[A-Z])"),
]
_LITERAL_CANDIDATES: list[str] = ["||", "|", ";;", ";", ",,", ",", "."]

CUSTOM_REGEX_FILE = Path(__file__).parent / "custom_regexes.json"

def get_all_regex_strategies() -> list[tuple]:
    """Combina los regex hardcodeados con los aprobados dinámicamente por humanos."""
    strategies = list(_REGEX_STRATEGIES)
    
    if CUSTOM_REGEX_FILE.exists():
        try:
            with open(CUSTOM_REGEX_FILE, "r", encoding="utf-8") as f:
                customs = json.load(f)
                for c in customs:
                    strategies.append((re.compile(c["pattern"]), "regex", c["pattern"]))
        except Exception as e:
            print(f"Error cargando custom regexes: {e}")
            
    return strategies

def is_known_regex(pattern: str) -> bool:
    """Verifica si un patrón ya está en nuestra base de conocimientos."""
    strategies = get_all_regex_strategies()
    return any(pattern == s[2] for s in strategies)

def save_custom_regex(pattern: str) -> None:
    """Guarda un regex aprobado por el humano para aprendizaje global."""
    if is_known_regex(pattern):
        return
        
    customs = []
    if CUSTOM_REGEX_FILE.exists():
        try:
            with open(CUSTOM_REGEX_FILE, "r", encoding="utf-8") as f:
                customs = json.load(f)
        except Exception:
            pass
            
    customs.append({"pattern": pattern})
    
    with open(CUSTOM_REGEX_FILE, "w", encoding="utf-8") as f:
        json.dump(customs, f, indent=2, ensure_ascii=False)


def detect_separator(values: list[str], llm: Optional[BaseChatModel] = None) -> dict[str, str]:
    """
    Detecta deterministamente candidatos a separador en una lista de valores
    de una columna multivaluada, y usa un LLM como supervisor de la partición resultante.

    Args:
        values: Lista de strings tomados de la columna multivaluada del CSV.
        llm: Instancia del modelo para supervisión semántica.

    Returns:
        Dict con:
          - "type":  "literal" | "regex" | "unknown"
          - "value": El separador o patrón detectado (vacío si "unknown")
    """
    non_empty = [v for v in values if v and v.strip()]
    if not non_empty:
        return {"type": "unknown", "value": ""}

    sample = non_empty[0] # Tomamos una muestra representativa
    options = {}
    option_counter = 1
    
    # 1. Recolectar opciones de literales
    for sep in _LITERAL_CANDIDATES:
        tokens = [t.strip() for t in sample.split(sep) if t.strip()]
        if len(tokens) > 1:
            options[option_counter] = {"type": "literal", "value": sep, "tokens": tokens}
            option_counter += 1
            
    # 2. Recolectar opciones de Regex
    for compiled, kind, canonical in get_all_regex_strategies():
        tokens = [t.strip() for t in compiled.split(sample) if t.strip()]
        if len(tokens) > 1:
            options[option_counter] = {"type": kind, "value": canonical, "tokens": tokens}
            option_counter += 1

    if not options:
        return {"type": "unknown", "value": ""}

    if not llm:
        # Fallback sin LLM (devuelve la primera opción viable)
        print("[detect_separator] Sin LLM proporcionado, tomando la primera opción válida.")
        return {"type": options[1]["type"], "value": options[1]["value"]}

    # 3. Supervisión con el LLM (Multiple Choice con Few-Shot y Chain-of-Thought)
    prompt = load_agent_prompt('separator_selector', sample=sample)
    for opt_num, opt_data in options.items():
        prompt += f"OPCION {opt_num}: {opt_data['tokens']}\n"
        
    prompt += (
        "\nProporciona primero tu análisis en una etiqueta <thought> (explica brevemente por qué las opciones son válidas o inválidas) y luego tu respuesta final (el número de opción o 'NINGUNA') en una etiqueta <answer>."
    )
    
    from langchain_core.messages import SystemMessage
    try:
        response = llm.invoke([SystemMessage(content=prompt)]).content.strip()
        print(f"[detect_separator] LLM response:\n{response}")
        
        import re
        match = re.search(r'<answer>\s*(NINGUNA|\d+)\s*</answer>', response, re.IGNORECASE)
        if match:
            ans = match.group(1).upper()
            if ans != "NINGUNA" and ans.isdigit() and int(ans) in options:
                selected = options[int(ans)]
                print(f"[detect_separator] LLM eligió OPCION {ans}: {selected['value']}")
                return {"type": selected["type"], "value": selected["value"]}
            else:
                print(f"[detect_separator] LLM rechazó todas las opciones o dio una respuesta inesperada ({ans}).")
        else:
            print("[detect_separator] Falló la extracción del <answer>. Usando fallback 'unknown'.")
    except Exception as e:
        print(f"[detect_separator] Error invocando supervisor LLM: {e}")
        
    return {"type": "unknown", "value": ""}


# ---------------------------------------------------------------------------
# Validación determinista del config generado (sin LLM)
# ---------------------------------------------------------------------------

def _validate_config_deterministic(
    csv_path: str, config_path: str, n: int = 3
) -> dict[str, Any]:
    """
    Ejecuta el crosswalk sobre las primeras n filas y verifica deterministamente
    que las columnas críticas estén presentes en el output.

    Para la columna 'author', también verifica que el separador haya funcionado:
    si el campo contiene '|', el split fue exitoso.

    Args:
        csv_path:    Path al CSV fuente.
        config_path: Path al JSON de crosswalk config generado.
        n:           Cantidad de filas a usar en la validación.

    Returns:
        Dict con:
          - "ok":      bool — True si la validación pasó.
          - "columns": list[str] — columnas presentes en el output.
          - "missing": list[str] — columnas críticas ausentes.
          - "separator_ok": bool — True si el campo 'author' tiene separadores '|'.
          - "message": str — resumen legible del resultado.
    """
    CRITICAL = ["id", "title", "author", "date", "type"]
    ENRICHABLE_GENERIC_FIELDS = {
        "type", "date", "author", "title", "issn", "citation", "subject", "description", "isbn"
    }

    result = _create_validation_sample(csv_path, config_path, n=n)
    if not result:
        return {
            "ok": False,
            "columns": [],
            "missing": CRITICAL,
            "separator_ok": False,
            "message": (
                "ERROR: No se produjeron filas de salida. "
                "Verificá que los 'left' coincidan con los nombres de columna del CSV "
                "y que los campos required tengan valores."
            ),
        }

    cols = list(result[0].keys())
    missing = [c for c in CRITICAL if c not in cols] # Columnas criticas ausentes

    # Verificar separador de forma inteligente (heurística y casos de prueba conocidos)
    separator_ok = True
    separation_issues = []
    
    for field in ["author", "subject"]:
        if field in cols:
            for i, row in enumerate(result):
                # La API devuelve los valores unidos por '|'
                values = [v.strip() for v in row.get(field, "").split("|") if v.strip()]
                count = len(values)
                
                # Heurística general: si hay un string muy largo (ej. +40 chars) y no se separó en múltiples partes
                long_values = [v for v in values if len(v) > 40]
                # Solo consideramos error si es anormalmente largo y no hubo casi divisiones
                if long_values and count < 3:
                    separator_ok = False
                    separation_issues.append(
                        f"Fila {i+1} ({field}): posible falla, valor muy largo sin separar ('{long_values[0][:30]}...')."
                    )

    ok = not missing and separator_ok
    parts = [f"Columnas ({len(cols)}): {', '.join(cols)}"]
    if missing:
        parts.append(f"FALTAN columnas críticas: {', '.join(missing)}")
        enrichable_missing = [f for f in missing if f in ENRICHABLE_GENERIC_FIELDS]
        if enrichable_missing:
            parts.append(
                f"Tip: Si el CSV contiene una columna con DOI, podés usar la herramienta "
                f"'enrich_source_columns_from_doi' con target_fields={enrichable_missing} "
                f"para obtenerlos automáticamente desde Crossref antes de guardar los mappings."
            )
    if not separator_ok:
        parts.append("ADVERTENCIA: Fallas detectadas en la separación de valores:")
        parts.extend([f"  - {issue}" for issue in separation_issues])
    if ok:
        parts.append("Validación OK.")

    return {
        "ok": ok,
        "columns": cols,
        "missing": missing,
        "separator_ok": separator_ok,
        "message": "\n".join(parts),
    }


# ---------------------------------------------------------------------------
# [Legado] Validación del separador multivalor con un LLM secundario
# ---------------------------------------------------------------------------

def _validate_separator_with_llm(csv_path: str, config_path: str) -> str:
    """
    Valida la configuración del separador multivalor del crosswalk generado
    comparándola contra valores reales de las columnas author/subject del CSV
    fuente mediante un LLM especializado.

    Soporta tanto separadores literales (`original_separator`) como expresiones
    regulares (`separator_regex`, compatible con re.sub()).

    Args:
        csv_path: Path al CSV fuente.
        config_path: Path al JSON de crosswalk config generado.

    Returns:
        Análisis textual indicando si el separador es válido o proponiendo corrección.
    """
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        if not isinstance(cfg, list) or len(cfg) < 2:
            return "Error: The configuration JSON does not match the expected structure [mappings, settings]."

        mappings, settings = cfg[0], cfg[1]
        original_separator = settings.get("original_separator", "")
        separator_regex = settings.get("separator_regex", None)

        # Descripción legible del separador configurado
        if separator_regex:
            separator_config_description = (
                f"Configured separator type: REGEX (re.sub pattern)\n"
                f"Pattern: \"{separator_regex}\"\n"
                f"Fallback literal separator: \"{original_separator}\""
            )
        else:
            separator_config_description = (
                f"Configured separator type: LITERAL\n"
                f"Value: \"{original_separator}\""
            )

        # Identificar columnas mapeadas a 'author' o 'subject'
        target_fields = {"author", "subject"}
        multi_value_cols: list[str] = []
        for mapping in mappings:
            left = mapping.get("left", "")
            replace = mapping.get("replace", "")
            if replace in target_fields and left:
                multi_value_cols.extend([c.strip() for c in left.split("+")])

        if not multi_value_cols:
            return "No multi-value fields ('author' or 'subject') found in the mappings to validate."

        # Leer filas de muestra del CSV fuente
        head_rows = _read_csv_head(csv_path, n=5)
        if not head_rows:
            return "Error: Could not read sample rows from the source CSV file."

        # Recolectar valores de muestra para las columnas mapeadas
        samples_by_col: list[str] = []
        for col in set(multi_value_cols):
            matched_col = col if col in head_rows[0] else None
            if not matched_col:
                for actual_col in head_rows[0].keys():
                    if actual_col.lower() == col.lower():
                        matched_col = actual_col
                        break
            if matched_col:
                vals = []
                for row in head_rows:
                    val = row.get(matched_col, "").strip()
                    if val:
                        truncated = val[:80] + "..." if len(val) > 80 else val
                        vals.append(truncated)
                if vals:
                    samples_by_col.append(f"- Column '{matched_col}': {vals}")

        if not samples_by_col:
            return "No sample data found in the source CSV for mapped multi-value columns."

        # Invocar el LLM validador
        samples_text = "\n".join(samples_by_col)
        prompt = load_agent_prompt(
            "separator_validator",
            separator_config_description=separator_config_description,
            samples_text=samples_text,
        )
        validator_llm = FallbackLLM(
            groq_model=getattr(config, "SEARCHER_MODEL", "openai/gpt-oss-120b"),
            openrouter_model=getattr(config, "SEARCHER_MODEL", "openai/gpt-oss-120b"),
        ).resolve()
        response = validator_llm.invoke(prompt)
        return response.content

    except Exception as exc:
        return f"Error running separator validation: {repr(exc)}"


# ---------------------------------------------------------------------------
# Pre-enriquecimiento de columnas mediante Crossref
# ---------------------------------------------------------------------------

def enrich_source_with_crossref_doi(
    csv_path: str,
    doi_column: str,
    target_fields: list[str] | str = "type",
    output_path: Optional[str] = None,
) -> tuple[list[str], str, dict[str, list[str]]]:
    """
    Enriquece un CSV fuente consultando Crossref vía los DOIs de la columna dada.
    Agrega una o más columnas 'inferred_<campo>' al CSV con los valores obtenidos.

    Args:
        csv_path: Ruta al CSV fuente original.
        doi_column: Nombre de la columna en el CSV que contiene los DOIs.
        target_fields: Campo o lista de campos destino a inferir (ej. 'type' o ['type', 'date']).
        output_path: Ruta del CSV resultante. Si es None, se genera augmented_<original>.csv.

    Returns:
        tuple (added_columns, output_csv_path, sample_values_by_field)
    """
    import csv as _csv
    from concurrent.futures import ThreadPoolExecutor
    from core.clients.enrichers.crossref_enricher import CrossrefEnricher

    if isinstance(target_fields, str):
        fields = [target_fields]
    else:
        fields = list(target_fields)

    with open(csv_path, "r", encoding="utf-8") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = _csv.Sniffer().sniff(sample)
            delimiter = dialect.delimiter
        except _csv.Error:
            delimiter = ","
        reader = _csv.DictReader(f, delimiter=delimiter)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    # Identificar la columna de DOI de forma case-insensitive
    matched_col = None
    for col in fieldnames:
        if col.lower() == doi_column.lower():
            matched_col = col
            break

    if not matched_col:
        for col in fieldnames:
            if "doi" in col.lower():
                matched_col = col
                break

    if not matched_col:
        raise ValueError(f"Columna '{doi_column}' no encontrada en el CSV.")

    enricher = CrossrefEnricher()
    unique_dois = list({
        r[matched_col].strip() for r in rows if r.get(matched_col, "").strip()
    })

    doi_to_vals: dict[str, dict[str, str]] = {}

    def _fetch(doi: str) -> tuple[str, dict[str, str]]:
        try:
            data = enricher.enrich_by_doi(doi, schema="generic")
            return doi, {f: str(data.get(f, "") or "") for f in fields}
        except Exception:
            return doi, {f: "" for f in fields}

    if unique_dois:
        with ThreadPoolExecutor(max_workers=5) as executor:
            for doi, val_dict in executor.map(_fetch, unique_dois):
                doi_to_vals[doi] = val_dict

    added_columns: list[str] = []
    for f in fields:
        new_col = f"inferred_{f}"
        added_columns.append(new_col)
        if new_col not in fieldnames:
            fieldnames.append(new_col)

    sample_values_by_field: dict[str, list[str]] = {f: [] for f in fields}
    for r in rows:
        doi = r.get(matched_col, "").strip()
        field_vals = doi_to_vals.get(doi, {})
        for f in fields:
            inferred = field_vals.get(f, "")
            r[f"inferred_{f}"] = inferred
            samples = sample_values_by_field[f]
            if inferred and len(samples) < 5 and inferred not in samples:
                samples.append(inferred)

    if not output_path:
        base_dir = os.path.dirname(csv_path) or "."
        output_path = os.path.join(base_dir, f"augmented_{os.path.basename(csv_path)}")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = _csv.DictWriter(f, fieldnames=fieldnames, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)

    return added_columns, output_path, sample_values_by_field


# ---------------------------------------------------------------------------
# Verificación de correspondencia de esquema (Drift Detection) y Reutilización
# ---------------------------------------------------------------------------

@dataclass
class DriftReport:
    """
    Reporte de correspondencia de esquema entre un archivo CSV y una configuración de crosswalk.
    """
    is_valid: bool
    missing_columns: list[str] = field(default_factory=list)
    unmapped_columns: list[str] = field(default_factory=list)
    message: str = ""


def _read_csv_headers(csv_path: str) -> list[str]:
    """
    Lee las cabeceras del CSV de entrada infiriendo automáticamente el dialecto.
    """
    import csv as _csv
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = _csv.Sniffer().sniff(sample)
            delimiter = dialect.delimiter
        except _csv.Error:
            delimiter = ","
        reader = _csv.reader(f, delimiter=delimiter)
        first_row = next(reader, None)
        if not first_row:
            return []
        return [col.strip() for col in first_row if col is not None and col.strip()]


def validate_config_against_csv(csv_path: str, config_path: str) -> DriftReport:
    """
    Verifica la correspondencia entre las cabeceras del CSV fuente y los mapeos
    declarados en el crosswalk config JSON (Drift Detection).

    Reglas:
      - Lee las cabeceras del CSV de entrada.
      - Lee el JSON de crosswalk en config_path (la lista mappings es el elemento 0).
      - Extrae las columnas 'left' mapeadas. Si un mapping tiene 'left' compuesto por '+'
        (ej: 'colA+colB'), para un campo required se considera satisfecho si al menos una
        de las alternativas está en el CSV. Si ninguna está presente, es una columna faltante
        ('missing_columns').
      - Detecta 'unmapped_columns': columnas presentes en el CSV que no están referenciadas
        en ningún 'left'.
      - is_valid es True si y solo si missing_columns está vacío.

    Args:
        csv_path: Ruta al archivo CSV fuente.
        config_path: Ruta al archivo JSON de crosswalk config.

    Returns:
        DriftReport con el estado de validación, columnas faltantes, columnas no mapeadas y mensaje.
    """
    if not os.path.isfile(config_path):
        return DriftReport(
            is_valid=False,
            missing_columns=[],
            unmapped_columns=[],
            message=f"El archivo de configuración no existe: {config_path}",
        )
    if not os.path.isfile(csv_path):
        return DriftReport(
            is_valid=False,
            missing_columns=[],
            unmapped_columns=[],
            message=f"El archivo CSV no existe: {csv_path}",
        )

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, list) or len(cfg) == 0:
            return DriftReport(
                is_valid=False,
                missing_columns=[],
                unmapped_columns=[],
                message="Estructura de configuración inválida: se esperaba una lista [mappings, settings].",
            )
        mappings = cfg[0]
        if not isinstance(mappings, list):
            return DriftReport(
                is_valid=False,
                missing_columns=[],
                unmapped_columns=[],
                message="Estructura de configuración inválida: el elemento 0 debe ser una lista de mappings.",
            )
    except Exception as exc:
        return DriftReport(
            is_valid=False,
            missing_columns=[],
            unmapped_columns=[],
            message=f"Error leyendo la configuración JSON: {exc}",
        )

    try:
        csv_columns = _read_csv_headers(csv_path)
        if not csv_columns:
            return DriftReport(
                is_valid=False,
                missing_columns=[],
                unmapped_columns=[],
                message=f"El archivo CSV '{csv_path}' está vacío o no contiene cabeceras válidas.",
            )
    except Exception as exc:
        return DriftReport(
            is_valid=False,
            missing_columns=[],
            unmapped_columns=[],
            message=f"Error leyendo cabeceras del CSV '{csv_path}': {exc}",
        )

    def _matches_candidate(candidate: str, available_cols: list[str]) -> bool:
        for c in available_cols:
            if c == candidate or ('*' in candidate and fnmatch.fnmatch(c, candidate)):
                return True
        return False

    missing_columns: list[str] = []
    all_mapped_parts: list[str] = []

    for m in mappings:
        if not isinstance(m, dict):
            continue
        left = m.get("left", "")
        if not left:
            continue

        parts = [p.strip() for p in left.split("+") if p.strip()]
        all_mapped_parts.extend(parts)

        is_required = m.get("required") in (True, "true", "True", 1)
        if is_required:
            # Para un campo required se considera satisfecho si al menos una de las alternativas está en el CSV.
            # Si ninguna está presente, es una columna faltante ('missing_columns').
            satisfied = any(_matches_candidate(p, csv_columns) for p in parts)
            if not satisfied and left not in missing_columns:
                missing_columns.append(left)

    # Detectar 'unmapped_columns': columnas presentes en el CSV que no están referenciadas en ningún 'left'.
    unmapped_columns: list[str] = []
    for c in csv_columns:
        referenced = False
        for part in all_mapped_parts:
            if c == part or ('*' in part and fnmatch.fnmatch(c, part)):
                referenced = True
                break
        if not referenced and c not in unmapped_columns:
            unmapped_columns.append(c)

    is_valid = (len(missing_columns) == 0)

    if not is_valid:
        msg = f"Drift detectado: Faltan columnas requeridas en el CSV: {', '.join(missing_columns)}."
        if unmapped_columns:
            msg += f" Columnas no mapeadas: {', '.join(unmapped_columns)}."
    elif unmapped_columns:
        msg = f"Configuración válida con advertencia de drift: Columnas no mapeadas en el CSV: {', '.join(unmapped_columns)}."
    else:
        msg = "Configuración válida: El esquema del CSV coincide plenamente con los mappings."

    return DriftReport(
        is_valid=is_valid,
        missing_columns=missing_columns,
        unmapped_columns=unmapped_columns,
        message=msg,
    )


def get_existing_config(state: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Verifica si existe una configuración de crosswalk previa en disco para la fuente
    y valida correspondencia de esquema (Drift Detection) y funcionalidad determinista.

    Flujo:
      - Obtiene config_output_path = os.path.join(base_dir, f"crosswalk_config_{source_name}.json").
      - Si el archivo no existe, retorna None.
      - Ejecuta validate_config_against_csv(csv_path, config_output_path).
      - Si hay missing_columns: loguea advertencia/error de drift y retorna None
        (invalida la caché para forzar re-generación con LLM).
      - Si hay unmapped_columns: loguea advertencia detallada (logger.warning) indicando
        las columnas no mapeadas, pero continúa con la reutilización.
      - Ejecuta validación determinista obligatoria sobre el config cacheado:
        '_validate_config_deterministic(csv_path, config_output_path)'. Si la validación
        falla (ej. faltan columnas críticas 'id', 'title', etc. o falla el crosswalk),
        loguea advertencia y retorna None (invalida la caché para forzar re-generación).
      - Si todo es válido, retorna {"source_crosswalk_config": config_output_path}.

    Args:
        state: Estado del grafo con 'source_csv_path' y opcionalmente 'source_name'.

    Returns:
        Dict con {"source_crosswalk_config": config_output_path} si el config es válido,
        o None si se rechaza la caché.
    """
    csv_path = state.get("source_csv_path", "")
    source_name = state.get("source_name", "unknown")

    if not csv_path:
        logger.warning("[get_existing_config] 'source_csv_path' no fue provisto en el state.")
        return None

    base_dir = os.path.dirname(csv_path) or "."
    config_output_path = os.path.join(base_dir, f"crosswalk_config_{source_name}.json")

    if not os.path.isfile(config_output_path):
        return None

    # 1. Validación de Drift contra el esquema del CSV
    report = validate_config_against_csv(csv_path, config_output_path)

    if report.missing_columns or not report.is_valid:
        logger.warning(
            f"[get_existing_config] Drift detectado en config existente '{config_output_path}': "
            f"Faltan columnas requeridas en '{csv_path}': {report.missing_columns}. "
            "Invalidando caché de crosswalk config para forzar re-generación con LLM."
        )
        return None

    if report.unmapped_columns:
        logger.warning(
            f"[get_existing_config] Advertencia de drift en config existente '{config_output_path}': "
            f"Columnas no mapeadas detectadas en '{csv_path}': {report.unmapped_columns}. "
            "Se continúa con la reutilización de la configuración existente."
        )

    # 2. Validación determinista obligatoria sobre el config cacheado
    try:
        validation = _validate_config_deterministic(csv_path, config_output_path)
        if not validation.get("ok", False):
            logger.warning(
                f"[get_existing_config] Validación determinista fallida para '{config_output_path}' "
                f"con CSV '{csv_path}': {validation.get('message')}. "
                "Invalidando caché de crosswalk config para forzar re-generación con LLM."
            )
            return None
    except Exception as exc:
        logger.warning(
            f"[get_existing_config] Error durante la validación determinista de '{config_output_path}': {exc}. "
            "Invalidando caché de crosswalk config."
        )
        return None

    logger.info(f"[get_existing_config] Configuración existente validada exitosamente: {config_output_path}")
    return {"source_crosswalk_config": config_output_path}
