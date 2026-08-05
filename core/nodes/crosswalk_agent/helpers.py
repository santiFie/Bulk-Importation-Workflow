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
import tempfile
from pathlib import Path
from typing import Any, Optional

from langchain_groq import ChatGroq

from core.utils.config import config
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
_LITERAL_CANDIDATES: list[str] = ["||", "|", ";;", ";", ",,"]

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


def detect_separator(values: list[str], llm: Optional[ChatGroq] = None) -> dict[str, str]:
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
        validator_llm = ChatGroq(
            model=getattr(config, "SEARCHER_MODEL", "llama-3.3-70b-versatile"),
            temperature=0,
        )
        response = validator_llm.invoke(prompt)
        return response.content

    except Exception as exc:
        return f"Error running separator validation: {repr(exc)}"
