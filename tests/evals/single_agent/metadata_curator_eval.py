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
import contextlib
import json
import os
from pathlib import Path
from typing import Any
import pytest
from langsmith import testing as t

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


@pytest.fixture(scope="module", autouse=True)
def sample_pdf_ocr_fixture():
    """
    Genera un archivo PDF sintético en /tmp/sample_ruidoso_ocr.pdf para que
    la tool `re_extract_with_ocr` pueda ser evaluada de extremo a extremo sin mocks.
    """
    tmp_pdf_path = "/tmp/sample_ruidoso_ocr.pdf"
    try:
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text(
            (50, 72),
            "Estimación de Texturas y Facciones en Imágenes Médicas\n"
            "Gonzalez, Maria; Perez, Juan\n"
            "Facultad de Ingeniería, Universidad Nacional de La Plata\n"
            "Resumen: Este trabajo analiza técnicas de estimación de texturas..."
        )
        doc.save(tmp_pdf_path)
        doc.close()
    except Exception:
        pass
    yield tmp_pdf_path
    if os.path.exists(tmp_pdf_path):
        with contextlib.suppress(OSError):
            os.remove(tmp_pdf_path)


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
@pytest.mark.langsmith(test_suite_name="Metadata_Curator_Agent_Evaluation")
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

    t.log_inputs({
        "dataset": "golden_regression_dataset.json (clean subset)",
        "total_records": len(clean_records),
    })
    t.log_reference_outputs({
        "expected_over_correction_rate": 0.0,
        "expected_suspicious": 0,
    })

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

    t.log_outputs({
        "over_correction_rate": over_correction_rate,
        "curated_by_agent": curadas_agente,
        "quarantined": marcadas_revision,
        "suspicious_detected": sospechosas,
    })
    t.log_feedback(key="over_correction_rate", score=over_correction_rate)
    t.log_feedback(key="zero_over_correction_pass", score=1.0 if over_correction_rate == 0.0 else 0.0)

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


CASOS_EVAL_DECISIONES = [
    {
        "id_caso": "anomalia_recuperable_ocr_pdf_real",
        "descripcion": "Título con espacios intermedios y archivo PDF disponible; el agente debe ejecutar re_extract_with_ocr y reparar el título.",
        "input_fila": {
            "id": "sample_ruidoso_ocr.pdf",
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
        "espera_reparacion": True,
        "expected_tools": ["re_extract_with_ocr"],
    },
    {
        "id_caso": "anomalia_recuperable_doi_real_crossref",
        "descripcion": "Registro con título disperso y DOI real oficial; el agente debe validar/completar vía Crossref con validate_with_enrichers.",
        "input_fila": {
            "id": "item_doi_real_03.pdf",
            "title": "D e e p   i m p u t a t i o n   o f   m i s s i n g   v a l u e s",
            "author": "Kazijevs, Maksims; Samad, Md Delwar",
            "doi": "10.1016/j.jbi.2023.104440",
            "date": "2023",
            "type": "Articulo",
            "_curation": {
                "anomalias": {"title": ["CHARS_DISPERSOS"]},
                "score": 0.85,
            },
            "_corrections_applied": [],
        },
        "campo_afectado": "title",
        "campo_intacto": "doi",
        "valor_intacto_esperado": "10.1016/j.jbi.2023.104440",
        "espera_reparacion": True,
        "expected_tools": ["validate_with_enrichers"],
    },
    {
        "id_caso": "anomalia_irrecuperable_doi_inexistente_abstencion",
        "descripcion": "Registro con título corrupto y DOI inexistente (404); el agente debe consultar Crossref, no inventar y abstenerse a cuarentena.",
        "input_fila": {
            "id": "item_doi_404_04.pdf",
            "title": "Documento con título irreconocible ###@@@",
            "author": "Autor Desconocido",
            "doi": "10.9999/nonexistent.doi.404",
            "date": "2021",
            "type": "Articulo",
            "_curation": {
                "anomalias": {"title": ["RUIDO_CARACTERES"]},
                "score": 0.90,
            },
            "_corrections_applied": [],
        },
        "campo_afectado": "title",
        "campo_intacto": "date",
        "valor_intacto_esperado": "2021",
        "espera_curation_needed": True,
        "expected_tools": ["validate_with_enrichers"],
    },
    {
        "id_caso": "anomalia_irrecuperable_ocr_pdf_inexistente_resiliencia",
        "descripcion": "Registro con título corrupto e ID .pdf pero archivo inexistente; el agente intenta OCR, recibe error y se abstiene a cuarentena sin llamar a enrichers.",
        "input_fila": {
            "id": "item_ruidoso_inexistente_02.pdf",
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
        "espera_curation_needed": True,
        "expected_tools": ["re_extract_with_ocr"],
        "forbidden_tools": ["validate_with_enrichers"],
    },
    {
        "id_caso": "anomalia_irrecuperable_sin_archivo_cero_tools",
        "descripcion": "Registro tabular/CSV sin archivo PDF ni identificadores; el agente se abstiene inmediatamente con cero llamadas a herramientas.",
        "input_fila": {
            "id": "item_csv_registro_05",
            "title": "###???___!!!",
            "author": "Fernandez, Lucia",
            "date": "2019",
            "type": "Tesis",
            "_curation": {
                "anomalias": {"title": ["RUIDO_CARACTERES"]},
                "score": 0.95,
            },
            "_corrections_applied": [],
        },
        "campo_afectado": "title",
        "campo_intacto": "author",
        "valor_intacto_esperado": "Fernandez, Lucia",
        "espera_curation_needed": True,
        "expected_tools": [],
        "forbidden_tools": ["re_extract_with_ocr", "validate_with_enrichers"],
    },
]


@pytest.mark.eval
@pytest.mark.langsmith(test_suite_name="Metadata_Curator_Agent_Evaluation")
@pytest.mark.parametrize("caso", CASOS_EVAL_DECISIONES, ids=lambda c: c["id_caso"])
def test_curator_llm_decisions_on_anomalies(caso: dict[str, Any], agente_curador_real):
    """
    Evalúa la toma de decisiones y el uso de herramientas del LLM real frente a anomalías sin mocks:
      1. Focalización: la corrección se centra en el campo afectado y preserva campos válidos.
      2. No-alucinación y abstención: si el campo es irrecuperable o el DOI no existe, reporta curation_needed.
      3. Reparación efectiva: si hay PDF disponible o DOI real en Crossref, repara el valor con éxito.
      4. Evaluación de Tool Calls: valida que se invoquen las herramientas requeridas sin llamadas espurias.
      5. Notas: incluye correction_notes no vacío explicando el criterio aplicado.
    """
    fila_input = caso["input_fila"]
    campo_afectado = caso["campo_afectado"]
    campo_intacto = caso["campo_intacto"]
    valor_intacto_esperado = caso["valor_intacto_esperado"]
    expected_tools = caso.get("expected_tools", [])
    forbidden_tools = caso.get("forbidden_tools", [])

    t.log_inputs({
        "id_caso": caso["id_caso"],
        "input_fila": fila_input,
        "campo_afectado": campo_afectado,
    })
    t.log_reference_outputs({
        "campo_intacto": campo_intacto,
        "valor_intacto_esperado": valor_intacto_esperado,
        "expected_action": "repair" if caso.get("espera_reparacion") else "quarantine",
        "expected_tools": expected_tools,
        "forbidden_tools": forbidden_tools,
    })

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

    # 1. Extracción e inspección de tool_calls ejecutadas
    tools_ejecutadas: list[str] = []
    for msg in final_state.get("messages", []):
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                tools_ejecutadas.append(tc.get("name", ""))

    if not expected_tools:
        tool_accuracy = 1.0 if len(tools_ejecutadas) == 0 else 0.0
    else:
        has_expected = all(t in tools_ejecutadas for t in expected_tools)
        has_no_forbidden = all(f not in tools_ejecutadas for f in forbidden_tools)
        tool_accuracy = 1.0 if (has_expected and has_no_forbidden) else 0.0

    # 2. Verificación de justificación
    notes = corr.get("correction_notes", "")
    has_notes = bool(isinstance(notes, str) and notes.strip())

    # 3. Verificación de focalización: no debe alterar campos válidos
    field_preserved = True
    if campo_intacto in corr:
        field_preserved = corr[campo_intacto] == valor_intacto_esperado

    # 4. Verificación de decisión sobre el campo afectado
    reparado = campo_afectado in corr and corr[campo_afectado] != fila_input[campo_afectado]
    marcado_revision = bool(
        corr.get(f"{campo_afectado}_curation_needed", False)
        or corr.get("curation_needed", False)
    )
    action_taken = reparado or marcado_revision

    # Registrar outputs y métricas de feedback en LangSmith
    t.log_outputs({
        "decision": corr,
        "correction_notes": notes,
        "repaired": reparado,
        "marked_quarantine": marcado_revision,
        "tools_executed": tools_ejecutadas,
    })
    t.log_feedback(key="preserves_intact_fields", score=1.0 if field_preserved else 0.0)
    t.log_feedback(key="action_taken_accuracy", score=1.0 if action_taken else 0.0)
    t.log_feedback(key="tool_selection_accuracy", score=tool_accuracy)
    t.log_feedback(key="tool_calls_count", score=len(tools_ejecutadas))
    t.log_feedback(key="has_correction_notes", score=1.0 if has_notes else 0.0)

    # Aserciones locales de Pytest
    assert has_notes, (
        f"El agente debe proporcionar correction_notes justificando su decisión. Recibido: {corr}"
    )
    assert field_preserved, (
        f"El campo intacto '{campo_intacto}' fue alterado indebidamente: "
        f"'{corr.get(campo_intacto)}' != '{valor_intacto_esperado}'"
    )
    assert action_taken, (
        f"El LLM no reparó el campo '{campo_afectado}' ni lo marcó para revisión. "
        f"Decisión del agente: {corr}"
    )

    # Aserciones estrictas sobre el uso de herramientas (Tool-Call Evaluation)
    if expected_tools:
        for t_exp in expected_tools:
            assert t_exp in tools_ejecutadas, (
                f"El caso '{caso['id_caso']}' debía invocar la tool '{t_exp}'. "
                f"Tools ejecutadas: {tools_ejecutadas}"
            )
    else:
        assert len(tools_ejecutadas) == 0, (
            f"El caso '{caso['id_caso']}' no debía ejecutar ninguna herramienta. "
            f"Tools ejecutadas: {tools_ejecutadas}"
        )

    for f_tool in forbidden_tools:
        assert f_tool not in tools_ejecutadas, (
            f"El caso '{caso['id_caso']}' ejecutó la tool prohibida '{f_tool}'. "
            f"Tools ejecutadas: {tools_ejecutadas}"
        )

    # Aserciones estrictas sobre la decisión requerida (Contratos separados)
    if caso.get("espera_reparacion"):
        assert reparado is True, (
            f"El caso '{caso['id_caso']}' debía ser reparado con éxito pero fue marcado a revisión. "
            f"Decisión del agente: {corr}"
        )
        assert not marcado_revision, (
            f"El caso '{caso['id_caso']}' no debía ser enviado a cuarentena. "
            f"Decisión del agente: {corr}"
        )

    if caso.get("espera_curation_needed"):
        assert marcado_revision is True, (
            f"El caso '{caso['id_caso']}' debía ser marcado a revisión/cuarentena sin alucinar. "
            f"Decisión del agente: {corr}"
        )
        assert not reparado, (
            f"El caso '{caso['id_caso']}' no debía ser reparado con datos inventados. "
            f"Decisión del agente: {corr}"
        )

