"""
Cliente HTTP para la API REST del servicio de Deduplicador.

Se comunica con los endpoints de /api/tool/deduplicator/ en el backend
(Backend-Modulo-Nacho).

Flujo de uso típico:
  1. detect_duplicates() autentica la sesión (lazy JWT) si aún no lo está.
  2. POST /api/tool/deduplicator/ para iniciar la deduplicación.
  3. Polling de GET /api/tool/deduplicator/jobs/{id}/ hasta que status == 'FINISHED'.
  4. Descarga del CSV resultado via GET /api/tool/deduplicator/jobs/{id}/results/.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

from core.clients.base_client import JwtRestClient, JwtRestClientError

logger = logging.getLogger(__name__)

# Valores de estado que reporta el backend para los jobs de deduplicación
_STATUS_FINISHED  = "FINISHED"
_STATUS_FAILED    = "FAILED"
_STATUS_CANCELLED = "CANCELLED"


class DeduplicatorApiError(JwtRestClientError):
    """Excepción lanzada ante errores con la API de deduplicación."""
    pass


class DeduplicatorClient(JwtRestClient):
    """
    Cliente para el servicio de Deduplicador utilizando la API REST.

    Hereda de JwtRestClient el manejo de sesión y autenticación lazy.
    Solo implementa la lógica específica del dominio de deduplicación:
      - Envío de los dos CSVs al endpoint POST /api/tool/deduplicator/.
      - Polling por estado del job hasta FINISHED.
      - Descarga del CSV resultado.

    Attributes:
        base_url:      URL base del backend (ej. http://localhost:8000).
        poll_interval: Segundos entre cada intento de polling.
        poll_timeout:  Tiempo máximo de espera total en segundos.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        poll_interval: float = 2.0,
        poll_timeout: float = 360.0,
    ) -> None:
        super().__init__(base_url=base_url, poll_interval=poll_interval, poll_timeout=poll_timeout)

    @property
    def error_class(self) -> type[DeduplicatorApiError]:
        return DeduplicatorApiError

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def detect_duplicates(
        self,
        csv_file1_path: str,
        csv_file2_path: str,
        source_name: str,
        multithread: bool = False,
    ) -> bytes:
        """
        Ejecuta el proceso completo de deduplicación y retorna el CSV de resultados en bytes.

        Args:
            csv_file1_path: Path al CSV de SEDICI en formato genérico.
            csv_file2_path: Path al CSV de origen en formato genérico.
            source_name:    Nombre del repositorio origen (para descripción del job).
            multithread:    Si el servidor debe usar procesamiento multihilo.

        Returns:
            Contenido del CSV de resultados de deduplicación como bytes.
        """
        self._ensure_authenticated()
        job_id = self._submit_job(csv_file1_path, csv_file2_path, source_name, multithread)
        logger.info("[DeduplicatorClient] Job de deduplicación %s iniciado.", job_id)

        self._wait_for_job(job_id)
        logger.info("[DeduplicatorClient] Job %s completado. Descargando resultados.", job_id)

        return self._download_results(job_id)

    # ------------------------------------------------------------------
    # Métodos internos
    # ------------------------------------------------------------------

    def _submit_job(
        self,
        csv1_path: str,
        csv2_path: str,
        source_name: str,
        multithread: bool,
    ) -> int:
        """Sube los dos CSVs al endpoint POST /api/tool/deduplicator/."""
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
        """
        Hace polling sobre el estado del job hasta que termine (FINISHED/FAILED/CANCELLED).

        Raises:
            DeduplicatorApiError: Si el job falla, fue cancelado, o se supera el timeout.
        """
        status_url = self._url(f"/api/tool/deduplicator/jobs/{job_id}/")
        elapsed = 0.0

        while elapsed < self.poll_timeout:
            time.sleep(self.poll_interval)
            elapsed += self.poll_interval

            response = self._session.get(status_url)
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

            if status == _STATUS_FINISHED:
                return

            if status == _STATUS_FAILED:
                raise DeduplicatorApiError(
                    f"El proceso de deduplicación {job_id} falló en el servidor: "
                    f"{job_data.get('observations')}"
                )

            if status == _STATUS_CANCELLED:
                raise DeduplicatorApiError(
                    f"El proceso de deduplicación {job_id} fue cancelado."
                )

        raise DeduplicatorApiError(
            f"Timeout de espera del job {job_id} ({self.poll_timeout}s)."
        )

    def _download_results(self, job_id: int) -> bytes:
        """Descarga el CSV de resultados del job finalizado."""
        url = self._url(f"/api/tool/deduplicator/jobs/{job_id}/results/")
        response = self._session.get(url)

        if not response.ok:
            raise DeduplicatorApiError(
                f"Error al descargar resultados: {response.status_code} — {response.text}"
            )

        return response.content
