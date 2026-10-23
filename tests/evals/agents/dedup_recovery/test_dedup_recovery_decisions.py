"""
Evaluación de Toma de Decisiones, Memoria Episódica y Ejecución Transaccional de `DedupRecoveryAgent` con Pytest + LangSmith.

Módulo: tests/evals/agents/dedup_recovery/test_dedup_recovery_decisions.py
Suite de LangSmith: Dedup_Recovery_Decisions_And_Execution

Evalúa con Pytest y LLMs reales (sin mocks):
  1. Deducción autónoma Zero-Shot para anomalías de fechas en inglés y español.
  2. Transferencia positiva desde Memoria Episódica (CBR) para `value_mapping` y `regex_extraction`.
  3. Inmunidad ante episodios distractores y discriminación en recuperación mixta (`top_k=2`).
  4. Detección de anomalías en columnas distintas de `date` (`author`) mediante escaneo real del CSV.
  5. Calibración de abstención y escalamiento HITL (`manual_review`) ante rangos plurianuales ambiguos.
  6. Ejecución física de la reparación (`decision.execute`) bajo `CsvTransaction` y validación estructural.

Las métricas condicionales (`eval_strategy_params_validity`, `eval_episodic_memory_effect`,
`eval_csv_repair_execution`) se evalúan y registran únicamente en los casos específicos
donde aplican, evitando entradas con "No Feedback".
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest
from langsmith import testing as t

from core.agent.dedup_recovery.agent import invoke_recovery_agent
from core.agent.dedup_recovery.transaction import CsvTransaction
from core.utils.config import config
from tests.evals.agents.dedup_recovery.evaluate_dedup_recovery import build_episode_results
from tests.evals.metrics.dedup_recovery_metrics import (
    eval_ambiguity_detection,
    eval_confidence_calibration,
    eval_csv_repair_execution,
    eval_episodic_memory_effect,
    eval_hitl_calibration,
    eval_strategy_accuracy,
    eval_strategy_params_validity,
    eval_target_column_accuracy,
)

CASES_FILE_PATH = (
    Path(__file__).parent.parent.parent
    / "datasets"
    / "dedup_recovery"
    / "dedup_recovery_cases.json"
)


def _load_cases() -> list[dict[str, Any]]:
    """Carga los casos declarativos de evaluación del agente de recuperación."""
    if not CASES_FILE_PATH.exists():
        raise FileNotFoundError(f"No se encontró el dataset en: {CASES_FILE_PATH}")
    with open(CASES_FILE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def require_real_llm():
    """Exige un LLM real configurado; sin credenciales se omite el test (sin mocks)."""
    if not (
        config.GROQ_API_KEY
        or config.NVIDIA_API_KEY
        or config.OPEN_ROUTER_API_KEY
        or config.GEMINI_API_KEY
    ):
        pytest.skip("No hay API key de LLM configurada para ejecutar la evaluación semántica.")


@pytest.mark.eval
@pytest.mark.llm_eval
@pytest.mark.langsmith(test_suite_name="Dedup_Recovery_Decisions_And_Execution")
@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["id_caso"])
def test_dedup_recovery_decisions_and_execution(
    case: dict[str, Any], require_real_llm, tmp_path: Path
):
    """
    Verifica el contrato integral de `DedupRecoveryAgent`:
      - Selección de estrategia, columna objetivo, nivel de confianza y detección de ambigüedad.
      - Validez de parámetros específicos (`mapping_rules` / `regex_pattern`).
      - Efecto de Memoria Episódica (transferencia positiva e inmunidad a distractores).
      - Reparación atómica del archivo CSV real y preservación de invariantes estructurales.
    """
    inputs = case["inputs"]
    reference = case["reference_outputs"]
    columns = inputs["columns"]
    sample_rows = inputs.get("sample_rows", [])
    source_name = inputs["source_name"]
    error_msg = inputs["error_msg"]
    episodes = build_episode_results(inputs.get("episodes", []))

    # 1. Crear archivo CSV temporal real para escaneo de contexto y ejecución de la estrategia
    csv_file = tmp_path / f"{case['id_caso']}.csv"
    with open(csv_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        if sample_rows:
            writer.writerows(sample_rows)
        else:
            writer.writerow({c: "" for c in columns})

    t.log_inputs({
        "id_caso": case["id_caso"],
        "source_name": source_name,
        "error_msg": error_msg,
        "columns": columns,
        "memory_mode": inputs.get("memory_mode", "none"),
        "episodes_count": len(episodes),
        "tags": case.get("tags", []),
    })
    t.log_reference_outputs(reference)

    # 2. Invocar al agente de recuperación con LLM real
    decision = invoke_recovery_agent(
        error_msg=error_msg,
        source_name=source_name,
        csv_path=str(csv_file),
        episodes=episodes,
        llm=None,
    )

    # 3. Si la decisión es autorreparable, ejecutar la transformación bajo CsvTransaction
    repair_execution_payload: dict[str, Any] = {
        "status": "skipped",
        "modified_count": 0,
        "structure_valid": False,
        "cleaned_by_id": {},
    }

    if decision.is_auto_repairable:
        with CsvTransaction(str(csv_file)) as tx:
            strat_res = decision.execute(str(csv_file))
            val_res = tx.validate()
            if strat_res.status == "ok" and val_res.is_valid:
                tx.commit()
            else:
                tx.rollback()

        cleaned_by_id: dict[str, str] = {}
        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                row_id = str(row.get("id", ""))
                cleaned_by_id[row_id] = str(row.get(decision.target_column, ""))

        repair_execution_payload = {
            "status": strat_res.status,
            "modified_count": strat_res.modified_count,
            "structure_valid": val_res.is_valid,
            "validation_errors": val_res.errors,
            "cleaned_by_id": cleaned_by_id,
        }

    outputs_payload = {
        "decision": decision.model_dump(),
        "is_auto_repairable": decision.is_auto_repairable,
        "repair_execution": repair_execution_payload,
    }
    t.log_outputs(outputs_payload)

    mock_run = type("RunPayload", (), {"outputs": outputs_payload})()
    mock_example = type("ExamplePayload", (), {"inputs": inputs, "outputs": reference})()

    # 4. Evaluar y registrar métricas universales
    res_strategy = eval_strategy_accuracy(mock_run, mock_example)
    res_col = eval_target_column_accuracy(mock_run, mock_example)
    res_amb = eval_ambiguity_detection(mock_run, mock_example)
    res_hitl = eval_hitl_calibration(mock_run, mock_example)
    res_conf = eval_confidence_calibration(mock_run, mock_example)

    for res in (res_strategy, res_col, res_amb, res_hitl, res_conf):
        t.log_feedback(
            key=res["key"],
            score=res["score"],
            value=res.get("value"),
            comment=res.get("comment"),
        )

    # 5. Evaluar y registrar métricas específicas únicamente en los casos donde aplican
    res_params = eval_strategy_params_validity(mock_run, mock_example)
    if res_params["score"] is not None:
        t.log_feedback(
            key=res_params["key"],
            score=res_params["score"],
            value=res_params.get("value"),
            comment=res_params.get("comment"),
        )

    res_memory = eval_episodic_memory_effect(mock_run, mock_example)
    if res_memory["score"] is not None:
        t.log_feedback(
            key=res_memory["key"],
            score=res_memory["score"],
            value=res_memory.get("value"),
            comment=res_memory.get("comment"),
        )

    res_repair = eval_csv_repair_execution(mock_run, mock_example)
    if res_repair["score"] is not None:
        t.log_feedback(
            key=res_repair["key"],
            score=res_repair["score"],
            value=res_repair.get("value"),
            comment=res_repair.get("comment"),
        )

    # 6. Aserciones duras e invariantes de CI (sin "verde por casualidad")
    assert res_col["score"] == 1.0, f"Fallo en columna objetivo: {res_col['comment']}"
    assert res_strategy["score"] == 1.0, f"Fallo en estrategia elegida: {res_strategy['comment']}"
    assert res_amb["score"] == 1.0, f"Fallo en detección de ambigüedad: {res_amb['comment']}"
    assert res_hitl["score"] == 1.0, f"Fallo en calibración HITL/autorreparación: {res_hitl['comment']}"
    assert res_conf["score"] == 1.0, f"Fallo en nivel de confianza: {res_conf['comment']}"

    if res_params["score"] is not None:
        assert res_params["score"] == 1.0, (
            f"Parámetros de estrategia inválidos: {res_params['comment']}"
        )

    if res_memory["score"] is not None:
        assert res_memory["score"] == 1.0, (
            f"Fallo en transferencia o discriminación de memoria episódica: {res_memory['comment']}"
        )

    if res_repair["score"] is not None:
        assert res_repair["score"] == 1.0, (
            f"La ejecución de la estrategia no reparó correctamente el CSV: {res_repair['comment']}"
        )
