"""
Cliente HTTP para la API REST del servicio de Crosswalk.

Encapsula las llamadas a los endpoints de /api/tool/crosswalk/ del
backend (Backend-Modulo-Nacho).

Flujo de uso típico:
  1. run_crosswalk() autentica la sesión (lazy JWT) si aún no lo está.
  2. Sube el CSV + config como multipart/form-data.
  3. La API inicia el job y lo procesa en un thread (asíncrono).
  4. Se hace polling descargando el resultado via
     GET /api/tool/crosswalk/jobs/{id}/resources/?resource=results
     hasta obtener 200 OK (el archivo existe cuando el thread termina).

Credenciales (variables de entorno o config):
  CROSSWALK_API_URL       — URL base del backend  (default: http://localhost:8000)
  CROSSWALK_API_USERNAME  — Usuario para el login JWT
  CROSSWALK_API_PASSWORD  — Contraseña para el login JWT
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

from core.clients.base_client import JwtRestClient, JwtRestClientError

logger = logging.getLogger(__name__)


class CrosswalkApiError(JwtRestClientError):
    """Excepción lanzada cuando la API de crosswalk retorna un error."""
    pass


class CrosswalkClient(JwtRestClient):
    """
    Cliente para el servicio REST de Crosswalk con autenticación JWT.

    Hereda de JwtRestClient el manejo de sesión y autenticación lazy.
    Solo implementa la lógica específica del dominio de crosswalk:
      - Envío del CSV + config al endpoint POST /api/tool/crosswalk/.
      - Polling por existencia del archivo resultado.
      - Descarga del CSV resultante.

    Attributes:
        base_url:      URL base del backend (ej. http://localhost:8000).
        poll_interval: Segundos entre cada intento de descarga del resultado.
        poll_timeout:  Tiempo máximo de espera total en segundos.
    """

    @property
    def error_class(self) -> type[CrosswalkApiError]:
        return CrosswalkApiError

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def run_crosswalk(
        self,
        csv_path: str,
        config_path: str,
        description: Optional[dict] = None,
    ) -> bytes:
        """
        Ejecuta un job de crosswalk completo y devuelve el CSV resultante.

        Autentica la sesión si todavía no lo está (lazy auth), sube el CSV
        y el config JSON al backend, espera a que el thread del servidor
        escriba el archivo resultado y lo descarga.

        Args:
            csv_path:    Path al CSV fuente a transformar.
            config_path: Path al JSON de crosswalk config.
            description: Diccionario opcional de descripción del job.

        Returns:
            Contenido del CSV transformado como bytes.

        Raises:
            CrosswalkApiError: Si la API retorna un error o el job falla.
            FileNotFoundError: Si alguno de los archivos no existe.
        """
        self._ensure_authenticated()
        job_id = self._submit_job(csv_path, config_path, description or {})
        return self._download_result(job_id)

    # ------------------------------------------------------------------
    # Métodos internos
    # ------------------------------------------------------------------

    def _submit_job(self, csv_path: str, config_path: str, description: dict) -> int:
        """
        Sube el CSV y el config al endpoint POST /api/tool/crosswalk/.

        Returns:
            El id del CrosswalkJob creado.

        Raises:
            CrosswalkApiError: Si el servidor responde con un error.
        """
        url = self._url("/api/tool/crosswalk/")

        with open(csv_path, "rb") as csv_file, open(config_path, "rb") as config_file:
            files = {
                "csv":    (csv_path.split("/")[-1],    csv_file,    "text/csv"),
                "config": (config_path.split("/")[-1], config_file, "application/json"),
            }
            data = {"description": json.dumps(description)}

            logger.debug("[CrosswalkClient] POST %s", url)
            response = self._session.post(url, files=files, data=data)

        if not response.ok:
            raise CrosswalkApiError(
                f"Error al enviar job de crosswalk: {response.status_code} — {response.text}"
            )

        job_data = response.json()
        job_id = job_data.get("id")
        if not job_id:
            raise CrosswalkApiError(
                f"La respuesta del servidor no contiene 'id': {job_data}"
            )

        return job_id

    def _download_result(self, job_id: int) -> bytes:
        """
        Hace polling intentando descargar el CSV resultado hasta obtener 200 OK.

        El thread del servidor escribe `result.csv` al terminar el crosswalk.
        Cuando el archivo existe, la API responde 200. Un 404 indica que el
        thread aún no terminó y se reintenta. Cualquier otro código es un fallo.

        Args:
            job_id: ID del job a monitorear.

        Returns:
            Contenido del CSV transformado como bytes.

        Raises:
            CrosswalkApiError: Si se supera el timeout o la API retorna
                               un error distinto de 404.
        """
        poll_url = self._url(f"/api/tool/crosswalk/jobs/{job_id}/resources/")
        params = {"resource": "results"}
        elapsed = 0.0

        while elapsed < self.poll_timeout:
            time.sleep(self.poll_interval)
            elapsed += self.poll_interval

            response = self._session.get(poll_url, params=params)

            if response.status_code == 200:
                logger.info(
                    "[CrosswalkClient] Job %d completado (%.1fs). Descargando resultado.",
                    job_id, elapsed,
                )
                return response.content

            if response.status_code == 404:
                # El thread aún no escribió el archivo resultado
                continue

            raise CrosswalkApiError(
                f"Error inesperado al descargar resultado del job {job_id}: "
                f"{response.status_code} — {response.text}"
            )

        raise CrosswalkApiError(
            f"Timeout: el resultado del job {job_id} no estuvo disponible "
            f"en {self.poll_timeout}s."
        )
