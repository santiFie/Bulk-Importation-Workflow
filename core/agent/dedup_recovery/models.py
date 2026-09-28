"""
Modelos de dominio y resultados tipados para la Recuperación de Errores de Deduplicación.
"""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class StrategyResult(BaseModel):
    """Resultado tipado devuelto por la ejecución de una estrategia de recuperación."""

    status: Literal["ok", "skipped", "error"] = "skipped"
    is_applicable: bool = True
    modified_count: int = 0
    corrections: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str = ""

    @property
    def error(self) -> str:
        """Alias para compatibilidad con código existente."""
        return self.error_message

    def __getitem__(self, item: str) -> Any:
        if item == "error":
            return self.error_message
        return getattr(self, item)

    def get(self, key: str, default: Any = None) -> Any:
        if key == "error":
            return self.error_message or default
        return getattr(self, key, default)


class ValidationResult(BaseModel):
    """Resultado tipado de la validación estructural de CSVs."""

    is_valid: bool
    errors: list[str] = Field(default_factory=list)

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


class RecoveryDecision(BaseModel):
    """Estructura de decisión emitida por el agente de recuperación."""

    diagnosis: str = Field(description="Explicación clara del error y su causa raíz.")
    confidence: Literal["high", "medium", "low"] = Field(
        description="Nivel de certeza en que la solución propuesta resolverá la falla sin efectos colaterales."
    )
    is_ambiguous: bool = Field(
        description="True si la solución implica pérdida de datos, suposiciones no verificadas o rangos complejos."
    )
    strategy: Literal["value_mapping", "regex_extraction", "date_normalization", "manual_review"] = Field(
        description="Estrategia seleccionada para normalizar la anomalía."
    )
    target_column: str = Field(default="date", description="Nombre de la columna afectada en el CSV.")
    mapping_rules: dict[str, str] = Field(
        default_factory=dict,
        description="Diccionario {antiguo_valor: nuevo_valor} si la estrategia es 'value_mapping'.",
    )
    regex_pattern: str = Field(
        default="",
        description="Patrón de expresión regular con grupos de captura si la estrategia es 'regex_extraction'.",
    )
    regex_group: int = Field(
        default=1,
        description="Índice del grupo de captura a extraer si la estrategia es 'regex_extraction'.",
    )
    reasoning: str = Field(description="Justificación detallada de por qué se adoptó esta decisión.")

    @property
    def is_auto_repairable(self) -> bool:
        """
        Determina si la decisión es susceptible de autorreparación automática
        sin requerir confirmación humana (Tell, Don't Ask).
        """
        from core.agent.dedup_recovery.strategies import StrategyRegistry

        strategy_impl = StrategyRegistry.get(self.strategy)
        return (
            self.confidence == "high"
            and not self.is_ambiguous
            and strategy_impl.is_applicable
        )

    def execute(self, csv_path: str) -> StrategyResult:
        """
        Delega la ejecución polimórfica de la estrategia asignada sobre el CSV.
        """
        from core.agent.dedup_recovery.strategies import StrategyRegistry

        strategy_impl = StrategyRegistry.get(self.strategy)
        return strategy_impl.execute(csv_path, self)
