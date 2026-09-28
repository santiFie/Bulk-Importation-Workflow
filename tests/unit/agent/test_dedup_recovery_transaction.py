"""
Tests unitarios para el gestor transaccional CsvTransaction.
"""

from __future__ import annotations

import os
from pathlib import Path
import pandas as pd
import pytest

from core.agent.dedup_recovery.transaction import CsvTransaction


@pytest.fixture
def original_csv(tmp_path: Path) -> str:
    path = tmp_path / "dataset.csv"
    df = pd.DataFrame({
        "id": ["101", "102"],
        "title": ["Obra A", "Obra B"],
        "date": ["1999, June", "2000"],
    })
    df.to_csv(path, index=False)
    return str(path)


def test_transaction_commit_lifecycle(original_csv: str):
    """Verifica que los cambios se mantengan tras commit y el backup se elimine."""
    backup_file = None
    with CsvTransaction(original_csv) as tx:
        backup_file = tx.backup_path
        assert os.path.isfile(backup_file)

        # Modificación
        df = pd.read_csv(original_csv, dtype=str)
        df.at[0, "date"] = "1999-06"
        df.to_csv(original_csv, index=False)

        val = tx.validate()
        assert val.is_valid is True
        tx.commit()

    # Post-contexto: backup debe estar eliminado y los cambios persistidos
    assert not os.path.isfile(backup_file)
    df_post = pd.read_csv(original_csv, dtype=str)
    assert df_post["date"].iloc[0] == "1999-06"


def test_transaction_rollback_explicit(original_csv: str):
    """Verifica que el rollback explícito restaure el contenido original."""
    backup_file = None
    with CsvTransaction(original_csv) as tx:
        backup_file = tx.backup_path

        # Modificación que corrompe las columnas
        df = pd.DataFrame({"corrupt": ["val"]})
        df.to_csv(original_csv, index=False)

        val = tx.validate()
        assert val.is_valid is False
        assert any("columnas" in e for e in val.errors)

        tx.rollback()

    assert not os.path.isfile(backup_file)
    df_restored = pd.read_csv(original_csv, dtype=str)
    assert "title" in df_restored.columns
    assert df_restored["date"].iloc[0] == "1999, June"


def test_transaction_rollback_on_exception(original_csv: str):
    """Verifica que una excepción no controlada ejecute rollback y limpie el backup."""
    backup_file = None
    with pytest.raises(RuntimeError):
        with CsvTransaction(original_csv) as tx:
            backup_file = tx.backup_path

            # Modificación parcial
            df = pd.DataFrame({"id": ["only_one"]})
            df.to_csv(original_csv, index=False)

            raise RuntimeError("Error imprevisto en la ejecución.")

    assert not os.path.isfile(backup_file)
    df_restored = pd.read_csv(original_csv, dtype=str)
    assert len(df_restored) == 2
    assert df_restored["title"].iloc[0] == "Obra A"
