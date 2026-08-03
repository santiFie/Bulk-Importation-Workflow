"""
Nodo agente para la generación del crosswalk config del repositorio origen.

Implementa el Paso 2a del pipeline en cuatro fases independientes:

  Fase 1 — Mapeo de columnas (LLM, única herramienta):
      El LLM recibe solo las cabeceras y valores de muestra del CSV. Llama a
      `save_column_mappings` con el array de mappings. No recibe información
      sobre separadores ni settings.

  Fase 2 — Detección del separador (Python-first, ReAct como fallback):
      2a. `detect_separator()` analiza los valores reales de las columnas
          mapeadas a 'author' / 'subject' y clasifica el separador sin LLM.
      2b. Si no puede clasificarlo, un agente ReAct focalizado itera con
          `test_regex_on_samples` hasta encontrar un patrón válido (máx. 5 iter.).

  Fase 3 — Construcción y guardado del config JSON:
      Combina los mappings (Fase 1) con el separador (Fase 2) y escribe el
      archivo JSON final en disco.

  Fase 4 — Validación determinista:
      Ejecuta el crosswalk sobre 3 filas reales y verifica que las columnas
      críticas (id, title, author, date) estén presentes y que el separador
      haya producido splits en 'author'. No invoca ningún LLM adicional.
      Si la validación falla, reintenta Fase 1 con feedback (máx. 1 reintento).
"""

import csv as _csv_mod
import os
import json
from typing import Any, Optional

from pydantic import BaseModel
from pydantic import Field as PydanticField

from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_groq import ChatGroq
from langchain_nvidia_ai_endpoints import ChatNVIDIA
from langchain_openai import ChatOpenAI
from langsmith import traceable
from langgraph.types import interrupt

from core.utils.config import config
from core.utils.prompt_loader import load_agent_prompt
from core.nodes.crosswalk_agent.helpers import (
    _read_csv_head,
    _format_csv_head_for_prompt,
    _build_generic_columns_description,
    detect_separator,
    _validate_config_deterministic,
)

from core.utils.get_local_model import get_model
# Set to True to use local Ollama model, False to use remote OpenAI model
USE_LOCAL_MODEL = False

# ---------------------------------------------------------------------------
# Esquema Pydantic para el tool call de Fase 1
# ---------------------------------------------------------------------------

class ColumnMapping(BaseModel):
    """Mapeo de una columna CSV origen a un campo destino genérico."""

    left: str = PydanticField(
        description=(
            "Nombre exacto de la cabecera CSV origen. "
            "Soporta concatenación ('ColA+ColB') y wildcard ('author*')."
        )
    )
    replace: str = PydanticField(
        description="Nombre del campo destino genérico (ej: 'title', 'author', 'date')."
    )
    required: bool = PydanticField(
        default=False,
        description="True descarta la fila completa si este campo está vacío.",
    )
    default: Optional[str] = PydanticField(
        default=None,
        description="Valor por defecto cuando el campo está vacío. null si no aplica.",
    )
    filter: str = PydanticField(
        default="trim",
        description="Filtro a aplicar al valor: 'trim', 'lowercase', 'trim|lowercase' o ''.",
    )


# ---------------------------------------------------------------------------
# Utilidades internas
# ---------------------------------------------------------------------------

def _detect_file_delimiter(csv_path: str) -> str:
    """Detecta el delimitador de columnas del CSV usando csv.Sniffer."""
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            sample = f.read(4096)
        dialect = _csv_mod.Sniffer().sniff(sample)
        return dialect.delimiter
    except Exception:
        return ","


def _find_column(name: str, row: dict) -> Optional[str]:
    """Busca una columna en un dict de forma case-insensitive."""
    if name in row:
        return name
    name_lower = name.lower()
    for key in row:
        if key.lower() == name_lower:
            return key
    return None


def _build_fallback_mappings(head_rows: list[dict]) -> list[dict]:
    """Genera mappings 1:1 de fallback cuando el LLM no produce resultado."""
    if not head_rows:
        return []
    return [
        {"left": col, "replace": col, "default": "", "required": False, "filter": "trim"}
        for col in head_rows[0].keys()
    ]


def _save_config_file(
    mappings: list[dict],
    separator_info: dict[str, str],
    file_delimiter: str,
    config_output_path: str,
    base_dir: str,
) -> None:
    """Construye el JSON de crosswalk config y lo escribe en disco."""
    settings: dict = {
        "replace_separator": "|",
        "file_delimiter": file_delimiter,
    }
    if separator_info["type"] == "regex":
        settings["original_separator"] = "||"  # placeholder para el motor
        settings["separator_regex"] = separator_info["value"]
    else:
        settings["original_separator"] = separator_info.get("value") or "||"

    config_json = [mappings, settings]
    os.makedirs(base_dir, exist_ok=True)
    with open(config_output_path, "w", encoding="utf-8") as f:
        json.dump(config_json, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Fase 2b — Agente ReAct focalizado para inferencia de regex
# ---------------------------------------------------------------------------

def _phase2b_react_regex(
    llm: ChatGroq | ChatOpenAI | ChatNVIDIA,
    csv_path: str,
    column: str,
    head_rows: list[dict],
) -> Optional[str]:
    """
    Lanza un agente ReAct mínimo para inferir un regex separador cuando
    detect_separator() no pudo clasificar el patrón.

    El agente solo tiene acceso a `test_regex_on_samples` y se le instruye
    a devolver el regex final como texto plano cuando lo haya validado.

    Args:
        llm:       Instancia del LLM a usar.
        csv_path:  Path al CSV fuente.
        column:    Nombre de la columna a analizar.
        head_rows: Filas de muestra ya leídas.

    Returns:
        El patrón regex validado, o None si no se encontró uno adecuado.
    """
    @tool
    def test_regex_on_samples(pattern: str, column: str) -> str:
        """
        Testea un patrón regex contra una columna del CSV.

        Usá esta herramienta para verificar que el regex divide correctamente
        los campos multivaluados antes de dar tu respuesta final.
        Usá preferentemente zero-width assertions (lookahead/lookbehind) para
        no consumir caracteres del valor original.
        Ejemplo canónico para autores concatenados ("G. AadE. AakvaagB. Abbott"):
          r"(?<=[a-z])(?=[A-Z])"  <- divide donde minúscula va pegada a Mayúscula.

        Args:
            pattern: Patrón regex compatible con re.split() y re.sub().
            column:  Nombre exacto de la columna del CSV a probar.

        Returns:
            Valor original y tokens resultantes para cada fila de muestra.
        """
        import re as _re

        sample_rows = _read_csv_head(csv_path, n=20)
        if not sample_rows:
            return "Error: No se pudieron leer filas de muestra del CSV."

        matched_col = _find_column(column, sample_rows[0])
        if not matched_col:
            return (
                f"Error: columna '{column}' no encontrada. "
                f"Disponibles: {list(sample_rows[0].keys())}"
            )

        try:
            compiled = _re.compile(pattern)
        except _re.error as exc:
            return f"Error: patrón regex inválido '{pattern}': {exc}"

        lines = [f"Patrón: '{pattern}' | Columna: '{matched_col}'", ""]
        rows_with_multiple = 0
        tested = 0
        for i, row in enumerate(sample_rows):
            val = row.get(matched_col, "").strip()
            if not val:
                continue
            tested += 1
            val_to_test = val[:250]
            tokens = [t.strip() for t in compiled.split(val_to_test) if t and t.strip()]
            if len(tokens) > 1:
                rows_with_multiple += 1
            display_val = val_to_test[:100] + "..." if len(val) > 100 else val_to_test
            display_tokens = tokens[:8]
            if len(tokens) > 8:
                display_tokens.append(f"... (+{len(tokens)-8} más)")
            lines.append(f"Fila {i+1} — original : {display_val}")
            lines.append(f"Fila {i+1} — tokens ({len(tokens)}): {display_tokens}")
            lines.append("")

        pct = f"{rows_with_multiple}/{tested}"
        lines.append(f"Resumen: {pct} filas no vacías produjeron más de 1 token.")
        if tested > 0 and rows_with_multiple / tested < 0.5:
            lines.append(
                "⚠ ATENCIÓN: menos del 50% de filas se dividieron. "
                "El patrón puede ser demasiado restrictivo."
            )
        return "\n".join(lines)

    sub_llm = llm.bind_tools([test_regex_on_samples])
    react_prompt = load_agent_prompt("regex_react_agent", column=column)
    sub_messages: list = [SystemMessage(react_prompt)]

    for _ in range(5):
        response = sub_llm.invoke(sub_messages)
        sub_messages.append(response)

        if not response.tool_calls:
            candidate = response.content.strip().strip("`'\" \n")
            print(f"[Fase 2b] LLM propuso regex final: {candidate!r} — evaluando contra muestras...")
            # Verificar que el candidato es un regex válido
            import re as _re
            try:
                compiled = _re.compile(candidate)
                # Validar rápidamente contra las muestras
                sample_values = [
                    row.get(_find_column(column, row) or column, "")
                    for row in head_rows
                    if row.get(_find_column(column, row) or column, "").strip()
                ]
                if sample_values:
                    splits = [len(compiled.split(v)) for v in sample_values]
                    multi = sum(s > 1 for s in splits)
                    pct = multi / len(splits)
                    print(
                        f"[Fase 2b] Regex {candidate!r}: {multi}/{len(splits)} muestras divididas ({pct:.0%})"
                        f" → {'✓ aceptado' if pct > 0.5 else '✗ rechazado (cobertura insuficiente)'}"
                    )
                    if pct > 0.5:
                        return candidate
            except _re.error as exc:
                print(f"[Fase 2b] Regex inválido descartado: {candidate!r} — error: {exc}")
            return None  # Regex inválido o no matchea

        for tc in response.tool_calls:
            if tc["name"] == "test_regex_on_samples":
                res = test_regex_on_samples.invoke(tc["args"])
            else:
                res = f"Tool desconocida: {tc['name']}"
            sub_messages.append(ToolMessage(content=res, tool_call_id=tc["id"]))

    return None


# ---------------------------------------------------------------------------
# Nodo principal
# ---------------------------------------------------------------------------

@traceable(name="generate_source_crosswalk_config", run_type="tool")
def generate_source_crosswalk_config(state: dict) -> dict[str, Any]:
    """
    Paso 2a (agente) — Genera el crosswalk config en cuatro fases:
    mapping LLM → detección Python → construcción config → validación determinista.

    Returns:
        dict con source_crosswalk_config actualizado (path al JSON generado).
    """
    csv_path = state["source_csv_path"]
    source_name = state.get("source_name", "unknown")

    head_rows = _read_csv_head(csv_path, n=5)
    csv_head_text = _format_csv_head_for_prompt(head_rows)
    file_delimiter = _detect_file_delimiter(csv_path)

    base_dir = os.path.dirname(csv_path) or "."
    config_output_path = os.path.join(base_dir, f"crosswalk_config_{source_name}.json")

    if os.path.isfile(config_output_path):
        print(f"[generate_source_crosswalk_config] Reusando config existente: {config_output_path}")
        return {"source_crosswalk_config": config_output_path}

    llm = get_model(provider="groq", model=config.CROSSWALK_MODEL)

    # =========================================================================
    # FASE 1 — Mapeo de columnas (LLM, herramienta única)
    # =========================================================================

    def _run_phase1(feedback: Optional[str] = None) -> list[dict]:
        """Ejecuta la Fase 1 (mapeo LLM). Acepta feedback para reintento."""
        mappings_draft: list[dict] = []

        @tool
        def save_column_mappings(mappings: list[ColumnMapping]) -> str:
            """
            Guarda el borrador de mapeos de columnas propuesto.

            Llamar con el array completo de mapeos.
            NO incluir información de separadores.
            Los campos 'required', 'default' y 'filter' son opcionales:
            si no se indican, usan sus valores por defecto (false, null y 'trim').

            Args:
                mappings: Array de objetos ColumnMapping con left y replace
                          como campos obligatorios.

            Returns:
                Confirmación con la cantidad de mapeos guardados.
            """
            nonlocal mappings_draft
            # Convertir a dict para mantener compatibilidad con el resto del pipeline
            mappings_draft = [m.model_dump() for m in mappings]
            return f"OK: {len(mappings)} mapeos guardados."

        generic_desc = _build_generic_columns_description()
        system_prompt = load_agent_prompt(
            "crosswalk_agent",
            generic_desc=generic_desc,
            csv_head=csv_head_text,
        )
        if feedback:
            system_prompt += f"\n\n--- FEEDBACK DE VALIDACIÓN ANTERIOR ---\n{feedback}\nCorregí los mapeos considerando este feedback."

        phase1_llm = llm.bind_tools([save_column_mappings])
        messages: list = [SystemMessage(system_prompt)]

        print("[Fase 1] Generando mapeos de columnas con LLM")
        for iteration in range(3):
            response = phase1_llm.invoke(messages)
            messages.append(response)

            if response.tool_calls:
                for tc in response.tool_calls:
                    if tc["name"] == "save_column_mappings":
                        result_msg = save_column_mappings.invoke(tc["args"])
                    else:
                        result_msg = f"Tool desconocida: {tc['name']}"
                    messages.append(ToolMessage(content=result_msg, tool_call_id=tc["id"]))
                if mappings_draft:
                    print(f"[Fase 1] {len(mappings_draft)} mapeos guardados en iteración {iteration + 1}")
                    break
            else:
                print("[Fase 1] LLM no llamó herramientas — sin resultado")
                break

        if not mappings_draft:
            print("[Fase 1] Fallback: usando mapeo 1:1")
            return _build_fallback_mappings(head_rows)

        return mappings_draft

    mappings = _run_phase1()

    # =========================================================================
    # FASE 2 — Detección del separador (Python-first, ReAct como fallback)
    # =========================================================================
    print("[generate_source_crosswalk_config] Fase 2: detectando separador")

    # Identificar columnas mapeadas a 'author' o 'subject'
    multi_value_cols: list[str] = []
    for mapping in mappings:
        if mapping.get("replace") in {"author", "subject"}:
            left = mapping.get("left", "")
            multi_value_cols.extend(c.strip() for c in left.split("+") if c.strip())

    separator_info: dict[str, str] = {"type": "unknown", "value": ""}

    print(f"[Fase 2] Columnas multivaluadas detectadas (author/subject): {multi_value_cols or '(ninguna)'}")
    if multi_value_cols and head_rows:
        # Recolectar valores de muestra de las columnas multivaluadas
        sample_values: list[str] = []
        for col in set(multi_value_cols):
            matched = _find_column(col, head_rows[0])
            if matched:
                sample_values.extend(
                    row[matched].strip()
                    for row in head_rows
                    if row.get(matched, "").strip()
                )

        if sample_values:
            print(f"[Fase 2a] Analizando {len(sample_values)} valores de muestra de columnas: {sorted(set(multi_value_cols))}")
            separator_info = detect_separator(sample_values, llm)
            print(f"[Fase 2a] Resultado: type={separator_info['type']!r}, value={separator_info['value']!r}")

    # Fase 2b: ReAct focalizado si Python no pudo clasificar
    if separator_info["type"] == "unknown" and multi_value_cols:
        print("[Fase 2b] Lanzando agente ReAct para inferencia de regex")
        detected_regex = _phase2b_react_regex(
            llm=llm,
            csv_path=csv_path,
            column=multi_value_cols[0],
            head_rows=head_rows,
        )
        
        if detected_regex:
            from core.nodes.crosswalk_agent.helpers import is_known_regex, save_custom_regex
            import re
            
            # Solo iteramos al humano si el regex NO está en el conocimiento global
            if not is_known_regex(detected_regex):
                
                # Armar el reporte visual para el humano
                compiled = re.compile(detected_regex)
                samples_report = []
                for row in head_rows:
                    val = row.get(_find_column(multi_value_cols[0], row) or multi_value_cols[0], "").strip()
                    if val:
                        tokens = [t.strip() for t in compiled.split(val) if t.strip()]
                        samples_report.append({"original": val, "tokens": tokens})
                
                print("[HITL] Regex desconocido detectado. Pausando para validación humana...")
                
                # Pausamos la ejecución. El sistema orquestador (UI/CLI) atrapará esta interrupción.
                # Al reanudarse, 'human_response' contendrá el payload inyectado por el usuario.
                human_response = interrupt({
                    "action_required": "validate_regex",
                    "proposed_regex": detected_regex,
                    "column": multi_value_cols[0],
                    "samples": samples_report
                })
                
                if human_response.get("status") == "accepted":
                    final_regex = human_response.get("regex", detected_regex)
                    separator_info = {"type": "regex", "value": final_regex}
                    print(f"[Fase 2b] Regex final tras HITL: {final_regex!r}")
                    save_custom_regex(final_regex) # ¡Aprendizaje global para el futuro!
                else:
                    separator_info = {"type": "literal", "value": "||"}
                    print("[Fase 2b] Humano rechazó el regex; usando fallback '||'")
            else:
                # Es un regex generado por ReAct pero que ya es conocido, se acepta automáticamente.
                separator_info = {"type": "regex", "value": detected_regex}
                print(f"[Fase 2b] Regex aprobado (ya conocido): {detected_regex!r}")
                
        else:
            separator_info = {"type": "literal", "value": "||"}
            print("[Fase 2b] Sin resultado; usando fallback '||'")

    # =========================================================================
    # FASE 3 — Construcción y guardado del config
    # =========================================================================
    print("[generate_source_crosswalk_config] Fase 3: guardando config")
    _save_config_file(mappings, separator_info, file_delimiter, config_output_path, base_dir)
    sep_log = (
        f"regex={separator_info['value']!r}"
        if separator_info["type"] == "regex"
        else f"literal={separator_info.get('value', '||')!r}"
    )
    print(f"[Fase 3] Config guardado: {len(mappings)} mapeos, {sep_log}")

    # =========================================================================
    # FASE 4 — Validación determinista (sin LLM)
    # =========================================================================
    print("[generate_source_crosswalk_config] Fase 4: validación determinista")
    validation = _validate_config_deterministic(csv_path, config_output_path)
    print(f"[Fase 4] {validation['message']}")

    if not validation["ok"]:
        if validation.get("missing"):
            # Reintento único de Fase 1 con feedback completo enfocado
            feedback = f"Validación fallida. Faltan las siguientes columnas críticas en el resultado: {', '.join(validation['missing'])}.\n"
            feedback += f"Mensaje detallado del sistema:\n{validation['message']}\n\n"
            feedback += (
                "⚠️ ANÁLISIS DE CAUSAS COMUNES ⚠️\n"
                "1. CONFUSIÓN VALOR-CABECERA: Usaste un valor de celda de las muestras en lugar del NOMBRE EXACTO de la cabecera en el campo 'left'.\n"
                "2. COLUMNA INEXISTENTE: Inventaste una columna o la escribiste mal. Verificá la lista de columnas proporcionada.\n"
                "3. FILTRADO AGRESIVO: Configuraste 'required': true en una columna que a veces está vacía, provocando que toda la fila se elimine en la validación.\n\n"
                "INSTRUCCIÓN CRÍTICA: Reflexioná paso a paso sobre el error. Luego, vuelve a invocar 'save_column_mappings' "
                "con la lista COMPLETA de TODOS los mapeos corregidos. No devuelvas información parcial."
            )
            print("[Fase 4] Reintentando Fase 1 con feedback")
            mappings = _run_phase1(feedback=feedback)
            _save_config_file(mappings, separator_info, file_delimiter, config_output_path, base_dir)
            validation2 = _validate_config_deterministic(csv_path, config_output_path)
            print(f"[Fase 4 reintento] {validation2['message']}")
        else:
            print("[Fase 4] La validación falló por problemas de separación, pero los mapeos de columnas son correctos. No se re-ejecuta Fase 1.")

    return {"source_crosswalk_config": config_output_path}


# ---------------------------------------------------------------------------
# Fallback global: config 1:1 si todo falla
# ---------------------------------------------------------------------------

def _generate_fallback_config(head_rows: list[dict], output_path: str) -> None:
    """
    Genera un config de crosswalk básico como fallback de último recurso.
    Mapea cada columna del CSV a sí misma (rename directo) con trim.
    """
    if not head_rows:
        raise ValueError("No hay datos CSV para generar el config de fallback")

    mappings = [
        {
            "left": col,
            "replace": col,
            "default": "",
            "required": False,
            "filter": "trim",
        }
        for col in head_rows[0].keys()
    ]

    config_json = [
        mappings,
        {
            "original_separator": "||",
            "replace_separator": "|",
            "file_delimiter": ",",
        },
    ]

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(config_json, f, indent=2, ensure_ascii=False)

    print(f"[_generate_fallback_config] Config de fallback guardado en {output_path}")
