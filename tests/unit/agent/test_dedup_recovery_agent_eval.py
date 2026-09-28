"""
Tests unitarios deterministas para el Agente de Recuperación Reactiva y su nodo LangGraph.

Verifica:
  1. Formateo determinista de episodios para Few-Shot en prompts.
  2. Carga y composición del System Prompt y HumanMessage para el LLM.
  3. Plomería e interfaces de invocación con el modelo estructurado.
  4. Lógica de orquestación del nodo LangGraph (dedup_recovery_node):
     - Flujo de autorreparación exitosa (commit transaccional, actualización de estado).
     - Flujo de rollback ante fallo de validación y derivación a HITL.
     - Flujo directo a HITL (interrupt) para decisiones no autorreparables o ambiguas.
     - Despacho de acciones humanas al ActionHandlerRegistry.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from core.agent.dedup_recovery.agent import (
    build_recovery_system_prompt,
    dedup_recovery_node,
    format_episodes_for_prompt,
    invoke_recovery_agent,
)
from core.agent.dedup_recovery.models import RecoveryDecision, StrategyResult
from core.memory.episodic_memory.models import (
    Episode,
    EpisodeMetadata,
    EpisodePayload,
    EpisodeQueryResult,
)


@pytest.fixture
def sample_relevant_episode() -> EpisodeQueryResult:
    """Episodio histórico análogo relevante para pruebas deterministas de formato."""
    ep = Episode(
        id="ep_date_english_relevant",
        metadata=EpisodeMetadata(
            tool="deduplicator",
            source_name="unlp_doaj",
            error_class="ValueError",
            affected_column="date",
            resolved_by="autonomous",
        ),
        embedding_content="Error al comparar fechas: 1999, June — invalid literal for int() with base 10: 'June'",
        payload=EpisodePayload(
            error_summary="Columna date con mes en texto en inglés.",
            culprit_sample="1999, June",
            transformation_strategy="value_mapping",
            solution_applied={"June": "06", "July": "07"},
            reasoning="Se extrae el año y se mapea el mes textual a su representación numérica ISO.",
        ),
    )
    return EpisodeQueryResult(episode=ep, similarity_score=0.92)


# ---------------------------------------------------------------------------
# 1. Formateo de Memoria Episódica
# ---------------------------------------------------------------------------

def test_format_episodes_empty():
    """Valida el mensaje determinista cuando no existen episodios previos."""
    text = format_episodes_for_prompt([])
    assert "No se encontraron episodios similares previos" in text
    assert "desde cero" in text


def test_format_episodes_populated(sample_relevant_episode: EpisodeQueryResult):
    """Valida que los campos del episodio se serialicen correctamente en el texto para el LLM."""
    text = format_episodes_for_prompt([sample_relevant_episode])
    assert "--- Caso Previo #1 (Similitud: 92.0%) ---" in text
    assert "Columna date con mes en texto en inglés." in text
    assert "1999, June" in text
    assert "value_mapping" in text
    assert "{'June': '06', 'July': '07'}" in text
    assert "Se extrae el año" in text


# ---------------------------------------------------------------------------
# 2. Construcción de Prompts
# ---------------------------------------------------------------------------

def test_build_recovery_system_prompt():
    """Valida que la plantilla en disco se cargue y se interpole la sección de episodios."""
    episodes_text = "CASO_TEST_SIMILITUD_99"
    prompt = build_recovery_system_prompt(episodes_text)
    assert episodes_text in prompt
    assert "Deduplicador" in prompt or "deduplicación" in prompt.lower()


# ---------------------------------------------------------------------------
# 3. Plomería e Integración de invoke_recovery_agent
# ---------------------------------------------------------------------------

def test_invoke_recovery_agent_prompt_and_message_structure():
    """
    Verifica que invoke_recovery_agent extraiga el contexto del error determinísticamente
    y configure las llamadas al LLM con los tipos y contenidos exactos esperados.
    """
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured

    fake_decision = RecoveryDecision(
        diagnosis="Diagnóstico de prueba",
        confidence="high",
        is_ambiguous=False,
        strategy="date_normalization",
        target_column="date",
        reasoning="Razón de prueba",
    )
    mock_structured.invoke.return_value = fake_decision

    error_msg = "Error al comparar fechas: 1999, June — invalid literal for int() with base 10: 'June'"
    source_name = "unlp_doaj"
    csv_path = "/tmp/test.csv"

    decision = invoke_recovery_agent(
        error_msg=error_msg,
        source_name=source_name,
        csv_path=csv_path,
        episodes=[],
        llm=mock_llm,
    )

    # 1. Verifica contrato de salida estructurada
    mock_llm.with_structured_output.assert_called_once_with(RecoveryDecision)

    # 2. Verifica estructura de mensajes enviados al LLM
    mock_structured.invoke.assert_called_once()
    call_args = mock_structured.invoke.call_args[0][0]
    assert len(call_args) == 2

    system_msg, human_msg = call_args
    assert "SystemMessage" in type(system_msg).__name__
    assert "HumanMessage" in type(human_msg).__name__

    # 3. Verifica el contenido determinista extraído en el HumanMessage
    human_content = human_msg.content
    assert source_name in human_content
    assert error_msg in human_content
    assert "date" in human_content  # Columna extraída por extract_error_context
    assert csv_path in human_content

    # 4. El objeto retornado debe ser el devuelto por el structured_llm
    assert decision == fake_decision


@patch("core.agent.dedup_recovery.agent.FallbackLLM")
def test_invoke_recovery_agent_resolves_fallback_when_llm_is_none(mock_fallback_class):
    """Verifica que si no se pasa LLM, se inicialice FallbackLLM con la configuración estándar."""
    mock_fallback_instance = MagicMock()
    mock_resolved_llm = MagicMock()
    mock_structured = MagicMock()
    mock_fallback_class.return_value = mock_fallback_instance
    mock_fallback_instance.resolve.return_value = mock_resolved_llm
    mock_resolved_llm.with_structured_output.return_value = mock_structured
    mock_structured.invoke.return_value = RecoveryDecision(
        diagnosis="Diag",
        confidence="high",
        is_ambiguous=False,
        strategy="manual_review",
        reasoning="Reason",
    )

    invoke_recovery_agent(
        error_msg="Error genérico",
        source_name="fuente_prueba",
        csv_path="/tmp/fake.csv",
        episodes=[],
        llm=None,
    )

    mock_fallback_class.assert_called_once()
    mock_fallback_instance.resolve.assert_called_once()


# ---------------------------------------------------------------------------
# 4. Orquestación del Nodo LangGraph (dedup_recovery_node)
# ---------------------------------------------------------------------------

@patch("core.agent.dedup_recovery.agent.persist_success_episode")
@patch("core.agent.dedup_recovery.agent.CsvTransaction")
@patch("core.agent.dedup_recovery.agent.invoke_recovery_agent")
@patch("core.agent.dedup_recovery.agent.retrieve_relevant_episodes")
def test_dedup_recovery_node_auto_repair_success(
    mock_retrieve,
    mock_invoke,
    mock_tx_cls,
    mock_persist,
):
    """
    Verifica el flujo autorreparable exitoso:
      - Ejecuta transacción atómica.
      - Si la validación es exitosa, hace commit y persiste memoria de éxito.
      - Actualiza el estado incrementando reintentos y limpiando errores de deduplicación.
    """
    mock_retrieve.return_value = []
    decision = RecoveryDecision(
        diagnosis="Error de mes corregible",
        confidence="high",
        is_ambiguous=False,
        strategy="value_mapping",
        target_column="date",
        mapping_rules={"June": "06"},
        reasoning="Mapeo simple",
    )
    mock_invoke.return_value = decision

    # Mock de CsvTransaction
    mock_tx_instance = MagicMock()
    mock_tx_cls.return_value.__enter__.return_value = mock_tx_instance
    mock_tx_instance.validate.return_value = MagicMock(is_valid=True)

    state = {
        "node_errors": {"Deduplicate": "Error en fecha"},
        "dedup_error": "Error en fecha",
        "source_name": "scopus",
        "generic_source_csv_path": "/tmp/test.csv",
        "dedup_retry_count": 0,
        "applied_corrections": [{"col": "prev"}],
    }

    correction_dict = {"column": "date", "from": "June", "to": "06"}
    with patch.object(
        RecoveryDecision,
        "execute",
        return_value=StrategyResult(status="ok", modified_count=1, corrections=[correction_dict]),
    ):
        result = dedup_recovery_node(state)

    mock_tx_instance.commit.assert_called_once()
    mock_persist.assert_called_once_with(decision, "Error en fecha", "scopus", resolved_by="autonomous")
    assert result["dedup_retry_count"] == 1
    assert correction_dict in result["applied_corrections"]
    assert "Deduplicate" not in result["node_errors"]
    assert result["dedup_error"] is None
    assert result["dedup_recovery_diagnosis"]["strategy"] == "value_mapping"


@patch("core.agent.dedup_recovery.agent.interrupt")
@patch("core.agent.dedup_recovery.agent.CsvTransaction")
@patch("core.agent.dedup_recovery.agent.invoke_recovery_agent")
@patch("core.agent.dedup_recovery.agent.retrieve_relevant_episodes")
def test_dedup_recovery_node_auto_repair_rollback_on_invalid_validation(
    mock_retrieve,
    mock_invoke,
    mock_tx_cls,
    mock_interrupt,
):
    """
    Verifica que si la autorreparación produce un CSV corrupto o inválido:
      - Se ejecuta rollback de la transacción.
      - Se invoca interrupt() delegando al operador humano.
    """
    mock_retrieve.return_value = []
    decision = RecoveryDecision(
        diagnosis="Error fecha",
        confidence="high",
        is_ambiguous=False,
        strategy="value_mapping",
        target_column="date",
        mapping_rules={"June": "06"},
        reasoning="Mapeo simple",
    )
    mock_invoke.return_value = decision

    mock_tx_instance = MagicMock()
    mock_tx_cls.return_value.__enter__.return_value = mock_tx_instance
    # Validación falla (ej. pérdida de columnas)
    mock_tx_instance.validate.return_value = MagicMock(is_valid=False, errors=["Columnas perdidas"])

    mock_interrupt.return_value = {"action": "abort"}

    state = {
        "node_errors": {"Deduplicate": "Error en fecha"},
        "source_name": "scopus",
        "generic_source_csv_path": "/tmp/test.csv",
        "dedup_retry_count": 0,
    }

    with patch.object(
        RecoveryDecision,
        "execute",
        return_value=StrategyResult(status="ok", modified_count=1),
    ):
        result = dedup_recovery_node(state)

    mock_tx_instance.rollback.assert_called_once()
    mock_tx_instance.commit.assert_not_called()
    mock_interrupt.assert_called_once()
    assert result.get("node_errors", {}).get("Deduplicate") is not None


@patch("core.agent.dedup_recovery.agent.ActionHandlerRegistry")
@patch("core.agent.dedup_recovery.agent.interrupt")
@patch("core.agent.dedup_recovery.agent.CsvTransaction")
@patch("core.agent.dedup_recovery.agent.invoke_recovery_agent")
@patch("core.agent.dedup_recovery.agent.retrieve_relevant_episodes")
def test_dedup_recovery_node_non_autorepairable_triggers_direct_interrupt(
    mock_retrieve,
    mock_invoke,
    mock_tx_cls,
    mock_interrupt,
    mock_registry_cls,
):
    """
    Verifica que ante una decisión ambigua o manual_review:
      - NO intente autorreparar ni abrir transacciones sobre el CSV.
      - Emita inmediatamente interrupt() con la información estructurada.
      - Despache la acción resultante al manejador de ActionHandlerRegistry.
    """
    mock_retrieve.return_value = []
    decision = RecoveryDecision(
        diagnosis="Conflicto de años ambiguo",
        confidence="low",
        is_ambiguous=True,
        strategy="manual_review",
        target_column="date",
        reasoning="Requiere juicio humano",
    )
    mock_invoke.return_value = decision

    mock_interrupt.return_value = {"action": "abort"}
    mock_handler = MagicMock()
    mock_handler.handle.return_value = {"aborted": True}
    mock_registry_cls.get.return_value = mock_handler

    state = {
        "node_errors": {"Deduplicate": "Fallo ambiguo"},
        "source_name": "crossref",
        "generic_source_csv_path": "/tmp/test.csv",
        "dedup_retry_count": 0,
    }

    res = dedup_recovery_node(state)

    # No debe abrir transacción
    mock_tx_cls.assert_not_called()

    # Debe pausar vía interrupt
    mock_interrupt.assert_called_once()
    interrupt_payload = mock_interrupt.call_args[0][0]
    assert interrupt_payload["action_required"] == "dedup_error_review"
    assert interrupt_payload["affected_column"] == "date"
    assert interrupt_payload["diagnosis"] == "Conflicto de años ambiguo"

    # Debe despachar al handler 'abort'
    mock_registry_cls.get.assert_called_once_with("abort")
    assert res == {"aborted": True}
