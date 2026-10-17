"""
Evaluación Nativa de LangSmith para TargetSediciCrosswalkGenerator.

Módulo: tests/evals/agents/target_crosswalk/evaluate_target_crosswalk_mapping.py
Dataset en LangSmith: Target_Crosswalk_Mapping_Cases

Ejecuta una evaluación sistemática en LangSmith usando `aevaluate()` conectando
el agente real con los modelos LLM configurados (sin mocks) para benchmarking
y comparación lado a lado (Side-by-Side) en la plataforma.

Uso:
  python tests/evals/agents/target_crosswalk/evaluate_target_crosswalk_mapping.py
"""

from __future__ import annotations

import asyncio
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

from core.nodes.target_crosswalk_agent.node import TargetSediciCrosswalkGenerator
from tests.evals.metrics.target_crosswalk_metrics import TARGET_CROSSWALK_EVALUATORS

DATASET_NAME = "Target_Crosswalk_Mapping_Cases"
CASES_FILE_PATH = (
    PROJECT_ROOT
    / "tests"
    / "evals"
    / "datasets"
    / "target_crosswalk"
    / "target_crosswalk_cases.json"
)


def sync_dataset_to_langsmith(client: Client, dataset_name: str = DATASET_NAME) -> Any:
    """
    Sincroniza el dataset local de casos de crosswalk hacia SEDICI con LangSmith.
    Limpia ejemplos previos para asegurar paridad estricta con el archivo JSON declarativo.
    """
    if not CASES_FILE_PATH.exists():
        raise FileNotFoundError(f"No se encontró el archivo del dataset en: {CASES_FILE_PATH}")

    with open(CASES_FILE_PATH, "r", encoding="utf-8") as f:
        cases = json.load(f)

    if not client.has_dataset(dataset_name=dataset_name):
        print(f"Creando dataset '{dataset_name}' en LangSmith...")
        dataset = client.create_dataset(
            dataset_name=dataset_name,
            description="Casos canónicos de evaluación de mapeo hacia SEDICI y autorreparación de configuraciones.",
        )
    else:
        print(f"Dataset '{dataset_name}' ya existente. Sincronizando ejemplos...")
        dataset = client.read_dataset(dataset_name=dataset_name)
        existing_examples = list(client.list_examples(dataset_id=dataset.id))
        if existing_examples:
            client.delete_examples([e.id for e in existing_examples])

    for caso in cases:
        client.create_example(
            inputs={
                "id_caso": caso["id_caso"],
                "source_name": caso["inputs"]["source_name"],
                "columns": caso["inputs"]["columns"],
                "sample_rows": caso["inputs"].get("sample_rows", []),
                "source_crosswalk_config": caso["inputs"].get("source_crosswalk_config"),
                "user_target_config": caso["inputs"].get("user_target_config"),
            },
            outputs=caso["reference_outputs"],
            dataset_id=dataset.id,
        )

    print(f"✓ {len(cases)} ejemplos sincronizados correctamente en el dataset '{dataset_name}'.")
    return dataset


class BenchmarkTargetGenerator(TargetSediciCrosswalkGenerator):
    """Subclase para registrar la ejecución de herramientas durante el benchmarking nativo."""

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


@traceable(name="target_crosswalk_predict")
async def predict(inputs: dict[str, Any]) -> dict[str, Any]:
    """
    Función target de evaluación nativa:
    Crea archivos temporales y ejecuta TargetSediciCrosswalkGenerator con el modelo real.
    """
    columns = inputs["columns"]
    sample_rows = inputs.get("sample_rows", [])
    source_name = inputs.get("source_name", "benchmark_source")

    # 1. Crear CSV temporal
    tmp_csv = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8", newline="")
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

    tmp_source_cfg_path = None
    tmp_user_cfg_path = None

    try:
        # 2. Configuración previa source_to_generic si existe
        if inputs.get("source_crosswalk_config"):
            tmp_scfg = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
            try:
                json.dump(inputs["source_crosswalk_config"], tmp_scfg)
                tmp_scfg.flush()
                tmp_source_cfg_path = tmp_scfg.name
            finally:
                tmp_scfg.close()

        # 3. Configuración del usuario a reparar si existe
        if inputs.get("user_target_config"):
            tmp_ucfg = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
            try:
                json.dump(inputs["user_target_config"], tmp_ucfg)
                tmp_ucfg.flush()
                tmp_user_cfg_path = tmp_ucfg.name
            finally:
                tmp_ucfg.close()

        workspace_dir = tempfile.mkdtemp(prefix="target_eval_")

        generator = BenchmarkTargetGenerator()
        state: dict[str, Any] = {
            "source_name": source_name,
            "workspace_dir": workspace_dir,
            "reconciled_csv_path": tmp_csv_path,
        }
        if tmp_source_cfg_path:
            state["source_crosswalk_config"] = tmp_source_cfg_path
        if tmp_user_cfg_path:
            state["sedici_target_crosswalk_config"] = tmp_user_cfg_path

        loop = asyncio.get_running_loop()
        result_state = await loop.run_in_executor(None, generator.generate_config, state)
        config_path = result_state.get("sedici_target_crosswalk_config")

        mappings: list[dict[str, Any]] = []
        if config_path and os.path.isfile(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            mappings = data[0] if isinstance(data, list) and len(data) > 0 else []

        return {
            "mappings": mappings,
            "tools_executed": generator.tools_executed,
            "config_path": config_path,
        }

    finally:
        for p in (tmp_csv_path, tmp_source_cfg_path, tmp_user_cfg_path):
            if p and os.path.exists(p):
                os.remove(p)


async def run_evaluation(experiment_prefix: str = "Target_Crosswalk_Benchmark") -> Any:
    """Ejecuta la evaluación nativa en LangSmith."""
    client = Client()
    sync_dataset_to_langsmith(client, DATASET_NAME)

    print(f"\nIniciando corrida de evaluación nativa: {experiment_prefix}...")
    results = await aevaluate(
        predict,
        data=DATASET_NAME,
        evaluators=TARGET_CROSSWALK_EVALUATORS,
        experiment_prefix=experiment_prefix,
        description="Evaluación de fidelidad de mapeo a SEDICI y autorreparación de configuraciones erróneas.",
        max_concurrency=1,  # Respetar rate limits de LLMs reales
        client=client,
    )
    print("\n✓ Evaluación completada con éxito en LangSmith.")
    return results


if __name__ == "__main__":
    asyncio.run(run_evaluation())
