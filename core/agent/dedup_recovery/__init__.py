"""
Módulo del Agente de Recuperación de Deduplicación.
"""

from core.agent.dedup_recovery.agent import (
    dedup_recovery_node,
    invoke_recovery_agent,
    format_episodes_for_prompt,
    build_recovery_system_prompt,
)
from core.agent.dedup_recovery.models import (
    RecoveryDecision,
    StrategyResult,
    ValidationResult,
)
from core.agent.dedup_recovery.strategies import (
    RecoveryStrategy,
    ValueMappingStrategy,
    RegexExtractionStrategy,
    DateNormalizationStrategy,
    NullRecoveryStrategy,
    StrategyRegistry,
)
from core.agent.dedup_recovery.transaction import CsvTransaction
from core.agent.dedup_recovery.handlers import (
    HumanActionHandler,
    AcceptProposalHandler,
    ProvideManualCsvHandler,
    AbortActionHandler,
    ActionHandlerRegistry,
    RecoveryContext,
    persist_success_episode,
)
from core.agent.dedup_recovery.tools import (
    apply_value_mapping,
    apply_regex_extraction,
    apply_date_normalization,
    validate_csv_structure,
)

__all__ = [
    "dedup_recovery_node",
    "invoke_recovery_agent",
    "format_episodes_for_prompt",
    "build_recovery_system_prompt",
    "RecoveryDecision",
    "StrategyResult",
    "ValidationResult",
    "RecoveryStrategy",
    "ValueMappingStrategy",
    "RegexExtractionStrategy",
    "DateNormalizationStrategy",
    "NullRecoveryStrategy",
    "StrategyRegistry",
    "CsvTransaction",
    "HumanActionHandler",
    "AcceptProposalHandler",
    "ProvideManualCsvHandler",
    "AbortActionHandler",
    "ActionHandlerRegistry",
    "RecoveryContext",
    "persist_success_episode",
    "apply_value_mapping",
    "apply_regex_extraction",
    "apply_date_normalization",
    "validate_csv_structure",
]
