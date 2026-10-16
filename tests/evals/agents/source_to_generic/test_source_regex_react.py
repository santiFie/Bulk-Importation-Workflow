"""
Evaluación del Subagente ReAct para Inferencia de Regex en Fase 2b.

Módulo: tests/evals/agents/source_to_generic/test_source_regex_react.py
Suite de LangSmith: Source_To_Generic_Regex_React_Agent

Evalúa con Pytest y LLMs reales (sin mocks):
  1. Descubrimiento de expresiones regulares para patrones complejos (autores concatenados CamelCase, iniciales con punto).
  2. Validez sintáctica del regex devuelto (`re.compile`).
  3. Eficiencia de división (> 50% de las filas de muestra divididas en más de 1 token).
  4. Abstención o no división destructiva ante valores que representan entidades individuales.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

import pytest
from langsmith import testing as t

from core.nodes.source_to_generic.node import _phase2b_react_regex
from core.utils.config import config
from core.utils.get_local_model import FallbackLLM
from tests.evals.metrics.source_to_generic_metrics import eval_regex_react_quality

CASES_FILE_PATH = (
    Path(__file__).parent.parent.parent
    / "datasets"
    / "source_to_generic"
    / "source_regex_react_cases.json"
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
@pytest.mark.langsmith(test_suite_name="Source_To_Generic_Regex_React_Agent")
@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["id_caso"])
def test_regex_react_agent(case: dict[str, Any], require_real_llm, tmp_path: Path):
    """
    Verifica que el agente ReAct infiera o descarte correctamente expresiones regulares.
    """
    inputs = case["inputs"]
    reference = case["reference_outputs"]
    column_name = inputs["column"]
    sample_values = inputs["sample_values"]

    # Crear CSV temporal con las muestras de la columna
    csv_file = tmp_path / f"{case['id_caso']}.csv"
    with open(csv_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[column_name])
        writer.writeheader()
        for val in sample_values:
            writer.writerow({column_name: val})

    head_rows = [{column_name: val} for val in sample_values[:5]]

    t.log_inputs({
        "id_caso": case["id_caso"],
        "column": column_name,
        "sample_values": sample_values,
        "tags": case.get("tags", []),
    })
    t.log_reference_outputs(reference)

    llm = FallbackLLM(
        groq_model=config.CROSSWALK_MODEL,
        openrouter_model=config.CROSSWALK_MODEL,
    ).resolve()

    detected_regex = _phase2b_react_regex(
        llm=llm,
        csv_path=str(csv_file),
        column=column_name,
        head_rows=head_rows,
    )

    output_payload = {
        "proposed_regex": detected_regex if detected_regex is not None else "SIN_PATRON"
    }
    t.log_outputs(output_payload)

    # 1. Evaluación semántica de calidad del regex
    eval_result = eval_regex_react_quality(
        MockRun(output_payload), MockExample(inputs, reference)
    )
    t.log_feedback(
        key=eval_result["key"],
        score=eval_result["score"],
        comment=eval_result["comment"],
    )

    # 2. Aserciones duras según el tipo de caso
    expected_action = reference.get("expected_action")

    if expected_action == "no_split_or_sin_patron":
        # Caso negativo: o devolvió None (SIN_PATRON) o un patrón que no parte los nombres únicos
        if detected_regex is not None:
            compiled = re.compile(detected_regex)
            spurious_splits = sum(len(compiled.split(s)) > 1 for s in sample_values)
            assert spurious_splits == 0, (
                f"Falso positivo: el regex propuesto '{detected_regex}' partió {spurious_splits} valores individuales."
            )
    else:
        # Caso positivo: debe devolver un regex válido y con split > 50%
        assert detected_regex is not None, (
            f"El subagente ReAct no logró inferir ningún regex para el caso positivo '{case['id_caso']}'."
        )

        try:
            compiled = re.compile(detected_regex)
        except re.error as exc:
            pytest.fail(f"El regex propuesto '{detected_regex}' tiene sintaxis inválida: {exc}")

        splits = [len(compiled.split(s)) for s in sample_values]
        multi_token_rows = sum(s > 1 for s in splits)
        efficiency = multi_token_rows / len(sample_values)
        min_eff = reference.get("min_split_efficiency", 0.5)

        assert efficiency >= min_eff, (
            f"Eficiencia insuficiente ({efficiency:.1%}) para el patrón '{detected_regex}'. "
            f"Mínimo requerido: {min_eff:.1%}"
        )
        assert eval_result["score"] is not None and eval_result["score"] >= min_eff, (
            f"Los tokens obtenidos con el patrón '{detected_regex}' no coinciden con las entidades esperadas: "
            f"{eval_result['comment']}"
        )

