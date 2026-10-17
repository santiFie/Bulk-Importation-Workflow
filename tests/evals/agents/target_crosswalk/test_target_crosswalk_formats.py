"""
Evaluación de Robustez de Formatos de Columnas Post-Deduplicación con Pytest + LangSmith.

Módulo: tests/evals/agents/target_crosswalk/test_target_crosswalk_formats.py
Suite de LangSmith: Target_Crosswalk_Format_Robustness

Evalúa con LLMs reales (sin mocks):
  1. Resolución directa determinista para formato genérico canónico (PDF Ingest).
  2. Mapeo semántico de remanentes y abstención en formato heterogéneo (PubMed).
  3. Cobertura conjunta de columnas fuente y genéricas inyectadas en formato híbrido (Scopus).
  4. Abstención estricta ante columnas internas y métricas propietarias ruidosas.
  5. Invariantes de fidelidad de cabeceras y esquema oficial de SEDICI.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any

import pytest
from langsmith import testing as t

from core.nodes.target_crosswalk_agent.node import TargetSediciCrosswalkGenerator
from core.utils.config import config
from tests.evals.metrics.target_crosswalk_metrics import (
    eval_deterministic_core_preservation,
    eval_expected_mappings_accuracy,
    eval_forbidden_mappings_absence,
    eval_header_fidelity,
    eval_target_schema_conformance,
    eval_tool_trajectory,
)

CASES_FILE_PATH = (
    Path(__file__).parent.parent.parent
    / "datasets"
    / "target_crosswalk"
    / "target_crosswalk_cases.json"
)


def _load_format_cases() -> list[dict[str, Any]]:
    """Carga los casos de prueba declarativos de formatos post-deduplicación."""
    if not CASES_FILE_PATH.exists():
        raise FileNotFoundError(f"No se encontró el dataset en: {CASES_FILE_PATH}")
    with open(CASES_FILE_PATH, "r", encoding="utf-8") as f:
        all_cases = json.load(f)
    return [c for c in all_cases if "format" in c.get("tags", [])]


def _has_llm_api_key() -> bool:
    """Verifica si hay alguna credencial de LLM configurada para corridas reales."""
    return bool(config.GROQ_API_KEY or config.NVIDIA_API_KEY or config.OPEN_ROUTER_API_KEY)


class TrajectoryTrackingTargetGenerator(TargetSediciCrosswalkGenerator):
    """Subclase instrumentada para registrar las llamadas al bucle de LLM."""

    def __init__(self):
        super().__init__()
        self.tools_executed: list[str] = []

    def execute_llm_mapping(
        self,
        context: dict[str, Any],
        remaining_columns: list[str],
        feedback: str | None = None,
    ) -> list[dict[str, Any]]:
        self.tools_executed.append("save_column_mappings")
        return super().execute_llm_mapping(context, remaining_columns, feedback=feedback)


@pytest.mark.eval
@pytest.mark.langsmith(test_suite_name="Target_Crosswalk_Format_Robustness")
@pytest.mark.parametrize("case", _load_format_cases(), ids=lambda c: c["id_caso"])
def test_target_crosswalk_formats_eval(case: dict[str, Any], tmp_path: Path):
    """
    Evalúa la generación del crosswalk hacia SEDICI según los distintos formatos de columnas.
    """
    inputs = case["inputs"]
    reference = case["reference_outputs"]
    columns = inputs["columns"]
    sample_rows = inputs.get("sample_rows", [])
    expected_tools = reference.get("expected_tools", [])

    # Regla 1: Si el caso requiere LLM real y no hay API key, se omite con skip (sin mocks)
    if expected_tools and not _has_llm_api_key():
        pytest.skip("No hay API key de LLM configurada para ejecutar mapeo con modelo real.")

    # 1. Crear CSV temporal con las columnas y muestras representativas del caso
    csv_file = tmp_path / f"reconciled_{case['id_caso']}.csv"
    with open(csv_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        if sample_rows:
            writer.writerows(sample_rows)
        else:
            writer.writerow({c: "" for c in columns})

    # 2. Configurar source_crosswalk_config si está provisto
    source_cfg_path = None
    if inputs.get("source_crosswalk_config"):
        cfg_file = tmp_path / f"source_cfg_{case['id_caso']}.json"
        with open(cfg_file, "w", encoding="utf-8") as f:
            json.dump(inputs["source_crosswalk_config"], f)
        source_cfg_path = str(cfg_file)

    # 3. Registrar inputs y referencias en LangSmith
    t.log_inputs({
        "id_caso": case["id_caso"],
        "source_name": inputs["source_name"],
        "columns": columns,
        "sample_rows_count": len(sample_rows),
    })
    t.log_reference_outputs(reference)

    # 4. Ejecución del agente real con tracking de trayectoria
    generator = TrajectoryTrackingTargetGenerator()
    state = {
        "source_name": inputs["source_name"],
        "workspace_dir": str(tmp_path),
        "reconciled_csv_path": str(csv_file),
        "source_crosswalk_config": source_cfg_path,
    }

    result_state = generator.generate_config(state)
    target_config_path = result_state.get("sedici_target_crosswalk_config")
    assert target_config_path and os.path.isfile(target_config_path), (
        f"El agente no generó el archivo de configuración en disco para '{case['id_caso']}'"
    )

    with open(target_config_path, "r", encoding="utf-8") as f:
        config_data = json.load(f)

    mappings = config_data[0] if isinstance(config_data, list) and len(config_data) > 0 else []
    tools_executed = generator.tools_executed

    outputs_payload = {
        "mappings": mappings,
        "tools_executed": tools_executed,
        "target_config_path": target_config_path,
    }
    t.log_outputs(outputs_payload)

    # 5. Evaluación con métricas canónicas
    mock_run = type("RunPayload", (), {"outputs": outputs_payload})()
    mock_example = type("ExamplePayload", (), {"inputs": inputs, "outputs": reference})()

    res_header = eval_header_fidelity(mock_run, mock_example)
    res_schema = eval_target_schema_conformance(mock_run, mock_example)
    res_core = eval_deterministic_core_preservation(mock_run, mock_example)
    res_accuracy = eval_expected_mappings_accuracy(mock_run, mock_example)
    res_forbidden = eval_forbidden_mappings_absence(mock_run, mock_example)
    res_trajectory = eval_tool_trajectory(mock_run, mock_example)

    # 6. Registrar feedbacks en LangSmith
    t.log_feedback(key=res_header["key"], score=res_header["score"], comment=res_header["comment"])
    t.log_feedback(key=res_schema["key"], score=res_schema["score"], comment=res_schema["comment"])
    if res_core["score"] is not None:
        t.log_feedback(key=res_core["key"], score=res_core["score"], comment=res_core["comment"])
    if res_accuracy["score"] is not None:
        t.log_feedback(key=res_accuracy["key"], score=res_accuracy["score"], comment=res_accuracy["comment"])
    t.log_feedback(key=res_forbidden["key"], score=res_forbidden["score"], comment=res_forbidden["comment"])
    if res_trajectory["score"] is not None:
        t.log_feedback(key=res_trajectory["key"], score=res_trajectory["score"], comment=res_trajectory["comment"])

    # 7. Aserciones duras de CI
    assert res_header["score"] == 1.0, f"Fallo de fidelidad de cabeceras: {res_header['comment']}"
    assert res_schema["score"] == 1.0, f"Fallo de conformidad con esquema SEDICI: {res_schema['comment']}"
    assert res_forbidden["score"] == 1.0, f"Fallo de abstención: {res_forbidden['comment']}"

    if res_core["score"] is not None:
        assert res_core["score"] == 1.0, f"Pérdida de campos críticos: {res_core['comment']}"

    if res_accuracy["score"] is not None:
        assert res_accuracy["score"] >= 0.8, f"Exactitud de mapeos insuficiente: {res_accuracy['comment']}"

    if res_trajectory["score"] is not None:
        assert res_trajectory["score"] == 1.0, f"Desvío de trayectoria: {res_trajectory['comment']}"
