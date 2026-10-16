"""
Evaluación de Trayectoria de Herramientas y Pre-enriquecimiento con Crossref.

Módulo: tests/evals/agents/source_to_generic/test_source_doi_enrichment_trajectory.py
Suite de LangSmith: Source_To_Generic_DOI_Enrichment_Trajectory

Evalúa con Pytest y LLMs reales (sin mocks):
  1. Activación obligatoria de `enrich_source_columns_from_doi` cuando faltan metadatos y hay DOI.
  2. Prohibición de la herramienta cuando los metadatos ya están completos o no hay DOI.
  3. Identificación correcta del nombre de la cabecera del DOI (incluso con nombres no estándar).
  4. Mapeo final en `save_column_mappings` de las columnas inferidas (`inferred_*`).
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any

import pytest
from langsmith import testing as t

from core.nodes.source_to_generic.node import SourceToGenericCrosswalkGenerator
from core.utils.config import config
from tests.evals.metrics.source_to_generic_metrics import eval_tool_selection

CASES_FILE_PATH = (
    Path(__file__).parent.parent.parent
    / "datasets"
    / "source_to_generic"
    / "source_doi_enrichment_cases.json"
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


class TrajectoryTrackingGenerator(SourceToGenericCrosswalkGenerator):
    """
    Subclase instrumentada para capturar las llamadas exactas a herramientas
    y sus argumentos sin alterar la lógica de negocio.
    """

    def __init__(self):
        super().__init__()
        self.tool_calls_log: list[dict[str, Any]] = []

    def get_extra_tools(self, context: dict[str, Any]) -> list[Any]:
        tools = super().get_extra_tools(context)
        instrumented_tools = []
        for t_tool in tools:
            name = t_tool.name
            orig_func = t_tool.func

            def make_wrapper(orig, tool_name):
                def wrapper(*args, **kwargs):
                    self.tool_calls_log.append({"name": tool_name, "args": kwargs})
                    return orig(*args, **kwargs)
                return wrapper

            t_tool.func = make_wrapper(orig_func, name)
            instrumented_tools.append(t_tool)
        return instrumented_tools


@pytest.mark.eval
@pytest.mark.langsmith(test_suite_name="Source_To_Generic_DOI_Enrichment_Trajectory")
@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["id_caso"])
def test_doi_enrichment_trajectory(case: dict[str, Any], require_real_llm, tmp_path: Path):
    """
    Verifica que el agente decida invocar o abstenerse de llamar a Crossref según el caso.
    """
    inputs = case["inputs"]
    reference = case["reference_outputs"]
    columns = inputs["columns"]
    sample_rows = inputs.get("sample_rows", [])

    # Preparar archivo CSV temporal con muestras
    csv_file = tmp_path / f"{case['id_caso']}.csv"
    with open(csv_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        if sample_rows:
            writer.writerows(sample_rows)
        else:
            writer.writerow({c: "" for c in columns})

    t.log_inputs({"id_caso": case["id_caso"], "columns": columns, "tags": case.get("tags", [])})
    t.log_reference_outputs(reference)

    generator = TrajectoryTrackingGenerator()
    state = {
        "source_csv_path": str(csv_file),
        "source_name": case["id_caso"],
    }
    context = generator.prepare_context(state)

    raw_mappings = generator.execute_llm_mapping(context, context["columns"])

    # Reconstruir la secuencia de herramientas ejecutadas
    tools_executed = [call["name"] for call in generator.tool_calls_log]
    if raw_mappings:
        tools_executed.append("save_column_mappings")

    output_payload = {
        "tools_executed": tools_executed,
        "tool_calls_details": generator.tool_calls_log,
        "mappings": raw_mappings,
    }
    t.log_outputs(output_payload)

    # 1. Evaluación de selección y trayectoria de herramientas
    class MockExample:
        def __init__(self, ref):
            self.outputs = ref

    class MockRun:
        def __init__(self, out):
            self.outputs = out

    tool_eval = eval_tool_selection(MockRun(output_payload), MockExample(reference))
    t.log_feedback(key=tool_eval["key"], score=tool_eval["score"], comment=tool_eval["comment"])

    # 2. Aserción sobre la trayectoria esperada
    expected_tools = reference.get("expected_tools", [])
    forbidden_tools = reference.get("forbidden_tools", [])

    for exp_t in expected_tools:
        assert exp_t in tools_executed, (
            f"Herramienta obligatoria '{exp_t}' no fue invocada. Ejecutadas: {tools_executed}"
        )

    for forb_t in forbidden_tools:
        assert forb_t not in tools_executed, (
            f"Herramienta prohibida '{forb_t}' fue invocada indebidamente."
        )

    # 3. Validación de argumentos de la tool si se invocó enrich_source_columns_from_doi
    if "enrich_source_columns_from_doi" in tools_executed:
        doi_call = next(
            (c for c in generator.tool_calls_log if c["name"] == "enrich_source_columns_from_doi"),
            None,
        )
        assert doi_call is not None, "Detalles de llamada a enrich_source_columns_from_doi no encontrados."

        expected_doi_col = reference.get("expected_doi_column")
        if expected_doi_col:
            actual_col = doi_call["args"].get("doi_column", "")
            assert actual_col.lower() == expected_doi_col.lower(), (
                f"Nombre de columna DOI esperado: '{expected_doi_col}', obtenido: '{actual_col}'"
            )
            t.log_feedback(key="doi_column_argument_accuracy", score=1.0)

        expected_target_fields = reference.get("expected_target_fields", [])
        if expected_target_fields:
            raw_targets = doi_call["args"].get("target_fields", [])
            actual_targets = [raw_targets] if isinstance(raw_targets, str) else list(raw_targets)
            actual_targets_lower = {f.lower() for f in actual_targets}
            missing_targets = [f for f in expected_target_fields if f.lower() not in actual_targets_lower]
            t.log_feedback(
                key="target_fields_argument_accuracy",
                score=1.0 if not missing_targets else 0.0,
            )
            assert not missing_targets, (
                f"Campos destino faltantes en enrich_source_columns_from_doi: {missing_targets}. "
                f"Solicitados: {actual_targets}"
            )

    # 4. Verificación de mapeo de columnas inferidas y mapeos esperados
    mapping_by_left = {str(m.get("left", "")).lower(): m.get("replace") for m in raw_mappings}

    expected_inferred = reference.get("expected_inferred_mappings", {})
    if expected_inferred:
        missing_inferred = [
            f"{inf_col}->{exp_rep}"
            for inf_col, exp_rep in expected_inferred.items()
            if mapping_by_left.get(inf_col.lower()) != exp_rep
        ]
        t.log_feedback(key="inferred_columns_mapped", score=1.0 if not missing_inferred else 0.0)
        assert not missing_inferred, (
            f"Columnas inferidas no mapeadas correctamente: {missing_inferred}. "
            f"Mapeos obtenidos: {raw_mappings}"
        )

    expected_mappings = reference.get("expected_mappings", {})
    if expected_mappings:
        missing_base = [
            f"{col}->{exp_rep}"
            for col, exp_rep in expected_mappings.items()
            if mapping_by_left.get(col.lower()) != exp_rep
        ]
        t.log_feedback(key="expected_mappings_accuracy", score=1.0 if not missing_base else 0.0)
        assert not missing_base, (
            f"Mapeos esperados faltantes o incorrectos: {missing_base}. "
            f"Mapeos obtenidos: {raw_mappings}"
        )

