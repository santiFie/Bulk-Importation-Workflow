"""
Tests unitarios para el nodo ValidateInputCSVs (core/nodes/validation_node.py).

Cubre:
  - CSVs válidos de Source y SEDICI pasan sin error.
  - Archivos inexistentes o vacíos (0 bytes / 0 filas de datos) lanzan ValueError temprano (Fail-Fast).
  - Source CSV sin título y sin DOI lanza ValueError temprano.
  - Source CSV sin título pero con DOI es aceptado sin error.
  - Source CSV sin ID genera automáticamente la columna 'id' autoincremental y actualiza 'source_csv_path'.
  - SEDICI CSV sin columnas requeridas (título o identificador) lanza ValueError.
  - Ausencia de columnas habituales emite advertencia en el log.
  - Tolerancia cuando existen errores previos de ingestión (PDFIngest_errors).
"""

import os
import logging
import pandas as pd
import pytest

from core.nodes.validation_node import validate_input_csvs_node
from core.state import State


@pytest.fixture
def valid_source_csv(tmp_path):
    """Genera un CSV fuente válido con todas las columnas estándar."""
    csv_file = tmp_path / "valid_source.csv"
    df = pd.DataFrame([
        {
            "id": "item-1",
            "title": "Machine Learning in DSpace",
            "author": "Perez, Juan",
            "date": "2024-01-01",
            "doi": "10.1234/test.doi.1",
            "type": "Article",
            "issn": "1234-5678",
        },
        {
            "id": "item-2",
            "title": "Automated Metadata Ingestion",
            "author": "Gomez, Maria",
            "date": "2024-02-01",
            "doi": "10.1234/test.doi.2",
            "type": "Conference Paper",
            "issn": "1234-5678",
        },
    ])
    df.to_csv(csv_file, index=False)
    return str(csv_file)


@pytest.fixture
def valid_sedici_csv(tmp_path):
    """Genera un CSV SEDICI válido con dc.title y dc.identifier.uri."""
    csv_file = tmp_path / "valid_sedici.csv"
    df = pd.DataFrame([
        {
            "id": "10915/12345",
            "dc.title": "Previous Work on DSpace",
            "dc.identifier.uri": "http://sedici.unlp.edu.ar/handle/10915/12345",
            "dc.date.issued": "2023",
        }
    ])
    df.to_csv(csv_file, index=False)
    return str(csv_file)


class TestValidateInputCSVsNode:
    """Suite de pruebas unitarias para validate_input_csvs_node."""

    def test_valid_source_and_sedici_csvs_pass(self, valid_source_csv, valid_sedici_csv):
        """CSVs válidos de Source y SEDICI se validan exitosamente sin alteraciones."""
        state: State = {
            "source_csv_path": valid_source_csv,
            "repository_csv_path": valid_sedici_csv,
        }

        updates = validate_input_csvs_node(state)

        assert updates == {}
        assert state.get("source_csv_path") == valid_source_csv
        assert "ValidateInputCSVs" not in state.get("node_errors", {})

    def test_valid_source_without_sedici_csv_passes(self, valid_source_csv):
        """Si repository_csv_path no está presente o es None, solo se valida source y pasa."""
        state: State = {
            "source_csv_path": valid_source_csv,
            "repository_csv_path": "",
        }

        updates = validate_input_csvs_node(state)
        assert updates == {}

    def test_source_csv_missing_in_state_raises_value_error(self):
        """Si source_csv_path no está en el estado o está vacío, lanza ValueError."""
        state: State = {}

        with pytest.raises(ValueError, match="source_csv_path"):
            validate_input_csvs_node(state)

        assert "ValidateInputCSVs" in state.get("node_errors", {})

    def test_source_csv_nonexistent_file_raises_value_error(self, tmp_path):
        """Si el archivo source_csv_path no existe físicamente, lanza ValueError."""
        nonexistent = str(tmp_path / "does_not_exist.csv")
        state: State = {"source_csv_path": nonexistent}

        with pytest.raises(ValueError, match="no existe"):
            validate_input_csvs_node(state)

        assert "ValidateInputCSVs" in state.get("node_errors", {})

    def test_source_csv_empty_zero_bytes_raises_value_error(self, tmp_path):
        """Si source_csv_path tiene 0 bytes, lanza ValueError."""
        empty_file = tmp_path / "empty_0bytes.csv"
        empty_file.write_text("")
        state: State = {"source_csv_path": str(empty_file)}

        with pytest.raises(ValueError, match="vacío \\(0 bytes\\)"):
            validate_input_csvs_node(state)

        assert "ValidateInputCSVs" in state.get("node_errors", {})

    def test_source_csv_zero_rows_raises_value_error(self, tmp_path):
        """Si source_csv_path contiene cabecera pero 0 filas de datos, lanza ValueError."""
        header_only = tmp_path / "header_only.csv"
        header_only.write_text("id,title,author\n")
        state: State = {"source_csv_path": str(header_only)}

        with pytest.raises(ValueError, match="no contiene filas de datos"):
            validate_input_csvs_node(state)

        assert "ValidateInputCSVs" in state.get("node_errors", {})

    def test_source_without_title_and_without_doi_raises_value_error(self, tmp_path):
        """Si source_csv_path no tiene columna de título ni columna de DOI, lanza ValueError temprano."""
        csv_file = tmp_path / "no_title_no_doi.csv"
        df = pd.DataFrame([
            {"id": "1", "author": "Smith, J.", "date": "2024", "type": "Article"}
        ])
        df.to_csv(csv_file, index=False)

        state: State = {"source_csv_path": str(csv_file)}

        with pytest.raises(ValueError, match="no contiene columna de título ni de DOI"):
            validate_input_csvs_node(state)

        assert "ValidateInputCSVs" in state.get("node_errors", {})

    def test_source_without_title_but_with_doi_accepted(self, tmp_path):
        """Si no tiene columna título pero tiene DOI, se permite continuar para enriquecimiento."""
        csv_file = tmp_path / "no_title_has_doi.csv"
        df = pd.DataFrame([
            {"id": "1", "doi": "10.1000/182", "author": "Smith, J.", "date": "2024"}
        ])
        df.to_csv(csv_file, index=False)

        state: State = {"source_csv_path": str(csv_file)}

        updates = validate_input_csvs_node(state)
        # No debe lanzar error
        assert "ValidateInputCSVs" not in state.get("node_errors", {})

    def test_source_without_id_generates_autoincremental_id(self, tmp_path):
        """Si no hay columna identificadora candidata, genera 'id' autoincremental (1, 2, 3...)."""
        csv_file = tmp_path / "no_id.csv"
        df = pd.DataFrame([
            {"title": "Paper One", "author": "Author A", "date": "2024"},
            {"title": "Paper Two", "author": "Author B", "date": "2024"},
            {"title": "Paper Three", "author": "Author C", "date": "2024"},
        ])
        df.to_csv(csv_file, index=False)

        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir(parents=True, exist_ok=True)

        state: State = {
            "source_csv_path": str(csv_file),
            "workspace_dir": str(workspace_dir),
        }

        updates = validate_input_csvs_node(state)

        expected_new_path = str(workspace_dir / "source_with_id.csv")
        assert updates.get("source_csv_path") == expected_new_path
        assert state.get("source_csv_path") == expected_new_path
        assert os.path.isfile(expected_new_path)

        df_generated = pd.read_csv(expected_new_path)
        assert "id" in df_generated.columns
        # Verificar valores autoincrementales 1, 2, 3
        assert list(df_generated["id"]) == [1, 2, 3]
        assert list(df_generated["title"]) == ["Paper One", "Paper Two", "Paper Three"]

    def test_sedici_csv_nonexistent_raises_value_error(self, valid_source_csv, tmp_path):
        """Si repository_csv_path apunta a un archivo inexistente, lanza ValueError."""
        state: State = {
            "source_csv_path": valid_source_csv,
            "repository_csv_path": str(tmp_path / "no_sedici.csv"),
        }

        with pytest.raises(ValueError, match="repository_csv_path no existe"):
            validate_input_csvs_node(state)

        assert "ValidateInputCSVs" in state.get("node_errors", {})

    def test_sedici_csv_empty_zero_bytes_raises_value_error(self, valid_source_csv, tmp_path):
        """Si repository_csv_path está vacío (0 bytes), lanza ValueError."""
        empty_repo = tmp_path / "empty_repo.csv"
        empty_repo.write_text("")

        state: State = {
            "source_csv_path": valid_source_csv,
            "repository_csv_path": str(empty_repo),
        }

        with pytest.raises(ValueError, match="repository_csv_path está vacío"):
            validate_input_csvs_node(state)

        assert "ValidateInputCSVs" in state.get("node_errors", {})

    def test_sedici_csv_missing_title_raises_value_error(self, valid_source_csv, tmp_path):
        """Si repository_csv_path no contiene dc.title ni title, lanza ValueError."""
        bad_sedici = tmp_path / "sedici_no_title.csv"
        df = pd.DataFrame([
            {"id": "1", "dc.identifier.uri": "http://sedici/handle/1", "date": "2024"}
        ])
        df.to_csv(bad_sedici, index=False)

        state: State = {
            "source_csv_path": valid_source_csv,
            "repository_csv_path": str(bad_sedici),
        }

        with pytest.raises(ValueError, match="columna de título SEDICI"):
            validate_input_csvs_node(state)

        assert "ValidateInputCSVs" in state.get("node_errors", {})

    def test_sedici_csv_missing_identifier_raises_value_error(self, valid_source_csv, tmp_path):
        """Si repository_csv_path no contiene identificador SEDICI (dc.identifier.uri, etc.), lanza ValueError."""
        bad_sedici = tmp_path / "sedici_no_id.csv"
        df = pd.DataFrame([
            {"id": "1", "dc.title": "Sample Title", "date": "2024"}
        ])
        df.to_csv(bad_sedici, index=False)

        state: State = {
            "source_csv_path": valid_source_csv,
            "repository_csv_path": str(bad_sedici),
        }

        with pytest.raises(ValueError, match="columna de identificador SEDICI"):
            validate_input_csvs_node(state)

        assert "ValidateInputCSVs" in state.get("node_errors", {})

    def test_missing_habitual_columns_logs_warning(self, tmp_path, caplog):
        """Si faltan columnas habituales ('author', 'date', 'issn', 'type'), se emite logger.warning."""
        csv_file = tmp_path / "minimal.csv"
        df = pd.DataFrame([
            {"id": "1", "title": "Minimal Title"}
        ])
        df.to_csv(csv_file, index=False)

        state: State = {"source_csv_path": str(csv_file)}

        with caplog.at_level(logging.WARNING):
            validate_input_csvs_node(state)

        assert any("Columnas habituales ausentes" in record.message for record in caplog.records)

    def test_skip_validation_on_pdf_ingest_errors(self, tmp_path):
        """Si ya existen errores previos en PDFIngest, la validación se omite sin lanzar excepción."""
        empty_file = tmp_path / "source_from_pdfs.csv"
        empty_file.write_text("")

        state: State = {
            "source_csv_path": str(empty_file),
            "node_errors": {"PDFIngest_errors": "Error de conexión MinIO"},
        }

        updates = validate_input_csvs_node(state)
        assert updates == {}
