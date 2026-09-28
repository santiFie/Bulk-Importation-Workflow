"""
Tests unitarios para las estrategias polimórficas de recuperación (Strategy Pattern + NullObject).
"""

from __future__ import annotations

from pathlib import Path
import pandas as pd
import pytest

from core.agent.dedup_recovery.models import RecoveryDecision, StrategyResult
from core.agent.dedup_recovery.strategies import (
    DateNormalizationStrategy,
    NullRecoveryStrategy,
    RegexExtractionStrategy,
    StrategyRegistry,
    ValueMappingStrategy,
)


@pytest.fixture
def sample_csv(tmp_path: Path) -> str:
    path = tmp_path / "data.csv"
    df = pd.DataFrame({
        "id": ["1", "2"],
        "date": ["1999, June", "July 2000"],
    })
    df.to_csv(path, index=False)
    return str(path)


def test_strategy_registry_retrieval():
    assert isinstance(StrategyRegistry.get("value_mapping"), ValueMappingStrategy)
    assert isinstance(StrategyRegistry.get("regex_extraction"), RegexExtractionStrategy)
    assert isinstance(StrategyRegistry.get("date_normalization"), DateNormalizationStrategy)
    assert isinstance(StrategyRegistry.get("manual_review"), NullRecoveryStrategy)
    assert isinstance(StrategyRegistry.get("unknown_strategy"), NullRecoveryStrategy)


def test_null_recovery_strategy(sample_csv: str):
    null_strat = NullRecoveryStrategy()
    assert null_strat.is_applicable is False

    decision = RecoveryDecision(
        diagnosis="Incertidumbre alta",
        confidence="low",
        is_ambiguous=True,
        strategy="manual_review",
        reasoning="Requiere juicio humano",
    )
    res = null_strat.execute(sample_csv, decision)
    assert isinstance(res, StrategyResult)
    assert res.status == "skipped"
    assert res.is_applicable is False
    assert res.modified_count == 0


def test_value_mapping_strategy(sample_csv: str):
    strat = ValueMappingStrategy()
    assert strat.is_applicable is True

    decision = RecoveryDecision(
        diagnosis="Mes textual en columna date",
        confidence="high",
        is_ambiguous=False,
        strategy="value_mapping",
        target_column="date",
        mapping_rules={"June": "06", "July": "07"},
        reasoning="Mapeo a ISO",
    )
    res = strat.execute(sample_csv, decision)
    assert res.status == "ok"
    assert res.modified_count == 2
    assert res.is_applicable is True

    df = pd.read_csv(sample_csv, dtype=str)
    assert df["date"].iloc[0] == "1999, 06"


def test_recovery_decision_is_auto_repairable():
    # 1. Caso autorreparable
    d1 = RecoveryDecision(
        diagnosis="Error simple",
        confidence="high",
        is_ambiguous=False,
        strategy="value_mapping",
        reasoning="Probado",
    )
    assert d1.is_auto_repairable is True

    # 2. Confianza media -> No autorreparable
    d2 = RecoveryDecision(
        diagnosis="Error simple",
        confidence="medium",
        is_ambiguous=False,
        strategy="value_mapping",
        reasoning="Dudoso",
    )
    assert d2.is_auto_repairable is False

    # 3. Ambiguo -> No autorreparable
    d3 = RecoveryDecision(
        diagnosis="Rango de fechas",
        confidence="high",
        is_ambiguous=True,
        strategy="regex_extraction",
        reasoning="Pérdida de precisión",
    )
    assert d3.is_auto_repairable is False

    # 4. Revisión manual -> No autorreparable (NullObject is_applicable is False)
    d4 = RecoveryDecision(
        diagnosis="Complejo",
        confidence="high",
        is_ambiguous=False,
        strategy="manual_review",
        reasoning="No se puede automatizar",
    )
    assert d4.is_auto_repairable is False


def test_recovery_decision_execute_delegation(sample_csv: str):
    decision = RecoveryDecision(
        diagnosis="Mes textual",
        confidence="high",
        is_ambiguous=False,
        strategy="value_mapping",
        target_column="date",
        mapping_rules={"June": "06"},
        reasoning="Delegación",
    )
    res = decision.execute(sample_csv)
    assert res.status == "ok"
    assert res.modified_count >= 1
