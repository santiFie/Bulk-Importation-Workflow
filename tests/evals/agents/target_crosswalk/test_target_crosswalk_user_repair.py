"""
Evaluación de Resiliencia y Autorreparación de Configuración del Usuario con Pytest + LangSmith.

Módulo: tests/evals/agents/target_crosswalk/test_target_crosswalk_user_repair.py
Suite de LangSmith: Target_Crosswalk_User_Config_Repair

Evalúa con LLMs reales (sin mocks):
  1. Detección de incompatibilidades en configuraciones suministradas por humanos.
  2. Autorreparación con LLM ante columnas origen inexistentes o renombradas en el CSV.
  3. Corrección de metadatos de destino inválidos hacia el catálogo formal de SEDICI.
  4. Omisión limpia de mapeos hacia columnas superfluas o eliminadas en la deduplicación.
  5. Preservación intacta de mapeos válidos preexistentes sin regresiones.
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
    eval_forbidden_mappings_absence,
    eval_header_fidelity,
    eval_target_schema_conformance,
    eval_tool_trajectory,
    eval_user_config_repair_accuracy,
    eval_valid_mappings_preservation,
)

CASES_FILE_PATH = (
    Path(__file__).parent.parent.parent
    / "datasets"
    / "target_crosswalk"
    / "target_crosswalk_cases.json"
)


def _load_repair_cases() -> list[dict[str, Any]]:
    """Carga los casos de prueba declarativos de autorreparación de configuraciones humanas."""
    if not CASES_FILE_PATH.exists():
        raise FileNotFoundError(f"No se encontró el dataset en: {CASES_FILE_PATH}")
    with open(CASES_FILE_PATH, "r", encoding="utf-8") as f:
        all_cases = json.load(f)
    return [c for c in all_cases if "repair" in c.get("tags", [])]


def _has_llm_api_key() -> bool:
    """Verifica si hay credenciales de LLM disponibles para la evaluación real."""
    return bool(config.GROQ_API_KEY or config.NVIDIA_API_KEY or config.OPEN_ROUTER_API_KEY)


class TrajectoryTrackingTargetGenerator(TargetSediciCrosswalkGenerator):
    """Subclase instrumentada para auditar tool calls durante la autorreparación."""

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
@pytest.mark.langsmith(test_suite_name="Target_Crosswalk_User_Config_Repair")
@pytest.mark.parametrize("case", _load_repair_cases(), ids=lambda c: c["id_caso"])
def test_target_crosswalk_user_repair_eval(case: dict[str, Any], tmp_path: Path):
    """
    Verifica que el agente valide y repare una configuración de crosswalk errónea
    provista por el usuario conectando con el LLM real.
    """
    inputs = case["inputs"]
    reference = case["reference_outputs"]
    columns = inputs["columns"]
    sample_rows = inputs.get("sample_rows", [])

    if not _has_llm_api_key():
        pytest.skip("No hay API key de LLM configurada para ejecutar la autorreparación real.")

    # 1. Crear CSV temporal representativo
    csv_file = tmp_path / f"reconciled_{case['id_caso']}.csv"
    with open(csv_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        if sample_rows:
            writer.writerows(sample_rows)
        else:
            writer.writerow({c: "" for c in columns})

    # 2. Guardar la configuración provista por el usuario (con errores a reparar)
    user_cfg_path = tmp_path / f"user_config_{case['id_caso']}.json"
    with open(user_cfg_path, "w", encoding="utf-8") as f:
        json.dump(inputs["user_target_config"], f)

    # 3. Registrar inputs y referencias en LangSmith
    t.log_inputs({
        "id_caso": case["id_caso"],
        "source_name": inputs["source_name"],
        "columns": columns,
        "user_target_config": inputs["user_target_config"],
    })
    t.log_reference_outputs(reference)

    # 4. Invocación del agente con configuración del usuario a validar y reparar
    generator = TrajectoryTrackingTargetGenerator()
    state = {
        "source_name": inputs["source_name"],
        "workspace_dir": str(tmp_path),
        "reconciled_csv_path": str(csv_file),
        "sedici_target_crosswalk_config": str(user_cfg_path),
    }

    result_state = generator.generate_config(state)
    repaired_config_path = result_state.get("sedici_target_crosswalk_config")
    assert repaired_config_path and os.path.isfile(repaired_config_path), (
        f"El agente no generó la configuración reparada en disco para '{case['id_caso']}'"
    )

    with open(repaired_config_path, "r", encoding="utf-8") as f:
        config_data = json.load(f)

    mappings = config_data[0] if isinstance(config_data, list) and len(config_data) > 0 else []
    tools_executed = generator.tools_executed

    outputs_payload = {
        "mappings": mappings,
        "tools_executed": tools_executed,
        "repaired_config_path": repaired_config_path,
        "omitted_columns": getattr(generator, "last_omitted_columns", []),
    }
    t.log_outputs(outputs_payload)

    # 5. Evaluación semántica de autorreparación
    mock_run = type("RunPayload", (), {"outputs": outputs_payload})()
    mock_example = type("ExamplePayload", (), {"inputs": inputs, "outputs": reference})()

    res_header = eval_header_fidelity(mock_run, mock_example)
    res_schema = eval_target_schema_conformance(mock_run, mock_example)
    res_repair = eval_user_config_repair_accuracy(mock_run, mock_example)
    res_preservation = eval_valid_mappings_preservation(mock_run, mock_example)
    res_forbidden = eval_forbidden_mappings_absence(mock_run, mock_example)
    res_trajectory = eval_tool_trajectory(mock_run, mock_example)

    # 6. Registrar feedbacks en LangSmith
    t.log_feedback(key=res_header["key"], score=res_header["score"], comment=res_header["comment"])
    t.log_feedback(key=res_schema["key"], score=res_schema["score"], comment=res_schema["comment"])
    if res_repair["score"] is not None:
        t.log_feedback(key=res_repair["key"], score=res_repair["score"], comment=res_repair["comment"])
    if res_preservation["score"] is not None:
        t.log_feedback(key=res_preservation["key"], score=res_preservation["score"], comment=res_preservation["comment"])
    t.log_feedback(key=res_forbidden["key"], score=res_forbidden["score"], comment=res_forbidden["comment"])
    if res_trajectory["score"] is not None:
        t.log_feedback(key=res_trajectory["key"], score=res_trajectory["score"], comment=res_trajectory["comment"])

    # 7. Aserciones duras de CI
    assert res_header["score"] == 1.0, f"Fallo de fidelidad de cabeceras en autorreparación: {res_header['comment']}"
    assert res_schema["score"] == 1.0, f"Fallo de esquema SEDICI en autorreparación: {res_schema['comment']}"
    assert res_forbidden["score"] == 1.0, f"Fallo de mapeos prohibidos: {res_forbidden['comment']}"

    if res_repair["score"] is not None:
        assert res_repair["score"] == 1.0, f"Fallo en la corrección de mapeos incompatibles: {res_repair['comment']}"

    if res_preservation["score"] is not None:
        assert res_preservation["score"] == 1.0, (
            f"Fallo en la preservación de mapeos válidos preexistentes: {res_preservation['comment']}"
        )

    if res_trajectory["score"] is not None:
        assert res_trajectory["score"] == 1.0, f"Desvío de trayectoria: {res_trajectory['comment']}"
