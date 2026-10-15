"""
Evaluadores homogéneos para la toma de decisiones y trayectorias del MetadataCuratorAgent.

Diseñados tanto para LangSmith Nativo (`langsmith.evaluate`) como para testing local.
Cada función sigue la firma estándar de evaluador de LangSmith: `(run: Run, example: Example) -> dict`.
"""

from __future__ import annotations

from typing import Any
from langsmith.schemas import Example, Run


def eval_tool_selection(run: Run, example: Example) -> dict[str, Any]:
    """
    Evalúa la precisión en la selección de herramientas (*Tool Selection Accuracy*).

    Valida que:
      1. Se invoquen todas las herramientas requeridas (`expected_tools`).
      2. No se ejecute ninguna herramienta prohibida (`forbidden_tools`).
      3. Si no se esperan herramientas, el conteo sea exactamente 0.
    """
    outputs = run.outputs or {}
    reference = example.outputs or {}

    expected_tools = reference.get("expected_tools", [])
    forbidden_tools = reference.get("forbidden_tools", [])
    tools_executed = outputs.get("tools_executed", [])

    if not expected_tools:
        score = 1.0 if len(tools_executed) == 0 else 0.0
    else:
        has_expected = all(t in tools_executed for t in expected_tools)
        has_no_forbidden = all(f not in tools_executed for f in forbidden_tools)
        score = 1.0 if (has_expected and has_no_forbidden) else 0.0

    return {
        "key": "tool_selection_accuracy",
        "score": score,
        "comment": f"Esperadas: {expected_tools}, Prohibidas: {forbidden_tools}, Ejecutadas: {tools_executed}",
    }


def eval_field_preservation(run: Run, example: Example) -> dict[str, Any]:
    """
    Evalúa que el agente preserve intactos los campos válidos del registro (*Field Preservation*).
    """
    outputs = run.outputs or {}
    reference = example.outputs or {}

    campo_intacto = reference.get("campo_intacto", "")
    valor_esperado = reference.get("valor_intacto_esperado", "")
    decision = outputs.get("decision", {})

    if not campo_intacto:
        return {"key": "preserves_intact_fields", "score": 1.0}

    valor_obtenido = decision.get(campo_intacto, valor_esperado)
    preservado = valor_obtenido == valor_esperado

    return {
        "key": "preserves_intact_fields",
        "score": 1.0 if preservado else 0.0,
        "comment": f"Campo '{campo_intacto}' esperado: '{valor_esperado}', obtenido: '{valor_obtenido}'",
    }


def eval_action_accuracy(run: Run, example: Example) -> dict[str, Any]:
    """
    Evalúa si la decisión tomada cumple estrictamente el contrato del caso:
      - Si espera reparación: debe reparar el valor y no enviar a cuarentena.
      - Si espera abstención: debe enviar a cuarentena y no reparar con datos inventados.
    """
    outputs = run.outputs or {}
    reference = example.outputs or {}

    espera_reparacion = reference.get("espera_reparacion", False)
    espera_curation_needed = reference.get("espera_curation_needed", False)

    reparado = bool(outputs.get("repaired", False))
    marcado_revision = bool(outputs.get("marked_quarantine", False))

    if espera_reparacion:
        score = 1.0 if (reparado and not marcado_revision) else 0.0
        comentario = "Reparación exitosa" if score == 1.0 else "Fallo en reparación o enviado a cuarentena indebida"
    elif espera_curation_needed:
        score = 1.0 if (marcado_revision and not reparado) else 0.0
        comentario = "Abstención conservadora correcta" if score == 1.0 else "Alucinación de datos o falta de cuarentena"
    else:
        score = 1.0 if (reparado or marcado_revision) else 0.0
        comentario = "Acción estándar"

    return {
        "key": "action_taken_accuracy",
        "score": score,
        "comment": comentario,
    }


def eval_has_notes(run: Run, example: Example) -> dict[str, Any]:
    """
    Evalúa que el agente proporcione una justificación explícita en `correction_notes`.
    """
    outputs = run.outputs or {}
    notes = outputs.get("correction_notes", "")
    has_notes = bool(isinstance(notes, str) and notes.strip())

    return {
        "key": "has_correction_notes",
        "score": 1.0 if has_notes else 0.0,
        "comment": "Notas presentes" if has_notes else "Falta justificación en correction_notes",
    }


# Lista canónica de evaluadores homogéneos para LangSmith Nativo
CURATOR_EVALUATORS = [
    eval_tool_selection,
    eval_field_preservation,
    eval_action_accuracy,
    eval_has_notes,
]
