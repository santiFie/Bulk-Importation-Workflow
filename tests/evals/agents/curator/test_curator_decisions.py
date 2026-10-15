"""
Evaluación de Toma de Decisiones y Trayectorias del Agente Curador con Pytest.

Módulo: tests/evals/agents/curator/test_curator_decisions.py
Suite de LangSmith: Metadata_Curator_Decision_Trajectories

Carga los casos de prueba declarativos desde `curator_decision_cases.json`
y evalúa el comportamiento del LLM real sin mocks:
  1. Focalización: preservación de campos válidos.
  2. No-alucinación y abstención conservadora ante incertidumbre o fallos.
  3. Reparación efectiva mediante tools cuando el archivo o DOI existe.
  4. Evaluación de llamadas a herramientas (expected_tools y forbidden_tools).
  5. Notas de justificación presentes.
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
    build_metadata_curator_agent,
    construir_mensaje_curacion,
    parsear_respuesta_agente,
)
from core.utils.config import config

CASES_FILE_PATH = (
    Path(__file__).parent.parent.parent / "datasets" / "curator" / "curator_decision_cases.json"
)


def _cargar_casos_decision() -> list[dict[str, Any]]:
    """Carga los 5 casos canónicos de prueba desde el archivo JSON declarativo."""
    if not CASES_FILE_PATH.exists():
        raise FileNotFoundError(f"No se encontró el archivo de casos en: {CASES_FILE_PATH}")
    with open(CASES_FILE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _tiene_api_key_llm() -> bool:
    """Verifica si hay alguna API key configurada para el agente."""
    return bool(config.GROQ_API_KEY or config.NVIDIA_API_KEY or config.OPEN_ROUTER_API_KEY)


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


@pytest.mark.eval
@pytest.mark.langsmith(test_suite_name="Metadata_Curator_Decision_Trajectories")
@pytest.mark.parametrize("caso", _cargar_casos_decision(), ids=lambda c: c["id_caso"])
def test_curator_llm_decisions_on_anomalies(caso: dict[str, Any], agente_curador_real):
    """
    Evalúa la toma de decisiones y el uso de herramientas del LLM real frente a anomalías sin mocks:
      1. Focalización: la corrección se centra en el campo afectado y preserva campos válidos.
      2. No-alucinación y abstención: si el campo es irrecuperable o el DOI no existe, reporta curation_needed.
      3. Reparación efectiva: si hay PDF disponible o DOI real en Crossref, repara el valor con éxito.
      4. Evaluación de Tool Calls: valida que se invoquen las herramientas requeridas sin llamadas prohibidas.
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
