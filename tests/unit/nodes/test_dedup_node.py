"""
Tests unitarios para el nodo Deduplicate (core/nodes/dedup_node.py).
"""

import os
from unittest.mock import MagicMock, patch
import pytest

from core.nodes.dedup_node import deduplicate, _log_csv_columns


class TestDeduplicateNode:
    """Verifica la invocación aislada del cliente de deduplicación y el guardado de resultados."""

    def test_deduplicate_success(self, tmp_path):
        sedici_csv = tmp_path / "generic_sedici.csv"
        sedici_csv.write_text("id,title,author\nsedici_1,Articulo SEDICI,Perez")
        source_csv = tmp_path / "generic_source.csv"
        source_csv.write_text("id,title,author\nsource_1,Articulo Fuente,Perez")
        dedup_output = tmp_path / "dedup.csv"

        state = {
            "source_name": "mi_fuente",
            "generic_sedici_csv_path": str(sedici_csv),
            "generic_source_csv_path": str(source_csv),
            "dedup_output_csv_path": str(dedup_output),
        }

        expected_bytes = (
            b"id_document1,id_document2,similarity\n"
            b"source_1,sedici_1,95.0\n"
        )

        with patch("core.nodes.dedup_node.DeduplicatorClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.detect_duplicates.return_value = expected_bytes
            mock_client_cls.return_value = mock_client

            result = deduplicate(state)

            assert result == {}
            mock_client.detect_duplicates.assert_called_once_with(
                csv_file1_path=str(sedici_csv),
                csv_file2_path=str(source_csv),
                source_name="mi_fuente",
            )
            assert os.path.isfile(dedup_output)
            with open(dedup_output, "rb") as fh:
                assert fh.read() == expected_bytes

    def test_log_csv_columns_handles_unreadable_file(self, tmp_path):
        """No debe lanzar excepción si un CSV no se puede leer para loguear columnas."""
        non_existent = str(tmp_path / "no_existe.csv")
        # Debe capturar y advertir en logger sin crashear
        _log_csv_columns(non_existent, "TEST")

    def test_deduplicate_client_error_propagates(self, tmp_path):
        sedici_csv = tmp_path / "generic_sedici.csv"
        sedici_csv.write_text("id,title\n1,A")
        source_csv = tmp_path / "generic_source.csv"
        source_csv.write_text("id,title\n2,B")

        state = {
            "source_name": "test",
            "generic_sedici_csv_path": str(sedici_csv),
            "generic_source_csv_path": str(source_csv),
            "dedup_output_csv_path": str(tmp_path / "out.csv"),
        }

        with patch("core.nodes.dedup_node.DeduplicatorClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.detect_duplicates.side_effect = ConnectionError("Deduplicator backend down")
            mock_client_cls.return_value = mock_client

            with pytest.raises(ConnectionError, match="Deduplicator backend down"):
                deduplicate(state)
