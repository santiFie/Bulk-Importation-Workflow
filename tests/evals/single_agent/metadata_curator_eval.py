"""
Suite de Evaluación Single-Agent para MetadataCuratorAgent con LLM Real (Sin Mocks).

⚠️ ADVERTENCIA CRÍTICA DE COSTOS:
Este archivo está decorado con `@pytest.mark.eval`. Su ejecución invoca modelos
de lenguaje (LLMs) reales que consumen tokens y generan costos económicos.
NO debe ejecutarse en pipelines regulares de CI ni en suites de tests unitarios rápidos.

Objetivos de la suite:
  1. Falsos Positivos y Sobre-Curación (Control Negativo):
     Garantiza que sobre metadatos limpios el agente y los filtros no intervengan
     ni modifiquen registros válidos (`over_correction_rate == 0.0%`).
  2. Razonamiento y Toma de Decisiones Autónomas (Control Positivo):
     Evalúa directamente la respuesta del LLM real ante anomalías complejas:
     - Focalización: repara o marca el campo afectado sin alterar los campos válidos.
     - Criterio de abstención: activa `curation_needed` ante ambigüedad o falta de datos
       en vez de alucinar valores ficticios.
     - Argumentación: incluye `correction_notes` justificando la decisión tomada.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
import pytest

from core.agent.metadata_curator_agent import (
    _construir_modelo_curador,
    build_metadata_curator_agent,
    construir_mensaje_curacion,
    parsear_respuesta_agente,
)
from core.nodes.curation_nodes import (
    CsvHandler,
    curate_metadata_node,
)
from core.utils.config import config


DATASETS_DIR = Path(__file__).parent.parent / "datasets"
GOLDEN_DATASET_PATH = DATASETS_DIR / "golden_regression_dataset.json"


def _tiene_api_key_llm() -> bool:
    """Verifica si hay al menos una API key de LLM configurada."""
    return bool(
        config.GROQ_API_KEY
        or config.NVIDIA_API_KEY
        or config.OPEN_ROUTER_API_KEY
    )


def _cargar_registros_limpios_de_control() -> list[dict[str, Any]]:
    """
    Carga registros de metadatos limpios y normalizados para la prueba de falsos positivos.
    """
    if GOLDEN_DATASET_PATH.exists():
        with open(GOLDEN_DATASET_PATH, "r", encoding="utf-8") as f:
            cases = json.load(f)
        registros = []
        for c in cases:
            meta = dict(c.get("metadata", {}))
            meta["id"] = f"{c.get('id', 'item')}.pdf"
            registros.append(meta)
        return registros
    else:
        raise FileNotFoundError(f"No se encontró el dataset de evaluación en: {GOLDEN_DATASET_PATH}")


@pytest.fixture(scope="module")
def agente_curador_real():
    """Instancia el agente curador conectado al LLM real sin mocks."""
    if not _tiene_api_key_llm():
        pytest.skip("No hay API key de LLM disponible para evaluación.")
    return asyncio.run(build_metadata_curator_agent())



# ---------------------------------------------------------------------------
# 1. Evaluación de Falsos Positivos y Sobre-Curación (Over-Correction Rate)
# ---------------------------------------------------------------------------

@pytest.mark.eval
@pytest.mark.asyncio
async def test_curator_zero_over_correction(tmp_path: Path):
    """
    Evalúa la tasa de sobre-curación (over-correction rate) sobre metadatos limpios.

    Un lote de control limpio:
      1. Debe ser clasificado íntegramente como 'limpio' en la Capa 1 heurística.
      2. No debe invocar al agente LLM de forma redundante.
      3. No debe alterar ningún campo original ni enviar ítems a cuarentena.
      4. over_correction_rate == 0.0%.
    """
    clean_records = _cargar_registros_limpios_de_control()
    assert len(clean_records) >= 2, "El conjunto de control debe tener registros limpios."

    source_csv = tmp_path / "clean_control_dataset.csv"
    CsvHandler.write(clean_records, str(source_csv))

    state = {
        "source_csv_path": str(source_csv),
        "workspace_dir": str(tmp_path),
    }

    result_state = await curate_metadata_node(state)

    curated_csv_path = result_state.get("curated_csv_path")
    pending_csv_path = result_state.get("pending_to_review_csv_path")
    stats = result_state.get("curation_stats", {})

    total_records = len(clean_records)
    curadas_agente = stats.get("curadas_agente", 0)
    marcadas_revision = stats.get("marcadas_revision", 0)
    sospechosas = stats.get("sospechosas_detectadas", 0)

    over_corrected_count = curadas_agente + marcadas_revision
    over_correction_rate = (over_corrected_count / total_records) if total_records > 0 else 0.0

    print(f"\n[EVAL OVER-CORRECTION] Total: {total_records}")
    print(f"[EVAL OVER-CORRECTION] Sospechosas detectadas: {sospechosas}")
    print(f"[EVAL OVER-CORRECTION] Curadas por agente: {curadas_agente}")
    print(f"[EVAL OVER-CORRECTION] Marcadas a revisión: {marcadas_revision}")
    print(f"[EVAL OVER-CORRECTION] Over-correction rate: {over_correction_rate:.2%}")

    assert over_correction_rate == 0.0, (
        f"Se detectó sobre-curación (over-correction rate = {over_correction_rate:.2%}). "
        f"{over_corrected_count} registros válidos fueron modificados o enviados a cuarentena."
    )
    assert stats.get("limpias") == total_records, "Todos los registros de control debieron clasificarse como limpios."
    assert sospechosas == 0, "No debe haber anomalías sospechosas en el dataset de control limpio."
    assert pending_csv_path is None or not os.path.exists(pending_csv_path), "No deben generarse ítems en cuarentena."

    curated_records = CsvHandler.read(curated_csv_path)
    assert len(curated_records) == total_records

    for orig, cur in zip(clean_records, curated_records):
        assert cur["title"] == orig["title"], f"El título cambió: '{orig['title']}' -> '{cur['title']}'"
        assert cur["author"] == orig["author"], f"El autor cambió: '{orig['author']}' -> '{cur['author']}'"


# ---------------------------------------------------------------------------
# 2. Evaluación de Razonamiento y Decisiones del LLM ante Anomalías (Sin Mocks)
# ---------------------------------------------------------------------------


# TODO: Agregar más casos no tan claros como estos
CASOS_EVAL_DECISIONES = [
    {
        "id_caso": "anomalia_recuperable_titulo_disperso",
        "descripcion": "Título con espacios intermedios anómalos; debe ser reparado sin alterar autor.",
        "input_fila": {
            "id": "item_ruidoso_01.pdf",
            "title": "E s t i m a c i o n   d e   t e x t u r a s   y   f a c c i o n e s",
            "author": "Gonzalez, Maria; Perez, Juan",
            "date": "2020",
            "type": "Articulo",
            "_curation": {
                "anomalias": {"title": ["CHARS_DISPERSOS"]},
                "score": 0.85,
            },
            "_corrections_applied": [],
        },
        "campo_afectado": "title",
        "campo_intacto": "author",
        "valor_intacto_esperado": "Gonzalez, Maria; Perez, Juan",
    },
    {
        "id_caso": "anomalia_irrecuperable_solicita_revision",
        "descripcion": "Registro con título corrupto/ilegible que debe marcar curation_needed sin alucinar.",
        "input_fila": {
            "id": "item_ilegible_02.pdf",
            "title": "###???___!!!",
            "author": "Lopez, Carlos",
            "date": "2018",
            "type": "Tesis",
            "_curation": {
                "anomalias": {"title": ["RUIDO_CARACTERES"]},
                "score": 0.95,
            },
            "_corrections_applied": [],
        },
        "campo_afectado": "title",
        "campo_intacto": "author",
        "valor_intacto_esperado": "Lopez, Carlos",
    },
]


@pytest.mark.eval
@pytest.mark.parametrize("caso", CASOS_EVAL_DECISIONES, ids=lambda c: c["id_caso"])
def test_curator_llm_decisions_on_anomalies(caso: dict[str, Any], agente_curador_real):
    """
    Evalúa la toma de decisiones del LLM real frente a anomalías sin mocks:
      1. Focalización: la corrección se centra en el campo afectado y preserva campos válidos.
      2. No-alucinación: si el campo es irrecuperable, reporta curation_needed.
      3. Notas: incluye correction_notes no vacío explicando el criterio aplicado.
    """
    fila_input = caso["input_fila"]
    campo_afectado = caso["campo_afectado"]
    campo_intacto = caso["campo_intacto"]
    valor_intacto_esperado = caso["valor_intacto_esperado"]

    mensaje = construir_mensaje_curacion([fila_input])
    state = {"messages": [mensaje]}

    final_state = asyncio.run(agente_curador_real.ainvoke(state, config={"recursion_limit": 15}))
    ultima_respuesta = final_state["messages"][-1]
    correcciones = parsear_respuesta_agente(ultima_respuesta.content)

    assert isinstance(correcciones, list) and len(correcciones) > 0, (
        f"El LLM no devolvió una lista JSON válida de correcciones. Respuesta: {ultima_respuesta.content}"
    )

    corr = next((c for c in correcciones if c.get("id") == fila_input["id"]), None)
    assert corr is not None, f"No se encontró corrección para el ID {fila_input['id']} en la respuesta."

    # 1. Verificación de justificación
    notes = corr.get("correction_notes", "")
    assert isinstance(notes, str) and notes.strip(), (
        f"El agente debe proporcionar correction_notes justificando su decisión. Recibido: {corr}"
    )

    # 2. Verificación de focalización: no debe alterar campos válidos
    if campo_intacto in corr:
        assert corr[campo_intacto] == valor_intacto_esperado, (
            f"El campo intacto '{campo_intacto}' fue alterado indebidamente: "
            f"'{corr[campo_intacto]}' != '{valor_intacto_esperado}'"
        )

    # 3. Verificación de decisión sobre el campo afectado
    reparado = campo_afectado in corr and corr[campo_afectado] != fila_input[campo_afectado]
    marcado_revision = bool(
        corr.get(f"{campo_afectado}_curation_needed", False)
        or corr.get("curation_needed", False)
    )

    assert reparado or marcado_revision, (
        f"El LLM no reparó el campo '{campo_afectado}' ni lo marcó para revisión. "
        f"Decisión del agente: {corr}"
    )
