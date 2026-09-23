"""
Tests unitarios para la detección de cambios de esquema (drift detection) y
reutilización de configuraciones de crosswalk en caché (Issue #35).
"""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.nodes.crosswalk_agent.helpers import (
    DriftReport,
    validate_config_against_csv,
    get_existing_config,
)
from core.nodes.crosswalk_agent.node import generate_source_crosswalk_config


# ---------------------------------------------------------------------------
# Fixtures auxiliares para crear CSVs y configuraciones JSON
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_config_dict():
    """Configuración estándar de crosswalk con campos requeridos y opcionales."""
    return [
        [
            {
                "left": "Item DOI",
                "replace": "doi",
                "required": True,
                "default": None,
                "filter": "",
            },
            {
                "left": "Publication Title",
                "replace": "title",
                "required": True,
                "default": None,
                "filter": "",
            },
            {
                "left": "Authors",
                "replace": "author",
                "required": True,
                "default": None,
                "filter": "trim",
            },
            {
                "left": "Publication Year",
                "replace": "date",
                "required": True,
                "default": None,
                "filter": "",
            },
            {
                "left": "Abstract",
                "replace": "description",
                "required": False,
                "default": "",
                "filter": "",
            },
        ],
        {
            "original_separator": "||",
            "replace_separator": "|",
            "file_delimiter": ",",
        },
    ]


@pytest.fixture
def setup_test_files(tmp_path, sample_config_dict):
    """
    Crea archivos de prueba base:
      - CSV estándar con las columnas requeridas y opcionales.
      - Archivo de configuración JSON correspondiente.
    """
    source_name = "test_repo"
    config_file = tmp_path / f"crosswalk_config_{source_name}.json"
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(sample_config_dict, f, indent=2)

    csv_file = tmp_path / "input.csv"
    csv_content = (
        "Item DOI,Publication Title,Authors,Publication Year,Abstract\n"
        "10.1000/1,Test Title,Author A||Author B,2024,An abstract here\n"
        "10.1000/2,Another Title,Author C,2023,Another abstract\n"
    )
    csv_file.write_text(csv_content, encoding="utf-8")

    return {
        "csv_path": str(csv_file),
        "config_path": str(config_file),
        "source_name": source_name,
        "tmp_path": tmp_path,
    }


# ---------------------------------------------------------------------------
# Tests para validate_config_against_csv
# ---------------------------------------------------------------------------

class TestValidateConfigAgainstCsv:
    def test_full_match_is_valid(self, setup_test_files):
        """El esquema coincide plenamente: is_valid es True, no hay columnas faltantes ni sin mapear."""
        report = validate_config_against_csv(
            setup_test_files["csv_path"], setup_test_files["config_path"]
        )
        assert isinstance(report, DriftReport)
        assert report.is_valid is True
        assert report.missing_columns == []
        assert report.unmapped_columns == []
        assert "plenamente" in report.message.lower() or "válida" in report.message.lower()

    def test_missing_required_column_invalidates_cache(self, setup_test_files, tmp_path):
        """Si falta una columna requerida en el CSV, is_valid es False y se reporta en missing_columns."""
        drift_csv = tmp_path / "drift_missing_doi.csv"
        # Omitimos la columna requerida "Item DOI"
        drift_csv.write_text(
            "Publication Title,Authors,Publication Year,Abstract\n"
            "Title 1,Author 1,2024,Abs\n",
            encoding="utf-8",
        )

        report = validate_config_against_csv(str(drift_csv), setup_test_files["config_path"])
        assert report.is_valid is False
        assert "Item DOI" in report.missing_columns
        assert report.unmapped_columns == []
        assert "drift" in report.message.lower()

    def test_missing_optional_column_remains_valid(self, setup_test_files, tmp_path):
        """Una columna con required=False que no está en el CSV no cuenta como missing_column."""
        drift_csv = tmp_path / "no_abstract.csv"
        # Omitimos "Abstract", que tiene required=False
        drift_csv.write_text(
            "Item DOI,Publication Title,Authors,Publication Year\n"
            "10.1234/test,Title 1,Author 1,2024\n",
            encoding="utf-8",
        )

        report = validate_config_against_csv(str(drift_csv), setup_test_files["config_path"])
        assert report.is_valid is True
        assert report.missing_columns == []
        assert report.unmapped_columns == []

    def test_unmapped_columns_detected(self, setup_test_files, tmp_path):
        """Columnas extras en el CSV se detectan como unmapped_columns sin invalidar is_valid si no faltan requeridas."""
        extra_cols_csv = tmp_path / "extra_cols.csv"
        extra_cols_csv.write_text(
            "Item DOI,Publication Title,Authors,Publication Year,Abstract,Keywords,Notes\n"
            "10.1000/1,Title,Author,2024,Abstract,AI|ML,Some internal note\n",
            encoding="utf-8",
        )

        report = validate_config_against_csv(str(extra_cols_csv), setup_test_files["config_path"])
        assert report.is_valid is True
        assert report.missing_columns == []
        assert set(report.unmapped_columns) == {"Keywords", "Notes"}
        assert "Columnas no mapeadas" in report.message

    def test_multi_column_alternatives_with_plus(self, tmp_path):
        """
        Para mapping con 'left'='colA+colB' y required=True:
          - Satisfecho si al menos una alternativa está en el CSV.
          - Si ninguna está presente, se reporta como missing_columns.
        """
        config_path = tmp_path / "multi_config.json"
        multi_config = [
            [
                {
                    "left": "dc.title+Title",
                    "replace": "title",
                    "required": True,
                },
                {
                    "left": "Author",
                    "replace": "author",
                    "required": True,
                },
            ],
            {"file_delimiter": ",", "original_separator": "||", "replace_separator": "|"},
        ]
        config_path.write_text(json.dumps(multi_config), encoding="utf-8")

        # Caso 1: CSV tiene "Title" (segunda alternativa)
        csv1 = tmp_path / "csv1.csv"
        csv1.write_text("Title,Author\nFoo,Bar\n", encoding="utf-8")
        report1 = validate_config_against_csv(str(csv1), str(config_path))
        assert report1.is_valid is True
        assert report1.missing_columns == []
        assert report1.unmapped_columns == []

        # Caso 2: CSV tiene "dc.title" (primera alternativa)
        csv2 = tmp_path / "csv2.csv"
        csv2.write_text("dc.title,Author\nFoo,Bar\n", encoding="utf-8")
        report2 = validate_config_against_csv(str(csv2), str(config_path))
        assert report2.is_valid is True
        assert report2.missing_columns == []
        assert report2.unmapped_columns == []

        # Caso 3: CSV no tiene ninguna de las dos alternativas
        csv3 = tmp_path / "csv3.csv"
        csv3.write_text("nombre,Author\nFoo,Bar\n", encoding="utf-8")
        report3 = validate_config_against_csv(str(csv3), str(config_path))
        assert report3.is_valid is False
        assert "dc.title+Title" in report3.missing_columns
        assert "nombre" in report3.unmapped_columns

    def test_file_not_found_handling(self, tmp_path):
        """Manejo defensivo ante archivos inexistentes."""
        report = validate_config_against_csv(
            str(tmp_path / "nonexistent.csv"), str(tmp_path / "nonexistent.json")
        )
        assert report.is_valid is False
        assert "no existe" in report.message.lower()


# ---------------------------------------------------------------------------
# Tests para get_existing_config
# ---------------------------------------------------------------------------

class TestGetExistingConfig:
    def test_file_not_existing_returns_none(self, tmp_path):
        """Si el archivo de configuración no existe en disco, retorna None."""
        state = {
            "source_csv_path": str(tmp_path / "input.csv"),
            "source_name": "never_imported",
        }
        res = get_existing_config(state)
        assert res is None

    @patch("core.nodes.crosswalk_agent.helpers._validate_config_deterministic")
    def test_successful_reuse_when_schema_matches_and_validation_passes(
        self, mock_deterministic, setup_test_files
    ):
        """Reutilización exitosa si el esquema del CSV coincide plenamente y la validación determinista pasa."""
        mock_deterministic.return_value = {
            "ok": True,
            "columns": ["id", "title", "author", "date", "type"],
            "missing": [],
            "separator_ok": True,
            "message": "Validación OK.",
        }

        state = {
            "source_csv_path": setup_test_files["csv_path"],
            "source_name": setup_test_files["source_name"],
        }

        res = get_existing_config(state)
        assert res is not None
        assert res == {"source_crosswalk_config": setup_test_files["config_path"]}
        mock_deterministic.assert_called_once_with(
            setup_test_files["csv_path"], setup_test_files["config_path"]
        )

    @patch("core.nodes.crosswalk_agent.helpers._validate_config_deterministic")
    def test_rejects_cache_on_missing_required_columns(
        self, mock_deterministic, setup_test_files, tmp_path, caplog
    ):
        """Rechazo de caché y retorno None si faltan columnas origen requeridas en el nuevo CSV."""
        drift_csv = tmp_path / "missing_author.csv"
        drift_csv.write_text(
            "Item DOI,Publication Title,Publication Year\n"
            "10.1000/1,Title,2024\n",
            encoding="utf-8",
        )

        state = {
            "source_csv_path": str(drift_csv),
            "source_name": setup_test_files["source_name"],
        }

        with caplog.at_level(logging.WARNING):
            res = get_existing_config(state)

        assert res is None
        # La validación determinista ni siquiera debe ejecutarse si hay drift crítico
        mock_deterministic.assert_not_called()
        assert any("Drift detectado" in record.message for record in caplog.records)
        assert any("Authors" in record.message for record in caplog.records)

    @patch("core.nodes.crosswalk_agent.helpers._validate_config_deterministic")
    def test_warns_on_unmapped_columns_and_reuses_if_valid(
        self, mock_deterministic, setup_test_files, tmp_path, caplog
    ):
        """Detección de drift y advertencia cuando se introducen columnas adicionales (reutilizando el config si es válido)."""
        extra_csv = tmp_path / "with_extra.csv"
        extra_csv.write_text(
            "Item DOI,Publication Title,Authors,Publication Year,Abstract,NewColumn1,NewColumn2\n"
            "10.1000/1,Title,Author,2024,Abs,Val1,Val2\n",
            encoding="utf-8",
        )

        mock_deterministic.return_value = {
            "ok": True,
            "columns": ["id", "title", "author", "date", "type"],
            "missing": [],
            "separator_ok": True,
            "message": "Validación OK.",
        }

        state = {
            "source_csv_path": str(extra_csv),
            "source_name": setup_test_files["source_name"],
        }

        with caplog.at_level(logging.WARNING):
            res = get_existing_config(state)

        # Se reutiliza porque no faltan requeridas y la validación determinista pasó
        assert res is not None
        assert res == {"source_crosswalk_config": setup_test_files["config_path"]}
        mock_deterministic.assert_called_once()
        # Debe haber logueado advertencia sobre las columnas no mapeadas
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("NewColumn1" in r.message and "NewColumn2" in r.message for r in warning_records)

    @patch("core.nodes.crosswalk_agent.helpers._validate_config_deterministic")
    def test_rejects_cache_when_deterministic_validation_fails(
        self, mock_deterministic, setup_test_files, caplog
    ):
        """Rechazo de caché si _validate_config_deterministic falla."""
        mock_deterministic.return_value = {
            "ok": False,
            "columns": ["author", "date"],
            "missing": ["id", "title"],
            "separator_ok": True,
            "message": "FALTAN columnas críticas: id, title",
        }

        state = {
            "source_csv_path": setup_test_files["csv_path"],
            "source_name": setup_test_files["source_name"],
        }

        with caplog.at_level(logging.WARNING):
            res = get_existing_config(state)

        assert res is None
        mock_deterministic.assert_called_once()
        assert any("Validación determinista fallida" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Test de integración del nodo generate_source_crosswalk_config
# ---------------------------------------------------------------------------

class TestGenerateSourceCrosswalkConfigNode:
    @patch("core.nodes.crosswalk_agent.node.get_existing_config")
    @patch("core.nodes.crosswalk_agent.node.FallbackLLM")
    def test_node_returns_cached_config_without_calling_llm(
        self, mock_llm_cls, mock_get_existing_config, setup_test_files
    ):
        """Cuando get_existing_config retorna una configuración válida, el nodo la devuelve sin instanciar ni invocar al LLM."""
        cached_result = {"source_crosswalk_config": setup_test_files["config_path"]}
        mock_get_existing_config.return_value = cached_result

        state = {
            "source_csv_path": setup_test_files["csv_path"],
            "source_name": setup_test_files["source_name"],
        }

        result = generate_source_crosswalk_config(state)

        assert result == cached_result
        mock_get_existing_config.assert_called_once_with(state)
        mock_llm_cls.assert_not_called()
