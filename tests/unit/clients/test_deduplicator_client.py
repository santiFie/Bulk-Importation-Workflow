"""
Tests unitarios para DeduplicatorClient (core/clients/deduplicator_client.py).

Verifica:
  - Inicialización y herencia de JwtRestClient.
  - Autenticación lazy con inyección del header 'Authorization: Bearer <token>'.
  - Manejo de errores de conexión y respuestas no-200 (códigos 4xx, 5xx, estados FAILED y CANCELLED).
  - Timeout en polling de deduplicación lanzando DeduplicatorApiError.
  - Ejecución exitosa completa con mocks de red (upload de CSVs, polling FINISHED y descarga de resultados).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
import requests

from core.clients.base_client import JwtRestClient
from core.clients.deduplicator_client import DeduplicatorApiError, DeduplicatorClient


class TestDeduplicatorClientInitialization:
    """Verifica herencia, atributos por defecto y parametrización."""

    def test_inherits_from_jwt_rest_client(self):
        client = DeduplicatorClient()
        assert isinstance(client, JwtRestClient)
        assert client.error_class is DeduplicatorApiError

    def test_default_configuration_values(self):
        client = DeduplicatorClient()
        assert client.poll_interval == 2.0
        assert client.poll_timeout == 360.0
        assert client._authenticated is False
        assert isinstance(client._session, requests.Session)

    def test_custom_configuration_values(self):
        client = DeduplicatorClient(
            base_url="http://dedup-srv:8000/",
            poll_interval=1.0,
            poll_timeout=60.0,
        )
        assert client.base_url == "http://dedup-srv:8000"
        assert client.poll_interval == 1.0
        assert client.poll_timeout == 60.0


class TestDeduplicatorClientAuthentication:
    """Verifica el flujo de login y manejo de credenciales."""

    def test_lazy_login_sets_auth_bearer_header(self):
        client = DeduplicatorClient(base_url="http://localhost:8000")
        assert client._authenticated is False

        mock_login_resp = MagicMock(spec=requests.Response)
        mock_login_resp.ok = True
        mock_login_resp.status_code = 200
        mock_login_resp.json.return_value = {"access": "dedup-jwt-token-abc"}

        with patch.object(client._session, "post", return_value=mock_login_resp) as mock_post, \
             patch("core.clients.base_client.config") as mock_config:
            mock_config.CROSSWALK_API_USERNAME = "dedup_user"
            mock_config.CROSSWALK_API_PASSWORD = "dedup_password"

            client._ensure_authenticated()

            assert client._authenticated is True
            assert client._session.headers.get("Authorization") == "Bearer dedup-jwt-token-abc"
            mock_post.assert_called_once_with(
                "http://localhost:8000/api/auth/login/",
                json={"username": "dedup_user", "password": "dedup_password"},
            )

            # Lazy auth: no debe volver a autenticar si ya lo está
            client._ensure_authenticated()
            assert mock_post.call_count == 1

    def test_login_missing_credentials_raises_deduplicator_api_error(self):
        client = DeduplicatorClient()
        with patch("core.clients.base_client.config") as mock_config:
            mock_config.CROSSWALK_API_USERNAME = None
            mock_config.CROSSWALK_API_PASSWORD = None

            with pytest.raises(DeduplicatorApiError, match="Credenciales no configuradas"):
                client.login()


class TestDeduplicatorClientErrorHandling:
    """Verifica manejo de errores de conexión, códigos no-200 y fallas de job."""

    def test_connection_error_on_submit_propagates(self, tmp_path: Path):
        client = DeduplicatorClient(base_url="http://localhost:8000")
        f1 = tmp_path / "f1.csv"
        f2 = tmp_path / "f2.csv"
        f1.write_text("a,b\n1,2")
        f2.write_text("a,b\n1,2")

        with patch.object(client, "_ensure_authenticated"), \
             patch.object(client._session, "post", side_effect=requests.exceptions.ConnectionError("Connection refused")):
            with pytest.raises(requests.exceptions.ConnectionError):
                client._submit_job(str(f1), str(f2), "test_source", multithread=False)

    def test_non_200_response_on_submit_raises_deduplicator_api_error(self, tmp_path: Path):
        client = DeduplicatorClient(base_url="http://localhost:8000")
        f1 = tmp_path / "f1.csv"
        f2 = tmp_path / "f2.csv"
        f1.write_text("a,b\n1,2")
        f2.write_text("a,b\n1,2")

        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.ok = False
        mock_resp.status_code = 400
        mock_resp.text = "Bad Request: CSVs malformados"

        with patch.object(client, "_ensure_authenticated"), \
             patch.object(client._session, "post", return_value=mock_resp):
            with pytest.raises(DeduplicatorApiError, match="Error al enviar deduplicación: 400"):
                client._submit_job(str(f1), str(f2), "test_source", multithread=False)

    def test_non_200_response_on_status_polling_raises_deduplicator_api_error(self):
        client = DeduplicatorClient(base_url="http://localhost:8000", poll_interval=0.01)

        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.ok = False
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        with patch.object(client._session, "get", return_value=mock_resp):
            with pytest.raises(DeduplicatorApiError, match="Error al consultar estado del job 55: 500"):
                client._wait_for_job(job_id=55)

    def test_non_200_response_on_download_results_raises_deduplicator_api_error(self):
        client = DeduplicatorClient(base_url="http://localhost:8000")

        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.ok = False
        mock_resp.status_code = 404
        mock_resp.text = "Resultado no encontrado"

        with patch.object(client._session, "get", return_value=mock_resp):
            with pytest.raises(DeduplicatorApiError, match="Error al descargar resultados: 404"):
                client._download_results(job_id=55)

    def test_job_failed_status_raises_deduplicator_api_error(self):
        client = DeduplicatorClient(base_url="http://localhost:8000", poll_interval=0.01)

        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "status": "FAILED",
            "progress": 25.0,
            "observations": "Error de memoria en el clustering TF-IDF",
        }

        with patch.object(client._session, "get", return_value=mock_resp):
            with pytest.raises(DeduplicatorApiError, match="falló en el servidor: Error de memoria"):
                client._wait_for_job(job_id=88)

    def test_job_cancelled_status_raises_deduplicator_api_error(self):
        client = DeduplicatorClient(base_url="http://localhost:8000", poll_interval=0.01)

        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "status": "CANCELLED",
            "progress": 10.0,
        }

        with patch.object(client._session, "get", return_value=mock_resp):
            with pytest.raises(DeduplicatorApiError, match="fue cancelado"):
                client._wait_for_job(job_id=99)


class TestDeduplicatorClientPollingTimeout:
    """Verifica el timeout durante el polling de deduplicación."""

    def test_polling_timeout_raises_deduplicator_api_error(self):
        client = DeduplicatorClient(
            base_url="http://localhost:8000",
            poll_interval=0.01,
            poll_timeout=0.03,
        )

        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.ok = True
        mock_resp.json.return_value = {"status": "IN_PROGRESS", "progress": 50.0}

        with patch.object(client._session, "get", return_value=mock_resp):
            with pytest.raises(DeduplicatorApiError, match="Timeout de espera del job 77"):
                client._wait_for_job(job_id=77)


class TestDeduplicatorClientFullFlow:
    """Verifica la ejecución exitosa completa con simulación de toda la secuencia."""

    def test_full_successful_execution_with_mocks(self, tmp_path: Path):
        client = DeduplicatorClient(
            base_url="http://localhost:8000",
            poll_interval=0.01,
            poll_timeout=1.0,
        )

        sedici_csv = tmp_path / "sedici_input.csv"
        source_csv = tmp_path / "source_input.csv"
        sedici_csv.write_text("id,title,author\n1,Titulo Sedici,Autor A")
        source_csv.write_text("id,title,author\n2,Titulo Origen,Autor B")

        # Mock Login
        login_resp = MagicMock(spec=requests.Response)
        login_resp.ok = True
        login_resp.status_code = 200
        login_resp.json.return_value = {"access": "valid-dedup-token"}

        # Mock Submit
        submit_resp = MagicMock(spec=requests.Response)
        submit_resp.ok = True
        submit_resp.status_code = 200
        submit_resp.json.return_value = {"id": 120}

        # Mock Status Polling: primero IN_PROGRESS, luego FINISHED
        poll_in_progress = MagicMock(spec=requests.Response)
        poll_in_progress.ok = True
        poll_in_progress.json.return_value = {"status": "IN_PROGRESS", "progress": 40.0}

        poll_finished = MagicMock(spec=requests.Response)
        poll_finished.ok = True
        poll_finished.json.return_value = {"status": "FINISHED", "progress": 100.0}

        status_sequence = [poll_in_progress, poll_finished]

        # Mock Download Results
        expected_results = b"id_source,id_sedici,dedup_status\n2,,UNIQUE\n"
        results_resp = MagicMock(spec=requests.Response)
        results_resp.ok = True
        results_resp.status_code = 200
        results_resp.content = expected_results

        def mock_post(url, *args, **kwargs):
            if "/api/auth/login/" in url:
                return login_resp
            if "/api/tool/deduplicator/" in url:
                return submit_resp
            raise ValueError(f"URL inesperada: {url}")

        def mock_get(url, *args, **kwargs):
            if "/api/tool/deduplicator/jobs/120/results/" in url:
                return results_resp
            if "/api/tool/deduplicator/jobs/120/" in url:
                return status_sequence.pop(0)
            raise ValueError(f"URL inesperada: {url}")

        with patch.object(client._session, "post", side_effect=mock_post), \
             patch.object(client._session, "get", side_effect=mock_get), \
             patch("core.clients.base_client.config") as mock_config:
            mock_config.CROSSWALK_API_USERNAME = "dedup_user"
            mock_config.CROSSWALK_API_PASSWORD = "dedup_pass"

            results = client.detect_duplicates(
                csv_file1_path=str(sedici_csv),
                csv_file2_path=str(source_csv),
                source_name="unlp_springer",
                multithread=True,
            )

            assert results == expected_results
            assert client._authenticated is True
            assert client._session.headers["Authorization"] == "Bearer valid-dedup-token"
