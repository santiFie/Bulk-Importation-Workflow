"""
Tests unitarios para las herramientas seguras del Agente de Recuperación.
"""

from __future__ import annotations

from pathlib import Path
import pandas as pd
import pytest

from core.agent.dedup_recovery.tools import (
    apply_value_mapping,
    apply_regex_extraction,
    apply_date_normalization,
    validate_csv_structure,
)


@pytest.fixture
def sample_csv(tmp_path: Path):
    path = tmp_path / "test.csv"
    data = {
        "id": ["1", "2", "3"],
        "title": ["Paper 1", "Paper 2", "Paper 3"],
        "date": ["1999, June", "July 2000", "2001"],
    }
    pd.DataFrame(data).to_csv(path, index=False)
    return str(path)


def test_apply_value_mapping(sample_csv: str):
    mapping = {"June": "06", "July": "07"}
    res = apply_value_mapping(sample_csv, "date", mapping)
    assert res["status"] == "ok"
    assert res["modified_count"] == 2

    df = pd.read_csv(sample_csv, dtype=str)
    assert df["date"].iloc[0] == "1999, 06"
    assert df["date"].iloc[1] == "07 2000"


def test_apply_regex_extraction(tmp_path: Path):
    path = tmp_path / "regex_test.csv"
    data = {"id": ["1", "2"], "date": ["June-July 1999", "Primavera 2004"]}
    pd.DataFrame(data).to_csv(path, index=False)

    res = apply_regex_extraction(str(path), "date", r"\b(19\d\d|20\d\d)\b", group=1)
    assert res["status"] == "ok"
    assert res["modified_count"] == 2

    df = pd.read_csv(str(path), dtype=str)
    assert list(df["date"]) == ["1999", "2004"]


def test_apply_date_normalization(sample_csv: str):
    res = apply_date_normalization(sample_csv, "date")
    assert res["status"] == "ok"
    assert res["modified_count"] == 2

    df = pd.read_csv(sample_csv, dtype=str)
    assert df["date"].iloc[0] == "1999-06"
    assert df["date"].iloc[1] == "2000-07"


def test_validate_csv_structure_valid(tmp_path: Path):
    p1 = tmp_path / "orig.csv"
    p2 = tmp_path / "mod.csv"
    data = {"id": ["1", "2"], "title": ["A", "B"]}
    pd.DataFrame(data).to_csv(p1, index=False)
    pd.DataFrame(data).to_csv(p2, index=False)

    check = validate_csv_structure(str(p1), str(p2))
    assert check["is_valid"] is True
    assert len(check["errors"]) == 0


def test_validate_csv_structure_corrupted(tmp_path: Path):
    p1 = tmp_path / "orig.csv"
    p2 = tmp_path / "mod.csv"
    pd.DataFrame({"id": ["1", "2"], "title": ["A", "B"]}).to_csv(p1, index=False)
    # mod perdió una fila y la columna title
    pd.DataFrame({"id": ["1"]}).to_csv(p2, index=False)

    check = validate_csv_structure(str(p1), str(p2))
    assert check["is_valid"] is False
    assert any("filas" in e for e in check["errors"])
    assert any("columnas" in e for e in check["errors"])
