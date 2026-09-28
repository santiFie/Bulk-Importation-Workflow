"""
Agente de Recuperación Reactiva de Errores de Deduplicación.

Recibe el diagnóstico determinista del error y los casos similares recuperados
desde la Memoria Episódica (Zero-LLM Retrieval) para formular una propuesta
de corrección estructurada.

Decide autónomamente si aplicar la corrección (alta certeza y no ambigüedad)
o solicitar confirmación humana mediante interrupt() de LangGraph.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt
from langsmith import traceable

from core.agent.dedup_recovery.handlers import (
    ActionHandlerRegistry,
    RecoveryContext,
    persist_success_episode,
)
from core.agent.dedup_recovery.models import RecoveryDecision
from core.agent.dedup_recovery.transaction import CsvTransaction
from core.memory.episodic_memory.models import EpisodeQueryResult
from core.memory.episodic_memory.retriever import (
    extract_error_context,
    retrieve_relevant_episodes,
)
from core.utils.config import config
from core.utils.get_local_model import FallbackLLM
from core.utils.prompt_loader import load_agent_prompt

logger = logging.getLogger(__name__)

# Compatibilidad hacia atrás
_persist_success_episode = persist_success_episode


def format_episodes_for_prompt(episodes: list[EpisodeQueryResult]) -> str:
    """Formatea la lista de episodios similares para inyección Few-Shot en el prompt."""
    if not episodes:
        return "(No se encontraron episodios similares previos. Resuelve el caso analizando la causa raíz desde cero.)"

    lines: list[str] = []
    for idx, res in enumerate(episodes, 1):
        ep = res.episode
        lines.append(f"--- Caso Previo #{idx} (Similitud: {res.similarity_score * 100:.1f}%) ---")
        lines.append(f"- Problema: {ep.payload.error_summary}")
        lines.append(f"- Muestra causante: '{ep.payload.culprit_sample}'")
        lines.append(f"- Estrategia aplicada: {ep.payload.transformation_strategy}")
        lines.append(f"- Solución técnica: {ep.payload.solution_applied}")
        lines.append(f"- Justificación: {ep.payload.reasoning}")
        lines.append("")
    return "\n".join(lines).strip()


def build_recovery_system_prompt(episodes_text: str) -> str:
    """Carga y formatea el System Prompt del agente de recuperación desde agent_prompts/."""
    return load_agent_prompt("dedup_recovery_agent", episodes_text=episodes_text)


def invoke_recovery_agent(
    error_msg: str,
    source_name: str,
    csv_path: str,
    episodes: list[EpisodeQueryResult],
    llm: Optional[Any] = None,
) -> RecoveryDecision:
    """
    Invoca al LLM para analizar el error e inferir la decisión de recuperación.
    """
    context = extract_error_context(error_msg, csv_path)
    episodes_text = format_episodes_for_prompt(episodes)
    system_prompt = build_recovery_system_prompt(episodes_text)

    user_content = (
        f"Se produjo un fallo en el Deduplicador para la fuente '{source_name}':\n"
        f"- Mensaje de error: {error_msg}\n"
        f"- Columna identificada: {context['affected_column']}\n"
        f"- Muestras extraídas del error: {context['sample_tokens']}\n"
        f"- Archivo CSV: {csv_path}\n\n"
        f"Analiza la anomalía y emite tu decisión estructurada de recuperación."
    )

    if llm is None:
        model_factory = FallbackLLM(
            groq_model=config.CROSSWALK_MODEL,
            openrouter_model=config.CROSSWALK_MODEL,
        )
        resolved_llm = model_factory.resolve()
    else:
        resolved_llm = llm

    structured_llm = resolved_llm.with_structured_output(RecoveryDecision)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]

    decision = structured_llm.invoke(messages)
    return decision


@traceable(name="DedupRecoveryNode", run_type="chain")
def dedup_recovery_node(state: dict) -> dict[str, Any]:
    """
    Nodo de Recuperación Reactiva — Se ejecuta tras un fallo en Deduplicate.

    Flujo:
      1. Recupera episodios similares de la memoria episódica determinista.
      2. Invoca al agente de recuperación para diagnosticar y proponer una solución.
      3. Si cumple criterios de autorreparación (is_auto_repairable), ejecuta la
         estrategia atómicamente bajo CsvTransaction y valida la integridad.
      4. Si no es autorreparable o la validación falla, delega a HITL vía interrupt()
         y despacha la acción del operador mediante ActionHandlerRegistry.
    """
    error_msg = state.get("node_errors", {}).get("Deduplicate", "")
    if not error_msg:
        error_msg = state.get("dedup_error", "Error desconocido en deduplicación.")

    source_name = state.get("source_name", "unknown")
    csv_path = state.get("generic_source_csv_path", "")
    retry_count = state.get("dedup_retry_count", 0)

    logger.info(
        "[DedupRecoveryNode] Iniciando análisis reactivo (intento %d/1) para '%s'.",
        retry_count + 1,
        source_name,
    )

    # 1. Recuperar memoria episódica
    episodes = retrieve_relevant_episodes(
        tool="deduplicator",
        source_name=source_name,
        error_msg=error_msg,
        csv_path=csv_path,
        top_k=2,
    )

    # 2. Decisión del agente
    decision = invoke_recovery_agent(
        error_msg=error_msg,
        source_name=source_name,
        csv_path=csv_path,
        episodes=episodes,
    )

    logger.info(
        "[DedupRecoveryNode] Decisión: estrategia='%s', confianza='%s', ambigua=%s",
        decision.strategy,
        decision.confidence,
        decision.is_ambiguous,
    )

    # Opción A: El agente cree que lo puede resolver
    if decision.is_auto_repairable:
        with CsvTransaction(csv_path) as tx:
            result = decision.execute(csv_path)
            validation = tx.validate()
            if result.status == "ok" and validation.is_valid:
                tx.commit()
                logger.info("[DedupRecoveryNode] Autorreparación validada exitosamente.")
                persist_success_episode(decision, error_msg, source_name, resolved_by="autonomous")

                new_corrections = list(state.get("applied_corrections", []))
                new_corrections.extend(result.corrections)

                node_errors = dict(state.get("node_errors", {}))
                node_errors.pop("Deduplicate", None)

                return {
                    "dedup_retry_count": retry_count + 1,
                    "applied_corrections": new_corrections,
                    "node_errors": node_errors,
                    "dedup_error": None,
                    "dedup_recovery_diagnosis": decision.model_dump(),
                }

            tx.rollback()
            logger.warning(
                "[DedupRecoveryNode] Falló autorreparación (resultado: %s, validación: %s).",
                result.status,
                validation.errors,
            )

    # Opción B: El agente solicita supervisión humana o hubo un fallo en autorreparación: HITL interrupt
    logger.info("[DedupRecoveryNode] Pausando ejecución para revisión humana (HITL interrupt)...")
    human_response = interrupt({
        "action_required": "dedup_error_review",
        "error_type": "DEDUPLICATION_FAILURE",
        "observation": error_msg,
        "affected_column": decision.target_column,
        "diagnosis": decision.diagnosis,
        "proposed_fix": decision.model_dump(),
        "options": [
            {"id": "accept_proposal", "label": "Aceptar y aplicar propuesta del agente"},
            {"id": "provide_manual_csv", "label": "Proveer ruta de CSV corregido manualmente"},
            {"id": "abort", "label": "Cancelar importación del lote"},
        ],
    })

    action = human_response.get("action", "abort") if isinstance(human_response, dict) else "abort"
    context = RecoveryContext(
        state=state,
        decision=decision,
        error_msg=error_msg,
        source_name=source_name,
        csv_path=csv_path,
        retry_count=retry_count,
    )
    handler = ActionHandlerRegistry.get(action)
    return handler.handle(human_response if isinstance(human_response, dict) else {}, context)
