"""
Manejadores desacoplados de acciones Human-in-the-Loop (HITL) para la recuperación.
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from core.agent.dedup_recovery.models import RecoveryDecision
from core.memory.episodic_memory.models import (
    Episode,
    EpisodeMetadata,
    EpisodePayload,
)
from core.memory.episodic_memory.store import EpisodicMemoryStore

logger = logging.getLogger(__name__)


def persist_success_episode(
    decision: RecoveryDecision,
    error_msg: str,
    source_name: str,
    resolved_by: Literal["autonomous", "hitl"],
) -> None:
    """Registra la resolución exitosa en la base vectorial de memoria episódica."""
    try:
        store = EpisodicMemoryStore()
        ep_id = f"ep_{uuid.uuid4().hex[:8]}"
        metadata = EpisodeMetadata(
            tool="deduplicator",
            source_name=source_name,
            error_class="ValueError",
            affected_column=decision.target_column,
            resolved_by=resolved_by,
        )
        payload = EpisodePayload(
            error_summary=decision.diagnosis,
            transformation_strategy=decision.strategy,
            solution_applied={
                "mapping_rules": decision.mapping_rules,
                "regex_pattern": decision.regex_pattern,
            },
            reasoning=decision.reasoning,
        )
        episode = Episode(
            id=ep_id,
            metadata=metadata,
            embedding_content=f"Tool: deduplicator. Error: {error_msg}. Column: {decision.target_column}",
            payload=payload,
        )
        store.save_episode(episode)
    except Exception as exc:
        logger.warning("[DedupRecovery] No se pudo guardar episodio en memoria episódica: %s", exc)


@dataclass
class RecoveryContext:
    """Contexto operativo provisto a los manejadores de respuestas HITL."""

    state: dict[str, Any]
    decision: RecoveryDecision
    error_msg: str
    source_name: str
    csv_path: str
    retry_count: int


class HumanActionHandler(ABC):
    """Interfaz abstracta (Command / Handler) para procesar respuestas de interrupt()."""

    @abstractmethod
    def handle(self, response: dict[str, Any], context: RecoveryContext) -> dict[str, Any]:
        """Procesa la respuesta humana y retorna el delta de actualización del estado."""
        pass


class AcceptProposalHandler(HumanActionHandler):
    """Aplica la propuesta formulada por el agente bajo autorización humana explícita."""

    def handle(self, response: dict[str, Any], context: RecoveryContext) -> dict[str, Any]:
        logger.info("[AcceptProposalHandler] Aplicando propuesta del agente aprobada por operador.")
        result = context.decision.execute(context.csv_path)
        persist_success_episode(
            context.decision, context.error_msg, context.source_name, resolved_by="hitl"
        )

        new_corrections = list(context.state.get("applied_corrections", []))
        new_corrections.extend(result.corrections)

        node_errors = dict(context.state.get("node_errors", {}))
        node_errors.pop("Deduplicate", None)

        return {
            "dedup_retry_count": context.retry_count + 1,
            "applied_corrections": new_corrections,
            "node_errors": node_errors,
            "dedup_error": None,
            "dedup_recovery_diagnosis": context.decision.model_dump(),
        }


class ProvideManualCsvHandler(HumanActionHandler):
    """Sustituye el CSV con una versión corregida externamente por el operador."""

    def handle(self, response: dict[str, Any], context: RecoveryContext) -> dict[str, Any]:
        manual_csv = response.get("csv_path", "")
        if not manual_csv or not os.path.isfile(manual_csv):
            raise FileNotFoundError(f"El CSV provisto por el operador no existe: {manual_csv}")

        shutil.copy(manual_csv, context.csv_path)
        logger.info("[ProvideManualCsvHandler] CSV manual inyectado exitosamente desde '%s'", manual_csv)
        persist_success_episode(
            context.decision, context.error_msg, context.source_name, resolved_by="hitl"
        )

        node_errors = dict(context.state.get("node_errors", {}))
        node_errors.pop("Deduplicate", None)

        return {
            "dedup_retry_count": context.retry_count + 1,
            "node_errors": node_errors,
            "dedup_error": None,
            "dedup_recovery_diagnosis": {"manual_csv_provided": manual_csv},
        }


class AbortActionHandler(HumanActionHandler):
    """Cancela el lote y aborta la ejecución del pipeline."""

    def handle(self, response: dict[str, Any], context: RecoveryContext) -> dict[str, Any]:
        logger.warning("[AbortActionHandler] Importación abortada por decisión humana tras error.")
        state_errors = dict(context.state.get("node_errors", {}))
        state_errors["Deduplicate"] = f"Cancelado por operador humano tras error: {context.error_msg}"
        return {
            "pipeline_status": "failed",
            "node_errors": state_errors,
        }


class ActionHandlerRegistry:
    """Registro y despachador de manejadores de acciones HITL."""

    _handlers: ClassVar[dict[str, HumanActionHandler]] = {
        "accept_proposal": AcceptProposalHandler(),
        "provide_manual_csv": ProvideManualCsvHandler(),
        "abort": AbortActionHandler(),
    }
    _default_handler: ClassVar[HumanActionHandler] = AbortActionHandler()

    @classmethod
    def get(cls, action: str) -> HumanActionHandler:
        """Obtiene el manejador correspondiente a la acción o el de abortar si no coincide."""
        return cls._handlers.get(action, cls._default_handler)
