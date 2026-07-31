"""
Cliente HTTP para la API REST del servicio de Deduplicador.

Se comunica con los endpoints de /api/tool/deduplicator/ en el backend
(Backend-Modulo-Nacho) reemplazando la integración previa por sockets/MCP.

Flujo de uso típico:
  1. Autenticación lazy via JWT.
  2. POST /api/tool/deduplicator/ para iniciar la deduplicación.
  3. Polling de GET /api/tool/deduplicator/jobs/{id}/ hasta que status == 'FINISHED'.
  4. Descarga del CSV resultado via GET /api/tool/deduplicator/jobs/{id}/results/.
"""

import json
import time
import logging
from typing import Optional

import requests

from core.utils.config import config

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:8000"


def _get_base_url() -> str:
    return getattr(config, "CROSSWALK_API_URL", _DEFAULT_BASE_URL).rstrip("/")


class DeduplicatorApiError(Exception):
    """Excepción lanzada ante errores con la API de deduplicación."""
    pass


class DeduplicatorClient:
    """
    Cliente para el servicio de Deduplicador utilizando la API REST.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        poll_interval: float = 2.0,
        poll_timeout: float = 360.0,
    ) -> None:
        self.base_url = (base_url or _get_base_url()).rstrip("/")
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self._session = requests.Session()
        self._authenticated = False

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def login(self) -> None:
        """Obtiene tokens JWT usando las credenciales globales en la config."""
        username = getattr(config, "CROSSWALK_API_USERNAME", None)
        password = getattr(config, "CROSSWALK_API_PASSWORD", None)

        if not username or not password:
            raise DeduplicatorApiError(
                "Credenciales no configuradas. Definí CROSSWALK_API_USERNAME y "
                "CROSSWALK_API_PASSWORD."
            )

        url = self._url("/api/auth/login/")
        response = self._session.post(
            url,
            json={"username": username, "password": password},
        )

        if not response.ok:
            raise DeduplicatorApiError(
                f"Error al autenticar: {response.status_code} — {response.text}"
            )

        tokens = response.json()
        access_token = tokens.get("access")
        if not access_token:
            raise DeduplicatorApiError("Respuesta de login inválida.")

        self._session.headers.update({"Authorization": f"Bearer {access_token}"})
        self._authenticated = True

    def detect_duplicates(
        self,
        csv_file1_path: str,
        csv_file2_path: str,
        source_name: str,
        multithread: bool = False,
    ) -> bytes:
        """
        Ejecuta el proceso completo de deduplicación y retorna el CSV de resultados en bytes.
        """
        if not self._authenticated:
            self.login()

        job_id = self._submit_job(csv_file1_path, csv_file2_path, source_name, multithread)
        logger.info("[DeduplicatorClient] Job de deduplicación %s iniciado.", job_id)

        self._wait_for_job(job_id)
        logger.info("[DeduplicatorClient] Job %s completado. Descargando resultados.", job_id)

        return self._download_results(job_id)

    def _submit_job(
        self,
        csv1_path: str,
        csv2_path: str,
        source_name: str,
        multithread: bool,
    ) -> int:
        url = self._url("/api/tool/deduplicator/")

        with open(csv1_path, "rb") as f1, open(csv2_path, "rb") as f2:
            files = {
                "csv_file1": (csv1_path.split("/")[-1], f1, "text/csv"),
                "csv_file2": (csv2_path.split("/")[-1], f2, "text/csv"),
            }
            data = {
                "description": json.dumps(f"Deduplicación {source_name}"),
                "multithread": "true" if multithread else "false",
            }
            response = self._session.post(url, files=files, data=data)

        if not response.ok:
            raise DeduplicatorApiError(
                f"Error al enviar deduplicación: {response.status_code} — {response.text}"
            )

        job_data = response.json()
        job_id = job_data.get("id")
        if not job_id:
            raise DeduplicatorApiError("No se obtuvo el ID del job de deduplicación.")

        return job_id

    def _wait_for_job(self, job_id: int) -> None:
        url = self._url(f"/api/tool/deduplicator/jobs/{job_id}/")
        elapsed = 0.0

        while elapsed < self.poll_timeout:
            time.sleep(self.poll_interval)
            elapsed += self.poll_interval

            response = self._session.get(url)
            if not response.ok:
                raise DeduplicatorApiError(
                    f"Error al consultar estado del job {job_id}: "
                    f"{response.status_code} — {response.text}"
                )

            job_data = response.json()
            status = job_data.get("status", "IN_PROGRESS")
            progress = float(job_data.get("progress", 0))

            logger.debug(
                "[DeduplicatorClient] Job %s — status=%s, progress=%.1f%%",
                job_id, status, progress,
            )

            if status == "FINISHED":
                return
            elif status == "FAILED":
                raise DeduplicatorApiError(
                    f"El proceso de deduplicación {job_id} falló en el servidor: {job_data.get('observations')}"
                )
            elif status == "CANCELLED":
                raise DeduplicatorApiError(f"El proceso de deduplicación {job_id} fue cancelado.")

        raise DeduplicatorApiError(
            f"Timeout de espera del job {job_id} ({self.poll_timeout}s)."
        )

    def _download_results(self, job_id: int) -> bytes:
        url = self._url(f"/api/tool/deduplicator/jobs/{job_id}/results/")
        response = self._session.get(url)

        if not response.ok:
            raise DeduplicatorApiError(
                f"Error al descargar resultados: {response.status_code} — {response.text}"
            )

        return response.content
