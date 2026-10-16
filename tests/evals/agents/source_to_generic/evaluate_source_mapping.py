"""
Evaluación Nativa de LangSmith para el mapeo semántico de SourceToGenericCrosswalkGenerator.

Módulo: tests/evals/agents/source_to_generic/evaluate_source_mapping.py
Dataset en LangSmith: Source_To_Generic_Mapping_Cases

Este script ejecuta una evaluación formal y sistemática en LangSmith usando `aevaluate()`.
A diferencia de los tests con mocks, conecta el agente real con los modelos LLM configurados.

Ventajas:
  - Dataset canónico de 6 casos (positivos, negativos y límite) sin duplicados.
  - Métricas homogéneas calculadas automáticamente con observabilidad en LangSmith.
  - Comparación visual lado a lado (Side-by-Side) en la UI de LangSmith para prompt tuning.

Uso:
  python tests/evals/agents/source_to_generic/evaluate_source_mapping.py
"""

from __future__ import annotations
import contextvars
from langsmith import traceable

import asyncio
import csv
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Asegurar path raíz del proyecto
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from langsmith import Client, aevaluate

from core.nodes.source_to_generic.node import SourceToGenericCrosswalkGenerator
from tests.evals.metrics.source_to_generic_metrics import SOURCE_MAPPING_EVALUATORS

DATASET_NAME = "Source_To_Generic_Mapping_Cases"
CASES_FILE_PATH = (
    PROJECT_ROOT
    / "tests"
    / "evals"
    / "datasets"
    / "source_to_generic"
    / "source_mapping_cases.json"
)


def sync_dataset_to_langsmith(client: Client, dataset_name: str = DATASET_NAME) -> Any:
    """
    Sincroniza el dataset local de casos de mapeo con LangSmith.
    Limpia ejemplos anteriores para garantizar exactamente los casos canónicos declarados sin duplicados.
    """
    if not CASES_FILE_PATH.exists():
        raise FileNotFoundError(f"No se encontró el dataset en: {CASES_FILE_PATH}")

    with open(CASES_FILE_PATH, "r", encoding="utf-8") as f:
        cases = json.load(f)

    if not client.has_dataset(dataset_name=dataset_name):
        print(f"Creando dataset '{dataset_name}' en LangSmith...")
        dataset = client.create_dataset(
            dataset_name=dataset_name,
            description="Casos canónicos de evaluación de mapeo semántico para SourceToGenericCrosswalkGenerator.",
        )
    else:
        print(f"Dataset '{dataset_name}' existente. Sincronizando ejemplos canónicos...")
        dataset = client.read_dataset(dataset_name=dataset_name)
        existing_examples = list(client.list_examples(dataset_id=dataset.id))
        if existing_examples:
            client.delete_examples([e.id for e in existing_examples])

    for caso in cases:
        client.create_example(
            inputs={
                "id_caso": caso["id_caso"],
                "columns": caso["inputs"]["columns"],
                "file_delimiter": caso["inputs"].get("file_delimiter", ","),
                "sample_rows": caso["inputs"].get("sample_rows", []),
            },
            outputs=caso["reference_outputs"],
            dataset_id=dataset.id,
        )

    print(f"✓ {len(cases)} ejemplos sincronizados correctamente en el dataset '{dataset_name}'.")
    return dataset

@traceable(name="source_to_generic_predict")
async def predict(inputs: dict[str, Any]) -> dict[str, Any]:
    """
    Función target de evaluación: ejecuta el agente de mapeo real sobre las muestras del caso.
    """
    columns = inputs["columns"]
    sample_rows = inputs.get("sample_rows", [])
    file_delimiter = inputs.get("file_delimiter", ",")

    # Crear un CSV temporal auténtico para el generador
    tmp_file = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8", newline="")
    try:
        writer = csv.DictWriter(tmp_file, fieldnames=columns, delimiter=file_delimiter)
        writer.writeheader()
        if sample_rows:
            writer.writerows(sample_rows)
        else:
            # Al menos una fila vacía para que no falle el lector
            writer.writerow({c: "" for c in columns})
        tmp_file.flush()
        tmp_csv_path = tmp_file.name
    finally:
        tmp_file.close()

    try:
        generator = SourceToGenericCrosswalkGenerator()
        state = {
            "source_csv_path": tmp_csv_path,
            "source_name": inputs.get("id_caso", "eval_case"),
        }
        context = generator.prepare_context(state)

        # Ejecución asíncrona del bucle LLM en executor propagando contextvars para LangSmith
        ctx = contextvars.copy_context()
        loop = asyncio.get_running_loop()
        raw_mappings = await loop.run_in_executor(
            None, ctx.run, generator.execute_llm_mapping, context, context["columns"]
        )
        validated_mappings = generator.validate_mappings(raw_mappings, context)

        return {
            "mappings": raw_mappings,
            "validated_mappings": validated_mappings,
            "columns": context["columns"],
        }
    finally:
        if os.path.exists(tmp_csv_path):
            os.remove(tmp_csv_path)


async def run_evaluation(experiment_prefix: str = "Source_To_Generic_Mapping_Benchmark") -> Any:
    """Ejecuta la evaluación nativa en LangSmith."""
    client = Client()
    sync_dataset_to_langsmith(client, DATASET_NAME)

    print(f"\nIniciando corrida de evaluación nativa: {experiment_prefix}...")
    results = await aevaluate(
        predict,
        data=DATASET_NAME,
        evaluators=SOURCE_MAPPING_EVALUATORS,
        experiment_prefix=experiment_prefix,
        description="Evaluación de fidelidad y cobertura de mapeo semántico hacia el esquema genérico.",
        max_concurrency=1,  # Serial para respetar rate limits de proveedores de LLM
        client=client,
    )
    print("\n✓ Evaluación completada con éxito.")
    return results


if __name__ == "__main__":
    asyncio.run(run_evaluation())
