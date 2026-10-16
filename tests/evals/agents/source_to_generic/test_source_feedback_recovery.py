"""
Evaluación de Resiliencia y Corrección con Feedback de Validación Determinista (Fase 4 -> Fase 1).

Módulo: tests/evals/agents/source_to_generic/test_source_feedback_recovery.py
Suite de LangSmith: Source_To_Generic_Feedback_Recovery

Evalúa con Pytest y LLMs reales (sin mocks):
  1. Incorporación efectiva del mensaje de error de validación determinista en la re-ejecución.
  2. Corrección de campos críticos omitidos (ej. title, id, date).
  3. Corrección de confusión valor-cabecera (reemplazar valores de celdas por nombres reales de columnas).
  4. Preservación de mapeos ya válidos sin regresiones espurias ni alucinación de cabeceras.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest
from langsmith import testing as t

from core.nodes.source_to_generic.node import SourceToGenericCrosswalkGenerator
from core.utils.config import config
from tests.evals.metrics.source_to_generic_metrics import (
    eval_feedback_recovery,
    eval_header_fidelity,
    eval_target_schema_adherence,
)

CASES_FILE_PATH = (
    Path(__file__).parent.parent.parent
    / "datasets"
    / "source_to_generic"
    / "source_feedback_recovery_cases.json"
)


def _load_cases() -> list[dict[str, Any]]:
    if not CASES_FILE_PATH.exists():
        raise FileNotFoundError(f"No se encontró el dataset en: {CASES_FILE_PATH}")
    with open(CASES_FILE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def require_real_llm():
    """Exige un LLM real configurado; sin credenciales se omite el test (sin mocks)."""
    if not (config.GROQ_API_KEY or config.NVIDIA_API_KEY or config.OPEN_ROUTER_API_KEY):
        pytest.skip("No hay API key de LLM configurada.")


class MockExample:
    def __init__(self, inputs, outputs):
        self.inputs = inputs
        self.outputs = outputs


class MockRun:
    def __init__(self, outputs):
        self.outputs = outputs


@pytest.mark.eval
@pytest.mark.langsmith(test_suite_name="Source_To_Generic_Feedback_Recovery")
@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["id_caso"])
def test_feedback_recovery(case: dict[str, Any], require_real_llm, tmp_path: Path):
    """
    Verifica que el LLM reciba el feedback de validación anterior y corrija sus mapeos.
    """
    inputs = case["inputs"]
    reference = case["reference_outputs"]
    columns = inputs["columns"]
    sample_rows = inputs.get("sample_rows", [])
    prior_mappings = inputs.get("prior_mappings", [])
    feedback_text = inputs["feedback"]
    if prior_mappings:
        feedback_text = (
            f"Mapeos propuestos en el intento anterior (fallido):\n"
            f"{json.dumps(prior_mappings, ensure_ascii=False)}\n\n"
            f"{feedback_text}"
        )

    # Crear CSV de prueba temporal con las filas de muestra reales del caso
    csv_file = tmp_path / f"{case['id_caso']}.csv"
    with open(csv_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        if sample_rows:
            writer.writerows(sample_rows)
        else:
            writer.writerow({c: f"sample_{c}" for c in columns})

    t.log_inputs({
        "id_caso": case["id_caso"],
        "columns": columns,
        "feedback_received": feedback_text,
        "prior_mappings": prior_mappings,
        "tags": case.get("tags", []),
    })
    t.log_reference_outputs(reference)

    generator = SourceToGenericCrosswalkGenerator()
    state = {
        "source_csv_path": str(csv_file),
        "source_name": case["id_caso"],
    }
    context = generator.prepare_context(state)

    # Invocación pasando el feedback de reintento
    retry_mappings = generator.execute_llm_mapping(
        context, context["columns"], feedback=feedback_text
    )

    output_payload = {"mappings": retry_mappings}
    t.log_outputs(output_payload)

    # 1. Evaluación de recuperación
    rec_eval = eval_feedback_recovery(
        MockRun(output_payload), MockExample(inputs, reference)
    )
    t.log_feedback(key=rec_eval["key"], score=rec_eval["score"], comment=rec_eval["comment"])

    # 2. Evaluación de fidelidad de cabeceras (no inventar columnas al corregir)
    fidelity_eval = eval_header_fidelity(
        MockRun(output_payload), MockExample(inputs, reference)
    )
    t.log_feedback(key=fidelity_eval["key"], score=fidelity_eval["score"], comment=fidelity_eval["comment"])

    # 3. Evaluación de adherencia al esquema destino
    schema_eval = eval_target_schema_adherence(
        MockRun(output_payload), MockExample(inputs, reference)
    )
    t.log_feedback(key=schema_eval["key"], score=schema_eval["score"], comment=schema_eval["comment"])

    assert fidelity_eval["score"] == 1.0, fidelity_eval["comment"]
    assert schema_eval["score"] == 1.0, schema_eval["comment"]

    # 4. Aserciones duras específicas del caso
    mapping_dict = {m.get("replace"): m.get("left") for m in retry_mappings}
    required_by_replace = {
        m.get("replace"): bool(m.get("required", False)) for m in retry_mappings
    }

    # Comprobar que los campos corregidos estén presentes
    fixed_fields = list(reference.get("fixed_fields", []))
    if reference.get("fixed_field"):
        fixed_fields.append(reference["fixed_field"])

    for field in fixed_fields:
        assert field in mapping_dict, (
            f"El campo corregido esperado '{field}' no se encuentra en el nuevo mapeo. "
            f"Mapeos devueltos: {mapping_dict}"
        )

    # Comprobar expected_left_mappings ({replace: expected_left})
    expected_left_mappings = dict(reference.get("expected_left_mappings", {}))
    if reference.get("expected_left_for_fixed_field") and reference.get("fixed_field"):
        expected_left_mappings[reference["fixed_field"]] = reference["expected_left_for_fixed_field"]

    for target_field, exp_left in expected_left_mappings.items():
        actual_left = str(mapping_dict.get(target_field, ""))
        assert actual_left.lower() == exp_left.lower(), (
            f"Cabecera 'left' esperada para '{target_field}': '{exp_left}', "
            f"obtenida: '{actual_left}'"
        )

    # Comprobar prohibición de usar valores de celda en 'left'
    forbidden_lefts = reference.get("forbidden_lefts", [])
    for forb_l in forbidden_lefts:
        for m in retry_mappings:
            assert forb_l.lower() != str(m.get("left", "")).lower(), (
                f"Valor de celda prohibido '{forb_l}' fue utilizado en el campo 'left'."
            )

    # Comprobar prohibición de mapear campos destino inexistentes
    forbidden_replaces = reference.get("forbidden_replaces", [])
    for forb_r in forbidden_replaces:
        assert forb_r not in mapping_dict, (
            f"Campo destino prohibido '{forb_r}' fue mapeado indebidamente desde '{mapping_dict.get(forb_r)}'."
        )

    # Comprobar que campos opcionales vacíos no tengan required=True
    for non_req_field in reference.get("expected_non_required_fields", []):
        assert not required_by_replace.get(non_req_field, False), (
            f"El campo opcional '{non_req_field}' mantuvo required=True tras el feedback de filtrado agresivo."
        )

    # Comprobar preservación de campos críticos
    must_contain = reference.get("must_contain_fields", [])
    for mc in must_contain:
        assert mc in mapping_dict, (
            f"Regresión: El campo obligatorio '{mc}' se perdió durante la corrección con feedback."
        )

