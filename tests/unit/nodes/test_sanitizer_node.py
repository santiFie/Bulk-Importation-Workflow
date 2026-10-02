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
        "type": ["Articulo", "Articulo"],
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


def test_infer_missing_type():
    """Prueba la inferencia determinista de tipo documental."""
    from core.nodes.sanitizer_node import infer_missing_type

    # Con tipo existente
    assert infer_missing_type({"type": "Articulo"}) == ("Articulo", False)

    # Inferencia con ISSN
    assert infer_missing_type({"type": "", "issn": "1234-5678"}) == ("Articulo", True)

    # Inferencia con citación de revista
    assert infer_missing_type({"type": None, "citation": "Revista Científica Vol. 4"}) == ("Articulo", True)

    # Inferencia con ISBN
    assert infer_missing_type({"type": "", "isbn": "978-987-1234-56-7"}) == ("Libro", True)

    # Inferencia por título de tesis
    assert infer_missing_type({"type": "", "title": "Tesis Doctoral sobre Antigravity"}) == ("Tesis", True)

    # Sin evidencia
    assert infer_missing_type({"type": "", "title": "Texto general"}) == ("", False)


def test_infer_missing_date():
    """Prueba la inferencia determinista de año en campos contextuales."""
    from core.nodes.sanitizer_node import infer_missing_date

    # Con fecha existente
    assert infer_missing_date({"date": "2021-05"}) == ("2021-05", False)

    # Inferencia desde citación
    assert infer_missing_date({"date": "", "citation": "Revista UNLP, Año 2018, No 3"}) == ("2018", True)

    # Inferencia desde título
    assert infer_missing_date({"date": None, "title": "Estudio socioeconómico 1995"}) == ("1995", True)

    # Sin evidencia
    assert infer_missing_date({"date": "", "citation": "Sin año indicado"}) == ("", False)


def test_pre_dedup_sanitizer_quarantine_segregation(tmp_path: Path):
    """
    Prueba que los registros con fecha o tipo nulos insalvables se segreguen
    a pending_to_review.csv y se eliminen del lote genérico y del CSV fuente.
    """
    gen_csv = tmp_path / "generic_source.csv"
    src_csv = tmp_path / "source.csv"

    # Fila 0: Válida completa
    # Fila 1: Salvable por inferencia (tipo vacío pero con ISSN)
    # Fila 2: Fecha vacía insalvable -> debe ir a cuarentena
    # Fila 3: Tipo vacío insalvable -> debe ir a cuarentena
    df_src = pd.DataFrame({
        "id_orig": ["1", "2", "3", "4"],
        "title": ["Doc 1", "Doc 2", "Doc 3", "Doc 4"],
        "date_orig": ["2020", "2021", None, "2023"],
        "type_orig": ["Articulo", None, "Articulo", None],
        "issn_orig": ["1111", "2222", "3333", None],
    })
    df_src.to_csv(src_csv, index=False)

    df_gen = pd.DataFrame({
        "id": ["1", "2", "3", "4"],
        "title": ["Doc 1", "Doc 2", "Doc 3", "Doc 4"],
        "date": ["2020", "2021", "", "2023"],
        "type": ["Articulo", "", "Articulo", ""],
        "issn": ["1111", "2222", "3333", ""],
        "author": ["A1", "A2", "A3", "A4"],
    })
    df_gen.to_csv(gen_csv, index=False)

    pending_csv = tmp_path / "pending_to_review.csv"

    state = {
        "workspace_dir": str(tmp_path),
        "source_csv_path": str(src_csv),
        "generic_source_csv_path": str(gen_csv),
        "pending_to_review_csv_path": str(pending_csv),
        "applied_corrections": [],
    }

    result = pre_dedup_sanitizer(state)

    # 1. Verificar pending_to_review.csv
    assert "pending_to_review_csv_path" in result
    assert pending_csv.exists()
    df_pending = pd.read_csv(pending_csv, dtype=str)
    assert len(df_pending) == 2
    assert list(df_pending["id_orig"]) == ["3", "4"]
    assert "quarantine_reason" in df_pending.columns
    assert df_pending.loc[0, "quarantine_reason"] == "Campo obligatorio 'date' ausente"
    assert df_pending.loc[1, "quarantine_reason"] == "Campo obligatorio 'type' ausente"

    # 2. Verificar generic_source.csv (solo deben quedar filas 1 y 2)
    df_gen_clean = pd.read_csv(gen_csv, dtype=str)
    assert len(df_gen_clean) == 2
    assert list(df_gen_clean["id"]) == ["1", "2"]
    # La fila 2 debe tener tipo inferido como 'Articulo' por el ISSN
    assert df_gen_clean.loc[1, "type"] == "Articulo"

    # 3. Verificar source.csv sincronizado (solo deben quedar filas 1 y 2)
    df_src_clean = pd.read_csv(src_csv, dtype=str)
    assert len(df_src_clean) == 2
    assert list(df_src_clean["id_orig"]) == ["1", "2"]


def test_pre_dedup_sanitizer_all_quarantined(tmp_path: Path):
    """Prueba el comportamiento cuando el 100% de los registros van a cuarentena."""
    gen_csv = tmp_path / "generic_source.csv"
    df_gen = pd.DataFrame({
        "id": ["1", "2"],
        "title": ["Doc 1", "Doc 2"],
        "date": ["", ""],
        "type": ["", ""],
        "author": ["A1", "A2"],
    })
    df_gen.to_csv(gen_csv, index=False)

    state = {
        "workspace_dir": str(tmp_path),
        "generic_source_csv_path": str(gen_csv),
        "applied_corrections": [],
    }

    result = pre_dedup_sanitizer(state)
    assert "node_errors" in result
    assert "PreDedupSanitizer" in result["node_errors"]

