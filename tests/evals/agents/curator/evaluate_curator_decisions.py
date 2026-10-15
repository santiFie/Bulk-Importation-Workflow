"""
Evaluación Nativa de LangSmith para el MetadataCuratorAgent.

Módulo: tests/evals/agents/curator/evaluate_curator_decisions.py
Dataset en LangSmith: Metadata_Curator_Decision_Cases

Este script ejecuta una evaluación formal y sistemática en LangSmith usando `client.evaluate()`.
A diferencia de los tests con mocks, conecta el agente real con sus tools genuinas (OCR y Crossref).

Ventajas:
  - Dataset canónico de 5 casos sin duplicados.
  - Métricas homogéneas calculadas automáticamente.
  - Comparación visual lado a lado (Side-by-Side) en la UI de LangSmith para prompt tuning.

Uso:
  python tests/evals/agents/curator/evaluate_curator_decisions.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Asegurar path raíz del proyecto
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from langsmith import Client, evaluate, aevaluate

from core.agent.metadata_curator_agent import (
    build_metadata_curator_agent,
    construir_mensaje_curacion,
    parsear_respuesta_agente,
)
from tests.evals.metrics.curator_metrics import CURATOR_EVALUATORS

DATASET_NAME = "Metadata_Curator_Decision_Cases"
CASES_FILE_PATH = (
    PROJECT_ROOT / "tests" / "evals" / "datasets" / "curator" / "curator_decision_cases.json"
)
CURATION_DATA_DIR = PROJECT_ROOT / "tests" / "data" / "ingest" / "curation"

SAMPLE_PDF_PATHS = [
    "/tmp/39-jaiio-ast-03.pdf-PDFA.pdf",
    "/tmp/39-jaiio-ast-06.pdf-PDFA.pdf",
    "/tmp/sample_ocr_paper_sin_autor.pdf",
    "/tmp/sample_ocr_corrupto.pdf",
]


def ensure_sample_pdf_exists() -> None:
    """Copia los PDFs de prueba a /tmp si no existen, necesarios para la tool OCR."""
    import shutil

    for dst_path in SAMPLE_PDF_PATHS:
        fname = os.path.basename(dst_path)
        src_path = CURATION_DATA_DIR / fname
        if not os.path.exists(dst_path) and src_path.exists():
            shutil.copy2(src_path, dst_path)


def sync_dataset_to_langsmith(client: Client, dataset_name: str = DATASET_NAME) -> Any:
    """
    Sincroniza el dataset local de casos de decisión con LangSmith.
    Limpia ejemplos anteriores para garantizar exactamente los 5 casos canónicos sin duplicados.
    """
    if not CASES_FILE_PATH.exists():
        raise FileNotFoundError(f"No se encontró el dataset en: {CASES_FILE_PATH}")

    with open(CASES_FILE_PATH, "r", encoding="utf-8") as f:
        cases = json.load(f)

    if not client.has_dataset(dataset_name=dataset_name):
        print(f"Creando dataset '{dataset_name}' en LangSmith...")
        dataset = client.create_dataset(
            dataset_name=dataset_name,
            description="Casos canónicos de evaluación de toma de decisiones y trayectorias de herramientas para MetadataCuratorAgent.",
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
                "input_fila": caso["input_fila"],
                "campo_afectado": caso["campo_afectado"],
            },
            outputs={
                "campo_intacto": caso["campo_intacto"],
                "valor_intacto_esperado": caso["valor_intacto_esperado"],
                "espera_reparacion": caso.get("espera_reparacion", False),
                "espera_curation_needed": caso.get("espera_curation_needed", False),
                "expected_tools": caso.get("expected_tools", []),
                "forbidden_tools": caso.get("forbidden_tools", []),
            },
            dataset_id=dataset.id,
        )

    print(f"✓ {len(cases)} ejemplos sincronizados correctamente en el dataset '{dataset_name}'.")
    return dataset


async def predict(inputs: dict[str, Any]) -> dict[str, Any]:
    """
    Función target de evaluación: ejecuta el agente curador real sobre la fila de entrada.
    """
    fila_input = inputs["input_fila"]
    campo_afectado = inputs.get("campo_afectado", "title")

    agente = await build_metadata_curator_agent()
    mensaje = construir_mensaje_curacion([fila_input])
    state = {"messages": [mensaje]}

    final_state = await agente.ainvoke(state, config={"recursion_limit": 15})
    ultima_respuesta = final_state["messages"][-1]
    correcciones = parsear_respuesta_agente(ultima_respuesta.content)

    corr = next((c for c in correcciones if c.get("id") == fila_input["id"]), {})

    # Extracción de herramientas ejecutadas a lo largo de los mensajes
    tools_ejecutadas: list[str] = []
    for msg in final_state.get("messages", []):
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                tools_ejecutadas.append(tc.get("name", ""))

    reparado = campo_afectado in corr and corr[campo_afectado] != fila_input.get(campo_afectado)
    marcado_revision = bool(
        corr.get(f"{campo_afectado}_curation_needed", False)
        or corr.get("curation_needed", False)
    )

    return {
        "decision": corr,
        "tools_executed": tools_ejecutadas,
        "correction_notes": corr.get("correction_notes", ""),
        "repaired": reparado,
        "marked_quarantine": marcado_revision,
    }


async def run_evaluation(experiment_prefix: str = "Metadata_Curator_Decision_Agent_Test_Few_Shot") -> Any:
    """Ejecuta la evaluación nativa en LangSmith."""
    ensure_sample_pdf_exists()
    client = Client()
    sync_dataset_to_langsmith(client, DATASET_NAME)

    print(f"\nIniciando corrida de evaluación nativa: {experiment_prefix}...")
    results = await aevaluate(
        predict,
        data=DATASET_NAME,
        evaluators=CURATOR_EVALUATORS,
        experiment_prefix=experiment_prefix,
        description="Evaluación de decisiones conservadoras y llamadas a tools (Usando Few-Shot).",
        max_concurrency=1,  # Serial o acotado para respetar rate limits de proveedores
        client=client,
    )
    print("\n✓ Evaluación completada con éxito.")
    return results


if __name__ == "__main__":
    asyncio.run(run_evaluation())
