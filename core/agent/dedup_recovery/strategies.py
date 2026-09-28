"""
Implementación del patrón Strategy + NullObject para las herramientas de recuperación.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from core.agent.dedup_recovery.models import StrategyResult

if TYPE_CHECKING:
    from core.agent.dedup_recovery.models import RecoveryDecision


class RecoveryStrategy(ABC):
    """Interfaz base abstracta para todas las estrategias de recuperación de datos."""

    @property
    @abstractmethod
    def is_applicable(self) -> bool:
        """Indica si la estrategia es ejecutable de forma automática sobre el CSV."""
        pass

    @abstractmethod
    def execute(self, csv_path: str, decision: RecoveryDecision) -> StrategyResult:
        """Aplica la transformación sobre el archivo CSV objetivo."""
        pass


class ValueMappingStrategy(RecoveryStrategy):
    """Aplica mapeo directo de valores (reemplazos exactos o por subcadena)."""

    @property
    def is_applicable(self) -> bool:
        return True

    def execute(self, csv_path: str, decision: RecoveryDecision) -> StrategyResult:
        from core.agent.dedup_recovery.tools import apply_value_mapping

        return apply_value_mapping(
            csv_path=csv_path,
            column=decision.target_column,
            mapping=decision.mapping_rules,
        )


class RegexExtractionStrategy(RecoveryStrategy):
    """Aplica extracción de patrones regulares mediante grupos de captura."""

    @property
    def is_applicable(self) -> bool:
        return True

    def execute(self, csv_path: str, decision: RecoveryDecision) -> StrategyResult:
        from core.agent.dedup_recovery.tools import apply_regex_extraction

        return apply_regex_extraction(
            csv_path=csv_path,
            column=decision.target_column,
            pattern=decision.regex_pattern,
            group=decision.regex_group,
        )


class DateNormalizationStrategy(RecoveryStrategy):
    """Aplica el algoritmo canónico de normalización de fechas del sanitizer."""

    @property
    def is_applicable(self) -> bool:
        return True

    def execute(self, csv_path: str, decision: RecoveryDecision) -> StrategyResult:
        from core.agent.dedup_recovery.tools import apply_date_normalization

        return apply_date_normalization(
            csv_path=csv_path,
            column=decision.target_column,
        )


class NullRecoveryStrategy(RecoveryStrategy):
    """
    NullObject Pattern para estrategias desconocidas o revisión manual ('manual_review').
    Evita cheques nulos o sentencias condicionales retornando un resultado inoperante seguro.
    """

    @property
    def is_applicable(self) -> bool:
        return False

    def execute(self, csv_path: str, decision: RecoveryDecision) -> StrategyResult:
        return StrategyResult(
            status="skipped",
            is_applicable=False,
            modified_count=0,
            corrections=[],
            error_message=f"Estrategia '{decision.strategy}' no aplicable automáticamente.",
        )


class StrategyRegistry:
    """Registro y factoría de estrategias de recuperación disponibles."""

    _strategies: ClassVar[dict[str, RecoveryStrategy]] = {
        "value_mapping": ValueMappingStrategy(),
        "regex_extraction": RegexExtractionStrategy(),
        "date_normalization": DateNormalizationStrategy(),
        "manual_review": NullRecoveryStrategy(),
    }
    _null_strategy: ClassVar[RecoveryStrategy] = NullRecoveryStrategy()

    @classmethod
    def get(cls, strategy_name: str) -> RecoveryStrategy:
        """Retorna la estrategia registrada o la NullObject si no existe."""
        return cls._strategies.get(strategy_name, cls._null_strategy)

    @classmethod
    def register(cls, name: str, strategy: RecoveryStrategy) -> None:
        """Permite registrar nuevas estrategias extendiendo el sistema sin modificarlo (OCP)."""
        cls._strategies[name] = strategy
