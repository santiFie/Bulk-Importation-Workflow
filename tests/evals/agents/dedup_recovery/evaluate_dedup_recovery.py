"""
Evaluación Nativa de LangSmith para el Agente de Recuperación Reactiva (`DedupRecoveryAgent`).

Módulo: tests/evals/agents/dedup_recovery/evaluate_dedup_recovery.py
Dataset en LangSmith: Dedup_Recovery_Decision_Cases

Ejecuta una evaluación formal y sistemática en LangSmith usando `aevaluate()` conectando
el agente real con los modelos LLM configurados (sin mocks).

Utiliza exclusivamente `DEDUP_RECOVERY_EVALUATORS` (métricas 100% homogéneas que aplican
a todas las filas del dataset), evitando celdas con "No Feedback" en la tabla comparativa
de experimentos de LangSmith. Las validaciones condicionales específicas de cada caso
(parámetros de regex/mapping, efecto de memoria episódica y ejecución transaccional en CSV)
se evalúan por separado en `test_dedup_recovery_decisions.py`.

Uso:
  .venv/bin/python tests/evals/agents/dedup_recovery/evaluate_dedup_recovery.py
"""

from __future__ import annotations

import asyncio
import contextvars
import csv
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from langsmith import Client, aevaluate, traceable

from core.agent.dedup_recovery.agent import invoke_recovery_agent
from core.memory.episodic_memory.models import (
    Episode,
    EpisodeMetadata,
    EpisodePayload,
    EpisodeQueryResult,
)
from tests.evals.metrics.dedup_recovery_metrics import DEDUP_RECOVERY_EVALUATORS

DATASET_NAME = "Dedup_Recovery_Decision_Cases"
CASES_FILE_PATH = (
    PROJECT_ROOT
    / "tests"
    / "evals"
    / "datasets"
    / "dedup_recovery"
    / "dedup_recovery_cases.json"
)


def build_episode_results(raw_episodes: list[dict[str, Any]]) -> list[EpisodeQueryResult]:
    """Deserializa la lista declarativa de episodios JSON a objetos `EpisodeQueryResult`."""
    results: list[EpisodeQueryResult] = []
    for item in raw_episodes:
        meta_dict = item.get("metadata", {})
        payload_dict = item.get("payload", {})
        ep = Episode(
            id=item["id"],
            metadata=EpisodeMetadata(**meta_dict),
            embedding_content=item.get("embedding_content", ""),
            payload=EpisodePayload(**payload_dict),
        )
        results.append(
            EpisodeQueryResult(
                episode=ep,
                similarity_score=float(item.get("similarity_score", 0.9)),
            )
        )
    return results


def sync_dataset_to_langsmith(client: Client, dataset_name: str = DATASET_NAME) -> Any:
    """
    Sincroniza idempotentemente el dataset local de casos de recuperación con LangSmith.
    Limpia ejemplos previos para asegurar correspondencia exacta con el JSON declarativo.
    """
    if not CASES_FILE_PATH.exists():
        raise FileNotFoundError(f"No se encontró el dataset en: {CASES_FILE_PATH}")

    with open(CASES_FILE_PATH, "r", encoding="utf-8") as f:
        cases = json.load(f)

    if not client.has_dataset(dataset_name=dataset_name):
        print(f"Creando dataset '{dataset_name}' en LangSmith...")
        dataset = client.create_dataset(
            dataset_name=dataset_name,
            description=(
                "Casos canónicos de evaluación de toma de decisiones, transferencia de "
                "Memoria Episódica (CBR) y calibración HITL para DedupRecoveryAgent."
            ),
        )
    else:
        print(f"Dataset '{dataset_name}' existente. Sincronizando ejemplos canónicos...")
        dataset = client.read_dataset(dataset_name=dataset_name)
        existing_examples = list(client.list_examples(dataset_id=dataset.id))
        if existing_examples:
            client.delete_examples([e.id for e in existing_examples])

    for caso in cases:
        inputs = caso["inputs"]
        client.create_example(
            inputs={
                "id_caso": caso["id_caso"],
                "source_name": inputs["source_name"],
                "error_msg": inputs["error_msg"],
                "columns": inputs["columns"],
                "sample_rows": inputs.get("sample_rows", []),
                "memory_mode": inputs.get("memory_mode", "none"),
                "episodes": inputs.get("episodes", []),
            },
            outputs=caso["reference_outputs"],
            dataset_id=dataset.id,
        )

    print(f"✓ {len(cases)} ejemplos sincronizados correctamente en el dataset '{dataset_name}'.")
    return dataset


@traceable(name="dedup_recovery_predict")
async def predict(inputs: dict[str, Any]) -> dict[str, Any]:
    """
    Función target de evaluación nativa:
    Materializa el CSV temporal con las muestras reales del caso e invoca a `invoke_recovery_agent`.
    """
    columns = inputs["columns"]
    sample_rows = inputs.get("sample_rows", [])
    source_name = inputs["source_name"]
    error_msg = inputs["error_msg"]
    episodes = build_episode_results(inputs.get("episodes", []))

    tmp_csv = tempfile.NamedTemporaryFile(
        "w", suffix=".csv", delete=False, encoding="utf-8", newline=""
    )
    try:
        writer = csv.DictWriter(tmp_csv, fieldnames=columns)
        writer.writeheader()
        if sample_rows:
            writer.writerows(sample_rows)
        else:
            writer.writerow({c: "" for c in columns})
        tmp_csv.flush()
        tmp_csv_path = tmp_csv.name
    finally:
        tmp_csv.close()

    try:
        ctx = contextvars.copy_context()
        loop = asyncio.get_running_loop()
        decision = await loop.run_in_executor(
            None,
            ctx.run,
            invoke_recovery_agent,
            error_msg,
            source_name,
            tmp_csv_path,
            episodes,
            None,
        )

        return {
            "decision": decision.model_dump(),
            "is_auto_repairable": decision.is_auto_repairable,
        }
    finally:
        if os.path.exists(tmp_csv_path):
            os.remove(tmp_csv_path)


async def run_evaluation(experiment_prefix: str = "Dedup_Recovery_Benchmark") -> Any:
    """Ejecuta el benchmark homogéneo nativo en LangSmith."""
    client = Client()
    sync_dataset_to_langsmith(client, DATASET_NAME)

    print(f"\nIniciando corrida de evaluación nativa: {experiment_prefix}...")
    results = await aevaluate(
        predict,
        data=DATASET_NAME,
        evaluators=DEDUP_RECOVERY_EVALUATORS,
        experiment_prefix=experiment_prefix,
        description=(
            "Evaluación homogénea de estrategia, columna objetivo, detección de ambigüedad "
            "y calibración HITL en DedupRecoveryAgent."
        ),
        max_concurrency=1,  # Serial para respetar rate limits de los proveedores de LLM
        client=client,
    )
    print("\n✓ Evaluación completada con éxito en LangSmith.")
    return results


if __name__ == "__main__":
    asyncio.run(run_evaluation())
