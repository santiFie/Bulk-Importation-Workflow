"""
Tests unitarios para los nodos del subgrafo de Ingesta (core/nodes/ingest_nodes.py).
"""

import os
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest

from core.nodes.ingest_nodes import pdf_ingest_node, route_input_source


class TestRouteInputSource:
    """Verifica la función de enrutamiento condicional."""

    def test_route_input_source_defaults_to_csv(self):
        assert route_input_source({}) == "csv"
        assert route_input_source({"input_source_type": "csv"}) == "csv"

    def test_route_input_source_pdf_minio(self):
        assert route_input_source({"input_source_type": "pdf_minio"}) == "pdf_minio"


class TestPdfIngestNode:
    """Verifica el nodo pdf_ingest_node de forma aislada."""

    @pytest.mark.asyncio
    async def test_missing_bucket_raises_value_error(self, tmp_path):
        """Si minio_bucket no está en el state, debe lanzar ValueError."""
        state = {"workspace_dir": str(tmp_path)}
        with pytest.raises(ValueError, match="'minio_bucket' no está definido"):
            await pdf_ingest_node(state)

    @pytest.mark.asyncio
    async def test_bucket_not_found_raises_value_error_and_logs(self, tmp_path):
        """
        Requerimiento del usuario: Si el bucket no existe en MinIO,
        se debe loguear un error y levantar una excepción.
        """
        state = {
            "workspace_dir": str(tmp_path),
            "minio_bucket": "bucket_fantasma",
        }

        with patch("core.nodes.ingest_nodes.Minio") as mock_minio_cls, \
             patch("core.nodes.ingest_nodes.logger.error") as mock_log_error:

            mock_minio_instance = MagicMock()
            mock_minio_instance.bucket_exists.return_value = False
            mock_minio_cls.return_value = mock_minio_instance

            with pytest.raises(ValueError, match="El bucket 'bucket_fantasma' no existe en MinIO"):
                await pdf_ingest_node(state)

            mock_minio_instance.bucket_exists.assert_called_once_with("bucket_fantasma")
            mock_log_error.assert_called()
            log_msg = mock_log_error.call_args[0][0]
            assert "bucket_fantasma" in log_msg and "no existe" in log_msg

    @pytest.mark.asyncio
    async def test_pdf_ingest_success_writes_csv(self, tmp_path):
        """Flujo exitoso: extrae metadatos de los PDFs y genera el CSV."""
        state = {
            "workspace_dir": str(tmp_path),
            "minio_bucket": "importacion",
            "minio_prefix": "lote1/",
        }

        mock_obj1 = MagicMock(object_name="lote1/articulo1.pdf")
        mock_obj2 = MagicMock(object_name="lote1/articulo2.pdf")

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"%PDF-1.4 fake content"

        metadata_payload = {
            "data": {
                "title": "Titulo Simulado",
                "creator": ["Perez, Juan"],
                "director": [],
                "abstract": "Resumen del trabajo",
                "date": "2024",
                "type": "Articulo",
                "subject": "Ciencias",
                "issn": "1234-5678",
                "isbn": "",
                "doi": "10.1000/182",
                "originPlaceInfo": "La Plata",
                "rights": "Creative Commons",
                "rightsurl": "http://cc.org",
            }
        }

        with patch("core.nodes.ingest_nodes.Minio") as mock_minio_cls, \
             patch("core.nodes.ingest_nodes.httpx.post") as mock_httpx_post:

            mock_minio = MagicMock()
            mock_minio.bucket_exists.return_value = True
            mock_minio.list_objects.return_value = [mock_obj1, mock_obj2]
            mock_minio.get_object.return_value = mock_resp
            mock_minio_cls.return_value = mock_minio

            mock_api_resp = MagicMock()
            mock_api_resp.raise_for_status.return_value = None
            mock_api_resp.json.return_value = metadata_payload
            mock_httpx_post.return_value = mock_api_resp

            result = await pdf_ingest_node(state)

            expected_csv = os.path.join(str(tmp_path), "source_from_pdfs.csv")
            assert result["source_csv_path"] == expected_csv
            assert os.path.isfile(expected_csv)

            df = pd.read_csv(expected_csv)
            assert len(df) == 2
            assert df.iloc[0]["title"] == "Titulo Simulado"
            assert df.iloc[0]["author"] == "Perez, Juan"
            assert df.iloc[0]["doi"] == "10.1000/182"

    @pytest.mark.asyncio
    async def test_empty_bucket_writes_empty_csv(self, tmp_path):
        """Si no hay archivos PDF en el bucket, genera un CSV vacío sin fallar."""
        state = {
            "workspace_dir": str(tmp_path),
            "minio_bucket": "importacion",
        }

        with patch("core.nodes.ingest_nodes.Minio") as mock_minio_cls:
            mock_minio = MagicMock()
            mock_minio.bucket_exists.return_value = True
            mock_minio.list_objects.return_value = []
            mock_minio_cls.return_value = mock_minio

            result = await pdf_ingest_node(state)

            expected_csv = os.path.join(str(tmp_path), "source_from_pdfs.csv")
            assert result["source_csv_path"] == expected_csv
            assert os.path.isfile(expected_csv)

            df = pd.read_csv(expected_csv)
            assert len(df) == 0
            assert "title" in df.columns

    @pytest.mark.asyncio
    async def test_list_objects_error_handled_cleanly(self, tmp_path):
        """Si list_objects lanza error de red, genera CSV vacío y registra en node_errors."""
        state = {
            "workspace_dir": str(tmp_path),
            "minio_bucket": "importacion",
        }

        with patch("core.nodes.ingest_nodes.Minio") as mock_minio_cls:
            mock_minio = MagicMock()
            mock_minio.bucket_exists.return_value = True
            mock_minio.list_objects.side_effect = RuntimeError("MinIO connection reset")
            mock_minio_cls.return_value = mock_minio

            result = await pdf_ingest_node(state)

            assert "node_errors" in result
            assert "PDFIngest_errors" in result["node_errors"]
            assert "MinIO connection reset" in result["node_errors"]["PDFIngest_errors"]
