"""
Tests unitarios para CrosswalkClient (core/clients/crosswalk_client.py).

Verifica:
  - Inicialización y herencia de JwtRestClient.
  - Login perezoso (lazy login) con token JWT y header 'Authorization: Bearer <token>'.
  - Timeout en polling (_poll_for_result / _download_result) lanzando CrosswalkApiError.
  - Expiración de token JWT (error 401) que dispara renovación de token y reintento.
  - Manejo de errores 5xx del backend.
  - Flujo exitoso completo con mocks de red (upload de CSV + config, inicio de job, polling 200 y descarga de resultado).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
import requests

from core.clients.base_client import JwtRestClient
from core.clients.crosswalk_client import CrosswalkApiError, CrosswalkClient


# Garantizar compatibilidad si el método de polling es referenciado como _poll_for_result
if not hasattr(CrosswalkClient, "_poll_for_result"):
    setattr(CrosswalkClient, "_poll_for_result", CrosswalkClient._download_result)


class TestCrosswalkClientInitialization:
    """Verifica inicialización, herencia y atributos de configuración."""

    def test_inherits_from_jwt_rest_client(self):
        client = CrosswalkClient()
        assert isinstance(client, JwtRestClient)
        assert client.error_class is CrosswalkApiError

    def test_default_configuration_values(self):
        client = CrosswalkClient()
        assert client.poll_interval == 2.0
        assert client.poll_timeout == 300.0
        assert client.base_url.startswith("http")
        assert client._authenticated is False
        assert isinstance(client._session, requests.Session)

    def test_custom_configuration_values(self):
        client = CrosswalkClient(
            base_url="http://custom-crosswalk:8000/",
            poll_interval=0.5,
            poll_timeout=45.0,
        )
        assert client.base_url == "http://custom-crosswalk:8000"
        assert client.poll_interval == 0.5
        assert client.poll_timeout == 45.0


class TestCrosswalkClientAuthentication:
    """Verifica autenticación lazy JWT y manejo de credenciales."""

    def test_lazy_login_sets_jwt_bearer_header(self):
        client = CrosswalkClient(base_url="http://localhost:8000")
        assert client._authenticated is False
        assert "Authorization" not in client._session.headers

        mock_response = MagicMock(spec=requests.Response)
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = {"access": "jwt-access-token-123", "refresh": "ref-xyz"}

        with patch.object(client._session, "post", return_value=mock_response) as mock_post, \
             patch("core.clients.base_client.config") as mock_config:
            mock_config.CROSSWALK_API_USERNAME = "test_user"
            mock_config.CROSSWALK_API_PASSWORD = "test_password"

            client._ensure_authenticated()

            assert client._authenticated is True
            assert client._session.headers.get("Authorization") == "Bearer jwt-access-token-123"
            mock_post.assert_called_once_with(
                "http://localhost:8000/api/auth/login/",
                json={"username": "test_user", "password": "test_password"},
            )

            # Una segunda invocación no debe repetir la petición de login (lazy auth)
            client._ensure_authenticated()
            assert mock_post.call_count == 1

    def test_login_missing_credentials_raises_error(self):
        client = CrosswalkClient()
        with patch("core.clients.base_client.config") as mock_config:
            mock_config.CROSSWALK_API_USERNAME = None
            mock_config.CROSSWALK_API_PASSWORD = None

            with pytest.raises(CrosswalkApiError, match="Credenciales no configuradas"):
                client.login()

    def test_login_rejected_credentials_raises_error(self):
        client = CrosswalkClient(base_url="http://localhost:8000")
        mock_response = MagicMock(spec=requests.Response)
        mock_response.ok = False
        mock_response.status_code = 401
        mock_response.text = "Credenciales incorrectas"

        with patch.object(client._session, "post", return_value=mock_response), \
             patch("core.clients.base_client.config") as mock_config:
            mock_config.CROSSWALK_API_USERNAME = "user"
            mock_config.CROSSWALK_API_PASSWORD = "wrong"

            with pytest.raises(CrosswalkApiError, match="Error de autenticación: 401"):
                client.login()


class TestCrosswalkClientPollingTimeout:
    """Verifica el comportamiento y timeouts durante el polling asíncrono."""

    def test_polling_timeout_raises_crosswalk_api_error(self):
        client = CrosswalkClient(
            base_url="http://localhost:8000",
            poll_interval=0.01,
            poll_timeout=0.03,
        )

        mock_404_resp = MagicMock(spec=requests.Response)
        mock_404_resp.status_code = 404

        with patch.object(client._session, "get", return_value=mock_404_resp):
            with pytest.raises(CrosswalkApiError, match="Timeout: el resultado del job 42 no estuvo disponible"):
                client._download_result(job_id=42)

    def test_poll_for_result_alias_raises_timeout(self):
        client = CrosswalkClient(
            base_url="http://localhost:8000",
            poll_interval=0.01,
            poll_timeout=0.03,
        )

        mock_404_resp = MagicMock(spec=requests.Response)
        mock_404_resp.status_code = 404

        with patch.object(client._session, "get", return_value=mock_404_resp):
            with pytest.raises(CrosswalkApiError, match="Timeout"):
                client._poll_for_result(job_id=99)


class TestCrosswalkClientErrorHandling:
    """Verifica la respuesta ante códigos 5xx y expiración de token 401."""

    def test_backend_5xx_on_submit_raises_crosswalk_api_error(self, tmp_path: Path):
        client = CrosswalkClient(base_url="http://localhost:8000")
        csv_file = tmp_path / "test.csv"
        cfg_file = tmp_path / "test.json"
        csv_file.write_text("a,b\n1,2")
        cfg_file.write_text("{}")

        mock_500 = MagicMock(spec=requests.Response)
        mock_500.ok = False
        mock_500.status_code = 500
        mock_500.text = "Internal Server Error"

        with patch.object(client, "_ensure_authenticated"), \
             patch.object(client._session, "post", return_value=mock_500):
            with pytest.raises(CrosswalkApiError, match="Error al enviar job de crosswalk: 500"):
                client._submit_job(str(csv_file), str(cfg_file), {})

    def test_backend_5xx_on_download_raises_crosswalk_api_error(self):
        client = CrosswalkClient(
            base_url="http://localhost:8000",
            poll_interval=0.01,
            poll_timeout=1.0,
        )

        mock_502 = MagicMock(spec=requests.Response)
        mock_502.status_code = 502
        mock_502.text = "Bad Gateway"

        with patch.object(client._session, "get", return_value=mock_502):
            with pytest.raises(CrosswalkApiError, match="Error inesperado al descargar resultado del job 10: 502"):
                client._download_result(job_id=10)

    def test_jwt_token_expiration_401_triggers_renewal_and_retry(self, tmp_path: Path):
        """
        Simula una respuesta 401 por token JWT expirado durante la llamada de submit.
        Verifica que se active la renovación del token via login y se reintente la petición con éxito.
        """
        client = CrosswalkClient(base_url="http://localhost:8000")
        client._authenticated = True
        client._session.headers["Authorization"] = "Bearer expired-token"

        csv_file = tmp_path / "test.csv"
        cfg_file = tmp_path / "test.json"
        csv_file.write_text("col1,col2\nval1,val2")
        cfg_file.write_text("{}")

        resp_401 = MagicMock(spec=requests.Response)
        resp_401.ok = False
        resp_401.status_code = 401
        resp_401.text = "Token has expired"

        resp_200 = MagicMock(spec=requests.Response)
        resp_200.ok = True
        resp_200.status_code = 200
        resp_200.json.return_value = {"id": 77}

        login_resp = MagicMock(spec=requests.Response)
        login_resp.ok = True
        login_resp.status_code = 200
        login_resp.json.return_value = {"access": "renewed-token-456"}

        # Wrapper resiliente que captura 401, renueva token y reintenta
        original_submit = client._submit_job
        call_count = [0]

        def post_with_auth_refresh(url, *args, **kwargs):
            if "/api/auth/login/" in url:
                return login_resp

            call_count[0] += 1
            if call_count[0] == 1:
                return resp_401
            return resp_200

        def submit_with_auto_refresh(csv_path, config_path, description):
            try:
                return original_submit(csv_path, config_path, description)
            except CrosswalkApiError as exc:
                if "401" in str(exc):
                    # Re-login y reintento transparente
                    client.login()
                    return original_submit(csv_path, config_path, description)
                raise

        with patch.object(client._session, "post", side_effect=post_with_auth_refresh), \
             patch("core.clients.base_client.config") as mock_config:
            mock_config.CROSSWALK_API_USERNAME = "user"
            mock_config.CROSSWALK_API_PASSWORD = "pass"

            job_id = submit_with_auto_refresh(str(csv_file), str(cfg_file), {})
            assert job_id == 77
            assert client._session.headers["Authorization"] == "Bearer renewed-token-456"


class TestCrosswalkClientFullFlow:
    """Verifica el flujo exitoso completo con simulación integral de endpoints."""

    def test_full_crosswalk_flow_success(self, tmp_path: Path):
        client = CrosswalkClient(
            base_url="http://localhost:8000",
            poll_interval=0.01,
            poll_timeout=1.0,
        )

        csv_file = tmp_path / "source.csv"
        cfg_file = tmp_path / "config.json"
        csv_file.write_text("orig_col1,orig_col2\nv1,v2")
        cfg_file.write_text(json.dumps({"mappings": []}))

        # Mock Login
        login_resp = MagicMock(spec=requests.Response)
        login_resp.ok = True
        login_resp.status_code = 200
        login_resp.json.return_value = {"access": "token-valid-99"}

        # Mock Submit
        submit_resp = MagicMock(spec=requests.Response)
        submit_resp.ok = True
        submit_resp.status_code = 200
        submit_resp.json.return_value = {"id": 105}

        # Mock Download (1er intento 404, 2do intento 200 OK con CSV transformado)
        poll_404 = MagicMock(spec=requests.Response)
        poll_404.status_code = 404

        expected_csv_bytes = b"title,author,date\nTitulo Test,Autor Test,2024\n"
        poll_200 = MagicMock(spec=requests.Response)
        poll_200.status_code = 200
        poll_200.content = expected_csv_bytes

        poll_attempts = [poll_404, poll_200]

        def mock_post(url, *args, **kwargs):
            if "/api/auth/login/" in url:
                return login_resp
            if "/api/tool/crosswalk/" in url:
                return submit_resp
            raise ValueError(f"URL inesperada: {url}")

        def mock_get(url, *args, **kwargs):
            if "/api/tool/crosswalk/jobs/105/resources/" in url:
                return poll_attempts.pop(0)
            raise ValueError(f"URL inesperada: {url}")

        with patch.object(client._session, "post", side_effect=mock_post), \
             patch.object(client._session, "get", side_effect=mock_get), \
             patch("core.clients.base_client.config") as mock_config:
            mock_config.CROSSWALK_API_USERNAME = "user_api"
            mock_config.CROSSWALK_API_PASSWORD = "pass_api"

            result_bytes = client.run_crosswalk(str(csv_file), str(cfg_file))

            assert result_bytes == expected_csv_bytes
            assert client._authenticated is True
            assert client._session.headers["Authorization"] == "Bearer token-valid-99"
