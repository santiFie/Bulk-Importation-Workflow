"""
Fixtures comunes y configuraciones para los tests unitarios de nodos.
"""

import csv
import os
from unittest.mock import MagicMock
import pytest


@pytest.fixture
def base_state(tmp_path):
    """Estado sintético mínimo para tests de nodos."""
    return {
        "source_name": "test_source",
        "dspace_collection": "123456789/1",
        "workspace_dir": str(tmp_path),
        "import_validate_only": False,
        "input_source_type": "csv",
        "node_errors": {},
    }


@pytest.fixture
def make_csv(tmp_path):
    """Helper fixture para crear archivos CSV temporales rápidamente."""
    def _create(filename: str, rows: list[dict], fieldnames: list[str] | None = None) -> str:
        filepath = os.path.join(tmp_path, filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        if not fieldnames and rows:
            fieldnames = list(rows[0].keys())
        elif not fieldnames:
            fieldnames = ["id", "title"]

        with open(filepath, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        return filepath

    return _create


@pytest.fixture
def mock_crosswalk_client():
    """Mock reusable para CrosswalkClient."""
    mock = MagicMock()
    mock.run_crosswalk.return_value = (
        b"id,title,author\n1,Titulo Articulo,Perez Juan\n"
    )
    return mock


@pytest.fixture
def mock_deduplicator_client():
    """Mock reusable para DeduplicatorClient."""
    mock = MagicMock()
    mock.detect_duplicates.return_value = (
        b"id_document1,id_document2,similarity\n"
        b"doc_source_1,doc_sedici_1,95.5\n"
        b"doc_source_2,doc_sedici_2,5.0\n"
    )
    return mock
