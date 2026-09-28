"""
Tests unitarios para el nodo PreDedupSanitizer.
"""

from __future__ import annotations

from pathlib import Path
import pandas as pd
import pytest

from core.nodes.sanitizer_node import clean_date_value, pre_dedup_sanitizer


@pytest.mark.parametrize(
    "raw_val, expected_clean, expected_modified",
    [
        ("1999", "1999", False),
        ("2024-05", "2024-05", False),
        ("2024-05-12", "2024-05-12", False),
        ("1999, June", "1999-06", True),
        ("June 1999", "1999-06", True),
        ("2004, Junio", "2004-06", True),
        ("Junio 2004", "2004-06", True),
        ("June-July 1999", "1999", True),
        ("1999, June-July", "1999", True),
        ("1999.", "1999", True),
        (" 2001 ", "2001", True),
        ("", "", False),
        (None, "", False),
    ],
)
def test_clean_date_value(raw_val, expected_clean, expected_modified):
    clean, modified = clean_date_value(raw_val)
    assert clean == expected_clean
    assert modified == expected_modified


def test_pre_dedup_sanitizer_full(tmp_path: Path):
    """Prueba la ejecución del nodo sobre un CSV y la persistencia de correcciones."""
    csv_path = tmp_path / "generic_source.csv"
    data = {
        "id": [" 101 ", "102"],
        "title": [" Paper A ", "Paper B"],
        "date": ["1999, June", "2004, Junio"],
        "author": ["Doe, J.", "Smith, A."],
    }
    pd.DataFrame(data).to_csv(csv_path, index=False)

    state = {
        "generic_source_csv_path": str(csv_path),
        "applied_corrections": [],
    }

    result = pre_dedup_sanitizer(state)
    assert "applied_corrections" in result
    corrections = result["applied_corrections"]
    assert len(corrections) == 2

    # Verificar archivo modificado en disco
    df_clean = pd.read_csv(csv_path, dtype=str)
    assert list(df_clean["date"]) == ["1999-06", "2004-06"]
    assert list(df_clean["id"]) == ["101", "102"]
    assert list(df_clean["title"]) == ["Paper A", "Paper B"]
