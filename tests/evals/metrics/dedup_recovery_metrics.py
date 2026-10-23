"""
Evaluadores semánticos y cuantitativos para el Agente de Recuperación Reactiva (`DedupRecoveryAgent`).

Diseñados bajo la Arquitectura Dual de Evaluación (Issue #47):
  1. Evaluadores Homogéneos (`DEDUP_RECOVERY_EVALUATORS`): Aplican al 100% de las filas del
     dataset en `evaluate_dedup_recovery.py` (`aevaluate`), garantizando ausencia total de
     celdas "No Feedback" en LangSmith.
  2. Evaluadores Específicos de Caso: Invocados selectivamente en Pytest + LangSmith
     (`test_dedup_recovery_decisions.py`) únicamente sobre los escenarios donde aplican
     (validación de parámetros de estrategia, efecto de memoria episódica y ejecución sobre CSV).
"""

from __future__ import annotations

import re
from typing import Any
from langsmith.schemas import Example, Run


# ---------------------------------------------------------------------------
# 1. Evaluadores Homogéneos (100% de cobertura en todos los casos)
# ---------------------------------------------------------------------------

def eval_strategy_accuracy(run: Run, example: Example) -> dict[str, Any]:
    """
    Evalúa si la estrategia elegida (`decision.strategy`) pertenece al conjunto
    de estrategias válidas declaradas para el caso (`expected_strategies`).

    Tipo de score: Numérico (1.0 / 0.0) + value categórico con la estrategia elegida.
    """
    outputs = run.outputs or {}
    reference = example.outputs or {}

    decision = outputs.get("decision", {})
    strategy = decision.get("strategy", "")
    expected_strategies = reference.get("expected_strategies", [])

    is_valid = strategy in expected_strategies
    return {
        "key": "strategy_accuracy",
        "score": 1.0 if is_valid else 0.0,
        "value": str(strategy),
        "comment": f"Obtenida: '{strategy}' | Esperadas: {expected_strategies}",
    }


def eval_target_column_accuracy(run: Run, example: Example) -> dict[str, Any]:
    """
    Evalúa si el agente identificó con exactitud la columna afectada (`decision.target_column`).

    Tipo de score: Numérico (1.0 / 0.0) + value con la columna detectada.
    """
    outputs = run.outputs or {}
    reference = example.outputs or {}

    decision = outputs.get("decision", {})
    actual_col = str(decision.get("target_column", "")).strip()
    expected_col = str(reference.get("expected_target_column", "")).strip()

    is_match = actual_col.lower() == expected_col.lower() and bool(expected_col)
    return {
        "key": "target_column_accuracy",
        "score": 1.0 if is_match else 0.0,
        "value": actual_col,
        "comment": f"Obtenida: '{actual_col}' | Esperada: '{expected_col}'",
    }


def eval_ambiguity_detection(run: Run, example: Example) -> dict[str, Any]:
    """
    Evalúa la detección de ambigüedad (`decision.is_ambiguous == expected_ambiguous`).

    Tipo de score: Numérico (1.0 / 0.0).
    """
    outputs = run.outputs or {}
    reference = example.outputs or {}

    decision = outputs.get("decision", {})
    actual_amb = bool(decision.get("is_ambiguous", False))
    expected_amb = bool(reference.get("expected_ambiguous", False))

    is_match = actual_amb == expected_amb
    return {
        "key": "ambiguity_detection_accuracy",
        "score": 1.0 if is_match else 0.0,
        "value": str(actual_amb),
        "comment": f"Obtenida: {actual_amb} | Esperada: {expected_amb}",
    }


def eval_hitl_calibration(run: Run, example: Example) -> dict[str, Any]:
    """
    Evalúa la calibración entre resolución autónoma (`is_auto_repairable=True`) y
    escalamiento a supervisión humana (`HITL` / `is_auto_repairable=False`).

    Clasifica el comportamiento en 4 categorías excluyentes:
      - 'autonomous_resolution': Era autorreparable y el agente lo resolvió autónomamente.
      - 'correct_hitl_escalation': Requería HITL y el agente escaló correctamente.
      - 'unnecessary_hitl_escalation': Era autorreparable pero el agente escaló a HITL.
      - 'missed_hitl_escalation': Era ambiguo/peligroso pero el agente intentó autorreparar.
    """
    outputs = run.outputs or {}
    reference = example.outputs or {}

    decision = outputs.get("decision", {})
    actual_auto = bool(outputs.get("is_auto_repairable", False))
    if "is_auto_repairable" not in outputs:
        actual_auto = (
            decision.get("confidence") == "high"
            and not bool(decision.get("is_ambiguous", False))
            and decision.get("strategy") != "manual_review"
        )

    expected_auto = bool(
        reference.get("expected_auto_repairable", not reference.get("expected_hitl", False))
    )

    if expected_auto and actual_auto:
        label = "autonomous_resolution"
        score = 1.0
    elif not expected_auto and not actual_auto:
        label = "correct_hitl_escalation"
        score = 1.0
    elif expected_auto and not actual_auto:
        label = "unnecessary_hitl_escalation"
        score = 0.0
    else:
        label = "missed_hitl_escalation"
        score = 0.0

    return {
        "key": "hitl_calibration",
        "score": score,
        "value": label,
        "comment": f"Esperaba autorreparable={expected_auto}, Obtenido={actual_auto} ({label})",
    }


def eval_confidence_calibration(run: Run, example: Example) -> dict[str, Any]:
    """
    Evalúa si el nivel de certeza reportado (`decision.confidence`) se encuentra
    dentro de los niveles esperados para el caso (`expected_confidence`).
    """
    outputs = run.outputs or {}
    reference = example.outputs or {}

    decision = outputs.get("decision", {})
    actual_conf = str(decision.get("confidence", "")).strip()
    expected_conf = reference.get("expected_confidence", [])

    is_match = actual_conf in expected_conf
    return {
        "key": "confidence_accuracy",
        "score": 1.0 if is_match else 0.0,
        "value": actual_conf,
        "comment": f"Obtenida: '{actual_conf}' | Esperadas: {expected_conf}",
    }


# ---------------------------------------------------------------------------
# 2. Evaluadores Específicos / Heterogéneos (Exclusivos para Pytest + LangSmith)
# ---------------------------------------------------------------------------

def eval_strategy_params_validity(run: Run, example: Example) -> dict[str, Any]:
    """
    Evalúa la validez técnica y semántica de los argumentos específicos de la estrategia:
      - Para 'value_mapping': verifica que `mapping_rules` no esté vacío, contenga alguna
        de las claves esperadas (`expected_mapping_keys`) y apunte a los valores esperados.
      - Para 'regex_extraction': verifica que `regex_pattern` compile en Python y extraiga
        exactamente los valores esperados sobre `expected_regex_samples`.
      - Para 'date_normalization' o 'manual_review' sin requisitos de parámetros: retorna score=None.
    """
    outputs = run.outputs or {}
    reference = example.outputs or {}

    decision = outputs.get("decision", {})
    strategy = decision.get("strategy", "")

    expected_keys = reference.get("expected_mapping_keys")
    expected_regex_samples = reference.get("expected_regex_samples")

    if not expected_keys and not expected_regex_samples:
        if strategy == "value_mapping":
            rules = decision.get("mapping_rules") or {}
            has_rules = isinstance(rules, dict) and len(rules) > 0
            return {
                "key": "strategy_params_validity",
                "score": 1.0 if has_rules else 0.0,
                "comment": (
                    f"Reglas propuestas: {rules}"
                    if has_rules
                    else "Estrategia 'value_mapping' seleccionada con mapping_rules vacío."
                ),
            }
        return {
            "key": "strategy_params_validity",
            "score": None,
            "comment": "N/A: El caso no requiere parámetros específicos de mapping o regex.",
        }

    if expected_keys:
        rules = decision.get("mapping_rules") or {}
        if not isinstance(rules, dict) or not rules:
            return {
                "key": "strategy_params_validity",
                "score": 0.0,
                "comment": f"Se esperaban claves {expected_keys}, pero mapping_rules está vacío.",
            }
        rules_lower = {str(k).lower(): str(v) for k, v in rules.items()}
        matched_keys = [k for k in expected_keys if k.lower() in rules_lower]
        target_substrings = reference.get("expected_mapping_target_substrings", [])
        targets_ok = True
        if target_substrings:
            all_vals = " ".join(str(v) for v in rules.values())
            targets_ok = any(sub.lower() in all_vals.lower() for sub in target_substrings)

        score = 1.0 if (matched_keys and targets_ok) else 0.0
        return {
            "key": "strategy_params_validity",
            "score": score,
            "comment": (
                f"Claves encontradas: {matched_keys}, reglas={rules}"
                if score == 1.0
                else f"Reglas insuficientes o erróneas: {rules}. Esperadas claves en {expected_keys}."
            ),
        }

    if expected_regex_samples:
        pattern = decision.get("regex_pattern") or ""
        group = int(decision.get("regex_group", 1))
        if not pattern:
            return {
                "key": "strategy_params_validity",
                "score": 0.0,
                "comment": "Se esperaba un regex_pattern válido, pero se recibió vacío.",
            }
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            return {
                "key": "strategy_params_validity",
                "score": 0.0,
                "comment": f"Expresión regular inválida '{pattern}': {exc}",
            }

        matches_ok = 0
        mismatches: list[str] = []
        for sample_in, expected_out in expected_regex_samples.items():
            m = compiled.search(sample_in)
            if m and m.lastindex and group <= m.lastindex:
                extracted = m.group(group).strip()
            elif m:
                extracted = m.group(0).strip()
            else:
                extracted = ""

            if extracted == expected_out:
                matches_ok += 1
            else:
                mismatches.append(f"'{sample_in}' -> '{extracted}' (esperado '{expected_out}')")

        accuracy = matches_ok / len(expected_regex_samples)
        return {
            "key": "strategy_params_validity",
            "score": float(accuracy),
            "comment": (
                f"Regex '{pattern}' (grupo {group}) extrajo correctamente {matches_ok}/{len(expected_regex_samples)} muestras."
                if not mismatches
                else f"Fallos de extracción con regex '{pattern}': {'; '.join(mismatches)}"
            ),
        }

    return {"key": "strategy_params_validity", "score": None}


def eval_episodic_memory_effect(run: Run, example: Example) -> dict[str, Any]:
    """
    Evalúa el impacto de la Memoria Episódica (CBR) únicamente cuando el caso inyecta episodios:
      - 'relevant': Verifica transferencia positiva (adopción de la estrategia y confianza 'high').
      - 'distractor': Verifica inmunidad (ausencia de columnas y claves prohibidas del distractor).
      - 'mixed': Verifica discriminación simultánea (adopción del relevante e inmunidad al distractor).
      - 'none' (sin episodios): Retorna score=None (no aplica).
    """
    inputs = example.inputs or {}
    outputs = run.outputs or {}
    reference = example.outputs or {}

    memory_mode = inputs.get("memory_mode", "none")
    episodes = inputs.get("episodes", [])
    if memory_mode == "none" or not episodes:
        return {
            "key": "episodic_memory_effect",
            "score": None,
            "comment": "N/A: Caso Zero-Shot sin episodios en memoria.",
        }

    decision = outputs.get("decision", {})
    strategy = decision.get("strategy", "")
    target_col = str(decision.get("target_column", "")).lower()
    confidence = decision.get("confidence", "")
    rules = decision.get("mapping_rules") or {}

    forbidden_cols = [c.lower() for c in reference.get("forbidden_columns", [])]
    forbidden_keys = [k.lower() for k in reference.get("forbidden_mapping_keys", [])]
    rules_keys_lower = {str(k).lower() for k in rules.keys()}

    contaminated = (
        target_col in forbidden_cols
        or any(fc in rules_keys_lower for fc in forbidden_cols)
        or any(fk in rules_keys_lower for fk in forbidden_keys)
    )

    strategy_ok = strategy in reference.get("expected_strategies", [])
    conf_ok = confidence == "high"

    if memory_mode == "relevant":
        passed = strategy_ok and conf_ok and not contaminated
        label = "positive_transfer_applied" if passed else "positive_transfer_failed"
    elif memory_mode == "distractor":
        passed = not contaminated and strategy_ok
        label = "distractor_ignored" if passed else "distractor_contamination"
    else:  # mixed
        passed = not contaminated and strategy_ok and conf_ok
        label = "mixed_memory_discriminated" if passed else "mixed_memory_failed"

    return {
        "key": "episodic_memory_effect",
        "score": 1.0 if passed else 0.0,
        "value": label,
        "comment": (
            f"Modo memoria='{memory_mode}' -> {label} "
            f"(strategy={strategy}, col={target_col}, conf={confidence}, contaminated={contaminated})"
        ),
    }


def eval_csv_repair_execution(run: Run, example: Example) -> dict[str, Any]:
    """
    Evalúa el resultado real de aplicar `decision.execute(csv_path)` y `validate_csv_structure`
    sobre el archivo CSV temporal de prueba.
    """
    outputs = run.outputs or {}
    reference = example.outputs or {}

    expected_auto = bool(
        reference.get("expected_auto_repairable", not reference.get("expected_hitl", False))
    )
    if not expected_auto:
        return {
            "key": "csv_repair_execution",
            "score": None,
            "comment": "N/A: El caso requiere escalamiento HITL y no debe modificar el CSV automáticamente.",
        }

    repair_result = outputs.get("repair_execution") or {}
    status = repair_result.get("status", "skipped")
    modified_count = int(repair_result.get("modified_count", 0))
    structure_valid = bool(repair_result.get("structure_valid", False))
    cleaned_by_id = repair_result.get("cleaned_by_id") or {}

    expected_cleaned = reference.get("expected_cleaned_values") or {}
    values_match = True
    mismatch_details: list[str] = []

    for row_id, allowed_vals in expected_cleaned.items():
        actual_val = cleaned_by_id.get(str(row_id))
        allowed_list = allowed_vals if isinstance(allowed_vals, list) else [allowed_vals]
        if actual_val not in allowed_list:
            values_match = False
            mismatch_details.append(
                f"fila id='{row_id}': obtenido='{actual_val}', esperado en {allowed_list}"
            )

    passed = (
        status == "ok"
        and modified_count > 0
        and structure_valid
        and values_match
    )

    return {
        "key": "csv_repair_execution",
        "score": 1.0 if passed else 0.0,
        "comment": (
            f"CSV reparado con éxito (modified_count={modified_count}, structure_valid={structure_valid})."
            if passed
            else f"Fallo en reparación física del CSV: status={status}, modified={modified_count}, "
                 f"structure_valid={structure_valid}, discrepancias={mismatch_details}"
        ),
    }


# Lista canónica de evaluadores 100% homogéneos para LangSmith Nativo (aevaluate)
DEDUP_RECOVERY_EVALUATORS = [
    eval_strategy_accuracy,
    eval_target_column_accuracy,
    eval_ambiguity_detection,
    eval_hitl_calibration,
    eval_confidence_calibration,
]
