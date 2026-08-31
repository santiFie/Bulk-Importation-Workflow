"""
Script de evaluación formal en LangSmith para el MetadataCuratorAgent.

Sincroniza el dataset local `tests/data/curation_dataset.json` con LangSmith
y ejecuta una evaluación automatizada, mockeando las tools para determinismo.
"""

import os
import json
import asyncio
from pathlib import Path
from unittest.mock import patch

from langsmith import Client, evaluate
from core.agent.metadata_curator_agent import build_metadata_curator_agent, construir_mensaje_curacion, parsear_respuesta_agente

DATASET_NAME = "Metadata_Curator_Evaluation"
DATASET_PATH = Path(__file__).parent.parent / "tests" / "data" / "curation_dataset.json"

def sync_dataset_to_langsmith(client: Client, dataset_name: str) -> bool:
    """Crea o actualiza el dataset en LangSmith desde el JSON local."""
    if not DATASET_PATH.exists():
        print(f"No se encontró el dataset en {DATASET_PATH}")
        return False
        
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        local_cases = json.load(f)

    if not client.has_dataset(dataset_name=dataset_name):
        print(f"Creando dataset '{dataset_name}' en LangSmith...")
        dataset = client.create_dataset(dataset_name=dataset_name)
    else:
        print(f"Dataset '{dataset_name}' ya existe. Sincronizando ejemplos...")
        dataset = client.read_dataset(dataset_name=dataset_name)
        # Limpiar ejemplos existentes para evitar duplicados en la sincronización
        examples = list(client.list_examples(dataset_id=dataset.id))
        if examples:
            client.delete_examples([e.id for e in examples])

    for caso in local_cases:
        client.create_example(
            inputs={"input": caso["input"], "mock_tools": caso.get("mock_tools", {})},
            outputs={"expected_output": caso["expected_output"]},
            dataset_id=dataset.id,
        )
    return True


async def predict(inputs: dict) -> dict:
    """Ejecuta el agente curador mockeando las tools especificadas."""
    fila_input = inputs["input"]
    mock_tools = inputs.get("mock_tools", {})

    agente = await build_metadata_curator_agent()
    mensaje = construir_mensaje_curacion([fila_input])
    state = {"messages": [mensaje]}

    ocr_mock = mock_tools.get("re_extract_with_ocr")
    enricher_mock = mock_tools.get("validate_with_enrichers")

    import contextlib
    with patch.object(
        __import__("core.agent.metadata_curator_agent", fromlist=["re_extract_with_ocr"]).re_extract_with_ocr, 
        "func", 
        side_effect=lambda **kwargs: ocr_mock
    ) if ocr_mock else contextlib.nullcontext(), \
         patch.object(
        __import__("core.agent.metadata_curator_agent", fromlist=["validate_with_enrichers"]).validate_with_enrichers, 
        "func", 
        side_effect=lambda **kwargs: enricher_mock
    ) if enricher_mock else contextlib.nullcontext():

        final_state = await agente.ainvoke(state, config={"recursion_limit": 15})
        ultima_respuesta = final_state["messages"][-1]
        correcciones = parsear_respuesta_agente(ultima_respuesta.content)
        
        # Devolver solo la corrección correspondiente al input actual
        id_buscado = fila_input.get("id")
        correccion = next((c for c in correcciones if c.get("id") == id_buscado), {})
        return {"correccion": correccion}


def exact_match_evaluator(run, example) -> dict:
    """Evalúa si la corrección del agente coincide exactamente con los valores esperados."""
    expected = example.outputs["expected_output"]
    actual = run.outputs["correccion"]

    if not actual:
        return {"key": "exact_match", "score": 0, "comment": "Agente no devolvió corrección"}

    score = 1
    errores = []
    for k, v_expected in expected.items():
        v_actual = actual.get(k)
        if v_actual != v_expected:
            score = 0
            errores.append(f"{k}: esperado '{v_expected}', obtenido '{v_actual}'")
            
    comment = "Coincidencia perfecta" if score == 1 else "Errores: " + "; ".join(errores)
    return {"key": "exact_match", "score": score, "comment": comment}


def main():
    if not os.environ.get("LANGSMITH_API_KEY"):
        print("Error: LANGSMITH_API_KEY no está configurada.")
        return

    client = Client()
    print("Sincronizando dataset...")
    if not sync_dataset_to_langsmith(client, DATASET_NAME):
        return

    print(f"Lanzando evaluación en dataset '{DATASET_NAME}'...")
    
    # LangSmith evaluate bloquea, pero nuestro predict es async. 
    # Podemos envolver predict en una función sincrónica
    def sync_predict(inputs: dict) -> dict:
        return asyncio.run(predict(inputs))

    results = evaluate(
        sync_predict,
        data=DATASET_NAME,
        evaluators=[exact_match_evaluator],
        experiment_prefix="MetadataCurator-Eval",
    )
    print("Evaluación iniciada. Revisa el dashboard de LangSmith para ver los resultados.")

if __name__ == "__main__":
    main()
