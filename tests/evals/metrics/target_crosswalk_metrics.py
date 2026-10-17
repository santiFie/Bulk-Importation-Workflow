"""
Evaluadores y métricas semánticas para TargetSediciCrosswalkGenerator.

Módulo: tests/evals/metrics/target_crosswalk_metrics.py
Diseñados para LangSmith Nativo (aevaluate) y Pytest + LangSmith.
Cumple la firma estándar: (run: Run, example: Example) -> dict[str, Any].
"""

from __future__ import annotations

import re
from typing import Any

from core.nodes.target_crosswalk_agent.helpers import (
    get_valid_sedici_fields,
    load_sedici_catalog,
)

# Catálogo oficial cargado una sola vez para validaciones
_CATALOG = load_sedici_catalog()
_SCHEMA_BY_FIELD, _VALID_BASE_FIELDS, _ALLOW_LANG_FIELDS = get_valid_sedici_fields(_CATALOG)


def _outputs(run: Any) -> dict[str, Any]:
    """Extrae el dict de salidas tanto de RunTree/Run de LangSmith como de dicts de prueba."""
    if hasattr(run, "outputs") and run.outputs is not None:
        return run.outputs
    if isinstance(run, dict):
        return run.get("outputs", run)
    return {}


def _inputs(example: Any) -> dict[str, Any]:
    """Extrae el dict de entradas tanto de Example de LangSmith como de dicts de prueba."""
    if hasattr(example, "inputs") and example.inputs is not None:
        return example.inputs
    if isinstance(example, dict):
        return example.get("inputs", {})
    return {}


def _reference(example: Any) -> dict[str, Any]:
    """Extrae las salidas de referencia esperadas."""
    if hasattr(example, "outputs") and example.outputs is not None:
        return example.outputs
    if isinstance(example, dict):
        return example.get("outputs", example.get("reference_outputs", {}))
    return {}


def _extract_mappings(outputs: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrae la lista de mapeos de la estructura de salida."""
    if "mappings" in outputs and isinstance(outputs["mappings"], list):
        return outputs["mappings"]
    if "validated_mappings" in outputs and isinstance(outputs["validated_mappings"], list):
        return outputs["validated_mappings"]
    # Si viene como formato config [mappings, options]
    if "config" in outputs and isinstance(outputs["config"], list) and len(outputs["config"]) > 0:
        return outputs["config"][0]
    return []


# ---------------------------------------------------------------------------
# Evaluadores de Invariantes y Conformidad Estructural
# ---------------------------------------------------------------------------

def eval_header_fidelity(run: Any, example: Any) -> dict[str, Any]:
    """
    Invariante de Fidelidad de Cabeceras:
    Verifica que cada columna 'left' en los mapeos exista estrictamente en las
    cabeceras del CSV de entrada (o sea una combinación válida de partes con '+').
    """
    outputs = _outputs(run)
    inputs = _inputs(example)
    mappings = _extract_mappings(outputs)
    available_cols = set(inputs.get("columns", []))

    if not mappings:
        return {
            "key": "header_fidelity",
            "score": 0.0,
            "comment": "No se encontraron mapeos en la salida.",
        }

    invalid_lefts: list[str] = []
    for m in mappings:
        left = str(m.get("left", "")).strip()
        parts = [p.strip() for p in left.split("+") if p.strip()]
        for part in parts:
            if part not in available_cols:
                invalid_lefts.append(part)

    score = 1.0 if not invalid_lefts else 0.0
    comment = (
        "Todas las columnas 'left' existen en las cabeceras del CSV."
        if score == 1.0
        else f"Columnas inexistentes o valores de celda detectados como 'left': {invalid_lefts}"
    )

    return {
        "key": "header_fidelity",
        "score": score,
        "comment": comment,
    }


def eval_target_schema_conformance(run: Any, example: Any) -> dict[str, Any]:
    """
    Invariante de Conformidad con el Esquema Oficial de SEDICI:
    Verifica que cada valor 'replace' sea un metadato formalmente válido en el
    catálogo de SEDICI y que respete las restricciones de calificadores de idioma.
    """
    outputs = _outputs(run)
    mappings = _extract_mappings(outputs)

    if not mappings:
        return {
            "key": "target_schema_conformance",
            "score": 0.0,
            "comment": "Sin mapeos generados.",
        }

    invalid_replaces: list[str] = []
    for m in mappings:
        replace = str(m.get("replace", "")).strip()
        match = re.match(r"^([a-zA-Z0-9\._\-]+)(?:\[([a-zA-Z]{2})\])?$", replace)
        if not match:
            invalid_replaces.append(f"{replace} (sintaxis inválida)")
            continue

        base_field = match.group(1)
        lang_qual = match.group(2)

        if base_field not in _VALID_BASE_FIELDS:
            invalid_replaces.append(f"{replace} (no existe en catálogo SEDICI)")
        elif lang_qual and base_field not in _ALLOW_LANG_FIELDS:
            invalid_replaces.append(f"{replace} (calificador [{lang_qual}] no admitido en este campo)")

    score = 1.0 if not invalid_replaces else 0.0
    comment = (
        "Todos los metadatos 'replace' cumplen el esquema oficial de SEDICI."
        if score == 1.0
        else f"Metadatos no conformes detectados: {invalid_replaces}"
    )

    return {
        "key": "target_schema_conformance",
        "score": score,
        "comment": comment,
    }


def eval_deterministic_core_preservation(run: Any, example: Any) -> dict[str, Any]:
    """
    Invariante de Preservación de Campos Troncales:
    Calcula la proporción [0.0, 1.0] de campos críticos de SEDICI (title, author, date, etc.)
    que quedaron asignados en la configuración final.
    """
    outputs = _outputs(run)
    ref = _reference(example)
    critical_fields = ref.get("critical_fields", [])

    if not critical_fields:
        return {
            "key": "deterministic_core_preservation",
            "score": None,
            "comment": "N/A: Sin campos críticos declarados en el caso.",
        }

    mappings = _extract_mappings(outputs)
    active_replaces = {m.get("replace", "").strip() for m in mappings}

    covered = [cf for cf in critical_fields if cf in active_replaces]
    ratio = len(covered) / len(critical_fields)
    missing = [cf for cf in critical_fields if cf not in active_replaces]

    return {
        "key": "deterministic_core_preservation",
        "score": ratio,
        "comment": (
            f"Preservados {len(covered)}/{len(critical_fields)} campos críticos."
            if not missing
            else f"Campos críticos faltantes: {missing}"
        ),
    }


def eval_expected_mappings_accuracy(run: Any, example: Any) -> dict[str, Any]:
    """
    Precisión frente al Oráculo (Corrección de Mapeos):
    Compara las asignaciones generadas contra las asignaciones declaradas en expected_mappings.
    """
    outputs = _outputs(run)
    ref = _reference(example)
    expected_mappings = ref.get("expected_mappings", {})

    if not expected_mappings:
        return {
            "key": "expected_mappings_accuracy",
            "score": None,
            "comment": "N/A: Sin mapeos esperados específicos.",
        }

    mappings = _extract_mappings(outputs)
    actual_map = {m.get("left", "").strip(): m.get("replace", "").strip() for m in mappings}

    matched = 0
    mismatches: list[str] = []
    for left_col, exp_replace in expected_mappings.items():
        got_replace = actual_map.get(left_col)
        norm_got = re.sub(r"\[[a-zA-Z]{2}\]$", "", got_replace).strip() if got_replace else ""
        norm_exp = re.sub(r"\[[a-zA-Z]{2}\]$", "", exp_replace).strip() if exp_replace else ""
        if got_replace == exp_replace or (norm_got and norm_got == norm_exp):
            matched += 1
        else:
            mismatches.append(f"Col '{left_col}': esperado '{exp_replace}', obtenido '{got_replace}'")

    accuracy = matched / len(expected_mappings)
    return {
        "key": "expected_mappings_accuracy",
        "score": accuracy,
        "comment": (
            f"Exactitud 100% ({matched}/{len(expected_mappings)} mapeos coincidentes)."
            if accuracy == 1.0
            else f"Exactitud {accuracy:.1%}. Diferencias: {'; '.join(mismatches[:3])}"
        ),
    }


def eval_forbidden_mappings_absence(run: Any, example: Any) -> dict[str, Any]:
    """
    Abstención y No-Alucinación (Ausencia de Mapeos Prohibidos):
    Verifica que no se hayan mapeado columnas redundantes (ej. First Author cuando ya
    existe Authors) ni columnas prohibidas/ruidosas (métricas propietarias, flags internas).
    """
    outputs = _outputs(run)
    ref = _reference(example)
    forbidden_mappings = ref.get("forbidden_mappings", {})
    forbidden_columns = set(ref.get("forbidden_columns_mapped", []))

    mappings = _extract_mappings(outputs)
    actual_map = {m.get("left", "").strip(): m.get("replace", "").strip() for m in mappings}

    violations: list[str] = []

    # Chequeo de pares (columna, metadato) prohibidos
    for left_col, f_replace in forbidden_mappings.items():
        if actual_map.get(left_col) == f_replace:
            violations.append(f"Par prohibido mapeado: '{left_col}' -> '{f_replace}'")

    # Chequeo de columnas que no debían mapearse a ningún metadato
    for col in forbidden_columns:
        if col in actual_map:
            violations.append(f"Columna prohibida/ruidosa fue mapeada: '{col}' -> '{actual_map[col]}'")

    score = 1.0 if not violations else 0.0
    return {
        "key": "forbidden_mappings_absence",
        "score": score,
        "comment": (
            "No se detectaron mapeos prohibidos o redundantes."
            if score == 1.0
            else f"Violaciones de abstención: {violations}"
        ),
    }


# ---------------------------------------------------------------------------
# Evaluadores Específicos para Autorreparación de Configuración del Usuario
# ---------------------------------------------------------------------------

def eval_user_config_repair_accuracy(run: Any, example: Any) -> dict[str, Any]:
    """
    Evalúa la capacidad de autorreparación del agente ante una configuración incorrecta
    provista por el humano.
    Verifica:
      1. Que las columnas reparadas declaradas en 'repaired_columns' estén mapeadas a su metadato correcto.
      2. Que las columnas a descartar/omitir declaradas en 'omitted_columns' hayan sido descartadas
         y NO figuren en los mappings resultantes.
      3. Que los mapeos prohibidos originales no persistan.
    """
    ref = _reference(example)
    repaired_cols = ref.get("repaired_columns", [])
    omitted_cols = ref.get("omitted_columns", [])
    expected_mappings = ref.get("expected_mappings", {})

    if not repaired_cols and not omitted_cols:
        return {
            "key": "user_config_repair_accuracy",
            "score": None,
            "comment": "N/A: Caso sin reparaciones u omisiones de configuración de usuario.",
        }

    outputs = _outputs(run)
    mappings = _extract_mappings(outputs)
    actual_map = {m.get("left", "").strip(): m.get("replace", "").strip() for m in mappings}

    repaired_correctly = 0
    failures: list[str] = []

    for col in repaired_cols:
        exp_target = expected_mappings.get(col)
        got_target = actual_map.get(col)
        norm_got = re.sub(r"\[[a-zA-Z]{2}\]$", "", got_target).strip() if got_target else ""
        norm_exp = re.sub(r"\[[a-zA-Z]{2}\]$", "", exp_target).strip() if exp_target else ""
        if got_target == exp_target or (norm_got and norm_got == norm_exp):
            repaired_correctly += 1
        else:
            failures.append(f"Columna reparada '{col}': esperado '{exp_target}', obtenido '{got_target}'")

    # Verificar que las columnas omitidas no persistan en los mappings
    omitted_correctly = 0
    for om in omitted_cols:
        if om in actual_map:
            failures.append(f"Columna superflua '{om}' no fue omitida y sigue mapeada a '{actual_map[om]}'")
        else:
            omitted_correctly += 1

    forbidden = ref.get("forbidden_mappings", {})
    for f_left, f_rep in forbidden.items():
        if actual_map.get(f_left) == f_rep:
            failures.append(f"Mapeo erróneo original '{f_left}' -> '{f_rep}' persiste en el resultado")

    total_expected = len(repaired_cols) + len(omitted_cols)
    score = 1.0 if not failures and (repaired_correctly + omitted_correctly) == total_expected else 0.0

    return {
        "key": "user_config_repair_accuracy",
        "score": score,
        "comment": (
            f"Autorreparación exitosa para {repaired_correctly + omitted_correctly}/{total_expected} columnas "
            f"({repaired_correctly} reparadas, {omitted_correctly} omitidas)."
            if score == 1.0
            else f"Fallas en autorreparación: {failures}"
        ),
    }


def eval_valid_mappings_preservation(run: Any, example: Any) -> dict[str, Any]:
    """
    Evalúa que los mapeos válidos provistos en la configuración del usuario
    hayan sido preservados intactos durante la autorreparación.
    """
    ref = _reference(example)
    preserved_cols = ref.get("preserved_columns", [])
    expected_mappings = ref.get("expected_mappings", {})

    if not preserved_cols:
        return {
            "key": "valid_mappings_preservation",
            "score": None,
            "comment": "N/A: Sin columnas preservadas declaradas.",
        }

    outputs = _outputs(run)
    mappings = _extract_mappings(outputs)
    actual_map = {m.get("left", "").strip(): m.get("replace", "").strip() for m in mappings}

    preserved_count = 0
    divergences: list[str] = []

    for col in preserved_cols:
        exp_target = expected_mappings.get(col)
        got_target = actual_map.get(col)
        norm_got = re.sub(r"\[[a-zA-Z]{2}\]$", "", got_target).strip() if got_target else ""
        norm_exp = re.sub(r"\[[a-zA-Z]{2}\]$", "", exp_target).strip() if exp_target else ""
        if got_target == exp_target or (norm_got and norm_got == norm_exp):
            preserved_count += 1
        else:
            divergences.append(f"Columna '{col}': esperado intacto '{exp_target}', obtenido '{got_target}'")

    ratio = preserved_count / len(preserved_cols)
    return {
        "key": "valid_mappings_preservation",
        "score": ratio,
        "comment": (
            f"Preservados {preserved_count}/{len(preserved_cols)} mapeos válidos preexistentes."
            if not divergences
            else f"Mapeos válidos alterados o perdidos: {divergences}"
        ),
    }


# ---------------------------------------------------------------------------
# Evaluador de Trayectoria de Herramientas
# ---------------------------------------------------------------------------

def eval_tool_trajectory(run: Any, example: Any) -> dict[str, Any]:
    """
    Evalúa la trayectoria de llamadas a herramientas (Tool Calls):
    - Verifica que se invoque 'save_column_mappings' cuando existen remanentes o reparaciones.
    - Verifica que no se invoquen herramientas espurias o no autorizadas.
    """
    ref = _reference(example)
    expected_tools = ref.get("expected_tools")
    forbidden_tools = ref.get("forbidden_tools", [])

    if expected_tools is None:
        return {
            "key": "tool_trajectory",
            "score": None,
            "comment": "N/A: Sin herramientas esperadas declaradas.",
        }

    outputs = _outputs(run)
    tools_executed = outputs.get("tools_executed", [])

    if not expected_tools:
        # No se esperaba ninguna invocación a herramientas (ej. resuelto 100% en Nivel 1)
        score = 1.0 if len(tools_executed) == 0 else 0.0
        comment = (
            "Sin herramientas ejecutadas (conforme con resolución determinista)."
            if score == 1.0
            else f"Se ejecutaron herramientas inesperadas: {tools_executed}"
        )
    else:
        has_expected = all(t in tools_executed for t in expected_tools)
        has_no_forbidden = all(t not in tools_executed for t in forbidden_tools)
        score = 1.0 if (has_expected and has_no_forbidden) else 0.0
        comment = (
            f"Herramientas ejecutadas conforme a lo esperado: {tools_executed}"
            if score == 1.0
            else f"Desvío de trayectoria: esperadas={expected_tools}, ejecutadas={tools_executed}"
        )

    return {
        "key": "tool_trajectory",
        "score": score,
        "comment": comment,
    }


# Lista de evaluadores homogéneos estándar para benchmarking en LangSmith
TARGET_CROSSWALK_EVALUATORS = [
    eval_header_fidelity,
    eval_target_schema_conformance,
    eval_deterministic_core_preservation,
    eval_expected_mappings_accuracy,
    eval_forbidden_mappings_absence,
    eval_user_config_repair_accuracy,
    eval_valid_mappings_preservation,
    eval_tool_trajectory,
]
