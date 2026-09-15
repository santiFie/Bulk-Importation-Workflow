"""
Nodo agente para la generación del crosswalk config del repositorio origen.

Implementa el Paso 2a del pipeline aplicando el patrón Template Method mediante
SourceToGenericCrosswalkGenerator:
  - Fase 1: Mapeo asistido por LLM hacia los 12 campos estándar del deduplicador.
  - Fase 2: Detección de separador multivalor (Python-first + ReAct + HITL).
  - Fase 3: Construcción y serialización del archivo de configuración JSON.
  - Fase 4: Validación funcional determinista con CrosswalkClient y feedback para reintento.
"""

import csv as _csv_mod
import json
import logging
import os
import re
from typing import Any, Optional

from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import tool, BaseTool
from langchain_core.language_models.chat_models import BaseChatModel
from langsmith import traceable
from langgraph.types import interrupt

from core.utils.prompt_loader import load_agent_prompt
from core.nodes.crosswalk_base.models import CrosswalkColumnMapping
from core.nodes.crosswalk_base.base import BaseCrosswalkGenerator
from core.nodes.source_to_generic.helpers import (
    GENERIC_COLUMNS,
    _read_csv_head,
    _format_csv_head_for_prompt,
    _build_generic_columns_description,
    detect_separator,
    _validate_config_deterministic,
    enrich_source_with_crossref_doi,
    is_known_regex,
    save_custom_regex,
)

logger = logging.getLogger(__name__)

# Alias para mantener retrocompatibilidad total con posibles imports externos
ColumnMapping = CrosswalkColumnMapping


# ---------------------------------------------------------------------------
# Utilidades internas de inspección de columnas
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


# ---------------------------------------------------------------------------
# Fase 2b — Agente ReAct focalizado para inferencia de regex
# ---------------------------------------------------------------------------

def _phase2b_react_regex(
    llm: BaseChatModel,
    csv_path: str,
    column: str,
    head_rows: list[dict],
) -> Optional[str]:
    """
    Lanza un agente ReAct mínimo para inferir un regex separador cuando
    detect_separator() no pudo clasificar el patrón.
    """
    @tool
    def test_regex_on_samples(pattern: str, column: str) -> str:
        """Testea un patrón regex contra una columna del CSV."""
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
            compiled = re.compile(pattern)
        except re.error as exc:
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
                "ATENCIÓN: menos del 50% de filas se dividieron. "
                "El patrón puede ser demasiado restrictivo."
            )
        return "\n".join(lines)

    sub_llm = llm.bind_tools([test_regex_on_samples])
    react_prompt = load_agent_prompt("regex_react_agent", column=column)
    sub_messages: list = [SystemMessage(content=react_prompt)]

    for _ in range(5):
        response = sub_llm.invoke(sub_messages)
        sub_messages.append(response)

        if not response.tool_calls:
            candidate = response.content.strip().strip("`'\" \n")
            print(f"[Fase 2b] LLM propuso regex final: {candidate!r} — evaluando contra muestras...")
            try:
                compiled = re.compile(candidate)
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
            except re.error as exc:
                print(f"[Fase 2b] Regex inválido descartado: {candidate!r} — error: {exc}")
            return None

        for tc in response.tool_calls:
            if tc["name"] == "test_regex_on_samples":
                res = test_regex_on_samples.invoke(tc["args"])
            else:
                res = f"Tool desconocida: {tc['name']}"
            sub_messages.append(ToolMessage(content=str(res), tool_call_id=tc["id"]))

    return None


# ---------------------------------------------------------------------------
# Implementación del Generador Template Method
# ---------------------------------------------------------------------------

class SourceToGenericCrosswalkGenerator(BaseCrosswalkGenerator):
    """
    Generador de crosswalk hacia el esquema genérico (Paso 2a).
    Especializa BaseCrosswalkGenerator con la detección de separadores de la fuente
    y la validación funcional contra la API de Crosswalk.
    """

    max_llm_iterations = 5

    def get_existing_config(self, state: dict[str, Any]) -> Optional[dict[str, Any]]:
        csv_path = state.get("source_csv_path", "")
        source_name = state.get("source_name", "unknown")
        base_dir = os.path.dirname(csv_path) or "."
        config_output_path = os.path.join(base_dir, f"crosswalk_config_{source_name}.json")

        if os.path.isfile(config_output_path):
            print(f"[generate_source_crosswalk_config] Reusando config existente: {config_output_path}")
            return {"source_crosswalk_config": config_output_path}
        return None

    def prepare_context(self, state: dict[str, Any]) -> dict[str, Any]:
        csv_path = state["source_csv_path"]
        source_name = state.get("source_name", "unknown")
        base_dir = os.path.dirname(csv_path) or "."
        config_output_path = os.path.join(base_dir, f"crosswalk_config_{source_name}.json")

        file_delimiter = _detect_file_delimiter(csv_path)
        head_rows = _read_csv_head(csv_path, n=5)
        columns = list(head_rows[0].keys()) if head_rows else []
        csv_head_text = _format_csv_head_for_prompt(head_rows)

        return {
            "state": state,
            "csv_path": csv_path,
            "source_name": source_name,
            "base_dir": base_dir,
            "output_config_path": config_output_path,
            "file_delimiter": file_delimiter,
            "head_rows": head_rows,
            "columns": columns,
            "csv_head_text": csv_head_text,
            "augmented_path": None,
        }

    def get_extra_tools(self, context: dict[str, Any]) -> list[BaseTool]:
        base_dir = context["base_dir"]
        source_name = context["source_name"]
        state = context["state"]

        @tool
        def enrich_source_columns_from_doi(
            doi_column: str,
            target_fields: list[str],
        ) -> str:
            """
            Consulta Crossref utilizando los DOIs de la columna especificada para obtener
            uno o varios metadatos genéricos faltantes (ej: 'type', 'date', 'author', 'citation', 'issn')
            y añade las columnas correspondientes ('inferred_<campo>') al CSV fuente.

            Args:
                doi_column: Nombre exacto de la columna en el CSV que contiene los DOIs (ej: 'DOI').
                target_fields: Lista de nombres de campos genéricos destino a inferir (ej: ['type'], ['type', 'date']).

            Returns:
                Confirmación detallando las nuevas columnas disponibles en el CSV para mapear.
            """
            try:
                out_path = os.path.join(base_dir, f"augmented_{source_name}.csv")
                fields_list = [target_fields] if isinstance(target_fields, str) else list(target_fields)
                added_cols, aug_path, samples_by_field = enrich_source_with_crossref_doi(
                    csv_path=context["csv_path"],
                    doi_column=doi_column,
                    target_fields=fields_list,
                    output_path=out_path,
                )
                context["csv_path"] = aug_path
                context["augmented_path"] = aug_path
                state["source_csv_path"] = aug_path
                context["head_rows"] = _read_csv_head(aug_path, n=5)
                context["columns"] = list(context["head_rows"][0].keys()) if context["head_rows"] else []
                context["csv_head_text"] = _format_csv_head_for_prompt(context["head_rows"])
                print(f"[Fase 1] Pre-enriquecimiento exitoso con Crossref. Columnas añadidas: {added_cols}")
                mapping_tips = [
                    f"left='{col}', replace='{f}'"
                    for col, f in zip(added_cols, fields_list)
                ]
                return (
                    f"Éxito: Se consultó Crossref vía '{doi_column}' y se generaron las columnas: {', '.join(added_cols)} "
                    f"en el CSV. Muestras obtenidas: {samples_by_field}. "
                    f"Ahora DEBES incluir en save_column_mappings los mapeos: {'; '.join(mapping_tips)}."
                )
            except Exception as exc:
                print(f"[Fase 1] Error en enrich_source_columns_from_doi: {exc}")
                return f"Error consultando Crossref: {exc}"

        return [enrich_source_columns_from_doi]

    def build_prompt(
        self,
        context: dict[str, Any],
        remaining_columns: list[str],
        feedback: Optional[str] = None,
    ) -> str:
        generic_desc = _build_generic_columns_description()
        system_prompt = load_agent_prompt(
            "crosswalk_agent",
            generic_desc=generic_desc,
            csv_head=context["csv_head_text"],
        )
        if feedback:
            system_prompt += (
                f"\n\n--- FEEDBACK DE VALIDACIÓN ANTERIOR ---\n{feedback}\n"
                f"Corregí los mapeos considerando este feedback."
            )
        return system_prompt

    def validate_mappings(
        self,
        raw_mappings: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        # Guardrail Nivel 3 (en memoria): Sintáctico base + restricción al esquema genérico
        base_validated = super().validate_mappings(raw_mappings, context)
        allowed_generic = set(GENERIC_COLUMNS.keys())
        validated = []
        for m in base_validated:
            rep = m.get("replace", "").strip()
            if rep in allowed_generic:
                validated.append(m)
            else:
                logger.warning(
                    "[SourceToGeneric Guardrail] Campo '%s' no pertenece al esquema genérico permitido. Mapeo descartado.",
                    rep,
                )

        if not validated:
            print("[Fase 1] Fallback: usando mapeo 1:1")
            return _build_fallback_mappings(context["head_rows"])
        return validated

    def resolve_separator_settings(
        self,
        mappings: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Fase 2: Detección Python-first, ReAct fallback y HITL interrupt."""
        head_rows = context["head_rows"]
        csv_path = context["csv_path"]
        file_delimiter = context["file_delimiter"]

        # Identificar columnas mapeadas a 'author' o 'subject'
        multi_value_cols: list[str] = []
        for mapping in mappings:
            if mapping.get("replace") in {"author", "subject"}:
                left = mapping.get("left", "")
                multi_value_cols.extend(c.strip() for c in left.split("+") if c.strip())

        separator_info: dict[str, str] = {"type": "unknown", "value": ""}
        print(f"[Fase 2] Columnas multivaluadas detectadas (author/subject): {multi_value_cols or '(ninguna)'}")

        from core.utils.config import config
        from core.utils.get_local_model import FallbackLLM
        llm = FallbackLLM(groq_model=config.CROSSWALK_MODEL, openrouter_model=config.CROSSWALK_MODEL).resolve()

        if multi_value_cols and head_rows:
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

        # Fase 2b: ReAct focalizado si no se clasificó
        if separator_info["type"] == "unknown" and multi_value_cols:
            print("[Fase 2b] Lanzando agente ReAct para inferencia de regex")
            detected_regex = _phase2b_react_regex(
                llm=llm,
                csv_path=csv_path,
                column=multi_value_cols[0],
                head_rows=head_rows,
            )

            if detected_regex:
                if not is_known_regex(detected_regex):
                    compiled = re.compile(detected_regex)
                    samples_report = []
                    for row in head_rows:
                        val = row.get(_find_column(multi_value_cols[0], row) or multi_value_cols[0], "").strip()
                        if val:
                            tokens = [t.strip() for t in compiled.split(val) if t and t.strip()]
                            samples_report.append({"original": val, "tokens": tokens})

                    print("[HITL] Regex desconocido detectado. Pausando para validación humana...")
                    human_response = interrupt({
                        "action_required": "validate_regex",
                        "proposed_regex": detected_regex,
                        "column": multi_value_cols[0],
                        "samples": samples_report,
                    })

                    if human_response.get("status") == "accepted":
                        final_regex = human_response.get("regex", detected_regex)
                        separator_info = {"type": "regex", "value": final_regex}
                        print(f"[Fase 2b] Regex final tras HITL: {final_regex!r}")
                        save_custom_regex(final_regex)
                    else:
                        separator_info = {"type": "literal", "value": "||"}
                        print("[Fase 2b] Humano rechazó el regex; usando fallback '||'")
                else:
                    separator_info = {"type": "regex", "value": detected_regex}
                    print(f"[Fase 2b] Regex aprobado (ya conocido): {detected_regex!r}")
            else:
                separator_info = {"type": "literal", "value": "||"}
                print("[Fase 2b] Sin resultado; usando fallback '||'")

        # Armar diccionario de configuración de separadores
        settings: dict[str, Any] = {
            "replace_separator": "|",
            "file_delimiter": file_delimiter,
        }
        if separator_info["type"] == "regex":
            settings["original_separator"] = "||"
            settings["separator_regex"] = separator_info["value"]
        else:
            settings["original_separator"] = separator_info.get("value") or "||"

        return settings

    def post_validate(self, config_path: str, context: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Fase 4: Validación funcional determinista (sin LLM) con CrosswalkClient."""
        csv_path = context["csv_path"]
        print("[generate_source_crosswalk_config] Fase 4: validación determinista")
        try:
            validation = _validate_config_deterministic(csv_path, config_path)
            print(f"[Fase 4] {validation['message']}")
        except Exception as e:
            print(f"[Fase 4] Error en validación determinista: {repr(e)}")
            validation = {"ok": False, "message": str(e), "missing": []}

        if not validation["ok"]:
            if validation.get("missing"):
                feedback = (
                    f"Validación fallida. Faltan las siguientes columnas críticas en el resultado: {', '.join(validation['missing'])}.\n"
                    f"Mensaje detallado del sistema:\n{validation['message']}\n\n"
                    f"⚠️ ANÁLISIS DE CAUSAS COMUNES ⚠️\n"
                    f"1. CONFUSIÓN VALOR-CABECERA: Usaste un valor de celda de las muestras en lugar del NOMBRE EXACTO de la cabecera en el campo 'left'.\n"
                    f"2. COLUMNA INEXISTENTE: Inventaste una columna o la escribiste mal. Verificá la lista de columnas proporcionada.\n"
                    f"3. FILTRADO AGRESIVO: Configuraste 'required': true en una columna que a veces está vacía, provocando que toda la fila se elimine en la validación.\n\n"
                    f"INSTRUCCIÓN CRÍTICA: Analiza el error y coloca tu reflexión EXCLUSIVAMENTE en el parámetro 'thought' de la herramienta. "
                    f"Luego, pasa la lista COMPLETA de TODOS los mapeos corregidos en el parámetro 'mappings'. "
                    f"NO devuelvas texto explicativo fuera de la llamada a la herramienta."
                )
                print("[Fase 4] Validación determinista solicitando reintento con feedback")
                return {"ok": False, "retry_feedback": feedback}
            else:
                print("[Fase 4] La validación falló por problemas de separación, pero los mapeos de columnas son correctos.")
                return {"ok": False}

        print("[generate_source_crosswalk_config] Fase 4: validación determinista completada exitosamente")
        return {"ok": True}

    def build_result(self, config_path: str, context: dict[str, Any]) -> dict[str, Any]:
        res = {"source_crosswalk_config": config_path}
        if context.get("augmented_path"):
            res["source_csv_path"] = context["augmented_path"]
        return res


# ---------------------------------------------------------------------------
# Definición del nodo
# ---------------------------------------------------------------------------

@traceable(name="generate_source_crosswalk_config", run_type="tool")
def generate_source_crosswalk_config(state: dict[str, Any]) -> dict[str, Any]:
    """
    Paso 2a (agente) — Genera el crosswalk config en cuatro fases:
    mapping LLM → detección Python → construcción config → validación determinista.

    Returns:
        dict con source_crosswalk_config actualizado (path al JSON generado).
    """
    generator = SourceToGenericCrosswalkGenerator()
    return generator.generate_config(state)
