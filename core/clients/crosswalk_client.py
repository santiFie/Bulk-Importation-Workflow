"""
Cliente HTTP para la API REST del servicio de Crosswalk.

Encapsula las llamadas a los endpoints de /api/tool/crosswalk/ del
backend (Backend-Modulo-Nacho), reemplazando el uso del script local
core/scripts/crosswalk/.

Flujo de uso típico:
  1. login() obtiene un par de tokens JWT (access + refresh) del endpoint
     POST /api/auth/login/ y los inyecta en la sesión HTTP.
  2. run_crosswalk() autentica la sesión si aún no lo está (lazy auth),
     luego sube el CSV + config como multipart/form-data.
  3. La API inicia el job y lo procesa en un thread (asíncrono).
  4. Se hace polling intentando descargar el resultado directamente via
     GET /api/tool/crosswalk/jobs/{id}/resources/?resource=results hasta
     obtener 200 OK (el archivo existe cuando el thread termina).

Credenciales (variables de entorno o config):
  CROSSWALK_API_URL       — URL base del backend  (default: http://localhost:8000)
  CROSSWALK_API_USERNAME  — Usuario para el login JWT
  CROSSWALK_API_PASSWORD  — Contraseña para el login JWT
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
    """Devuelve la URL base del backend, configurable via CROSSWALK_API_URL."""
    return getattr(config, "CROSSWALK_API_URL", _DEFAULT_BASE_URL).rstrip("/")


class CrosswalkApiError(Exception):
    """Excepción lanzada cuando la API de crosswalk retorna un error."""
    pass


class CrosswalkClient:
    """
    Cliente para el servicio REST de Crosswalk con autenticación JWT.

    Encapsula la lógica de:
      - Autenticación lazy via JWT (POST /api/auth/login/).
      - Envío del CSV + config al endpoint POST /api/tool/crosswalk/.
      - Polling del resultado hasta que el archivo esté disponible.
      - Descarga del CSV resultante.

    La autenticación es lazy: el token se obtiene automáticamente antes
    del primer request real y se reutiliza en toda la sesión HTTP.

    Attributes:
        base_url:      URL base del backend (ej. http://localhost:8000).
        poll_interval: Segundos entre cada intento de descarga del resultado.
        poll_timeout:  Tiempo máximo de espera total en segundos.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        poll_interval: float = 2.0,
        poll_timeout: float = 300.0,
    ) -> None:
        self.base_url = (base_url or _get_base_url()).rstrip("/")
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self._session = requests.Session()
        self._authenticated = False

    def _url(self, path: str) -> str:
        """Construye la URL completa para un path dado."""
        return f"{self.base_url}{path}"

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def login(self) -> None:
        """
        Obtiene un par de tokens JWT y los inyecta en la sesión HTTP.

        Lee las credenciales desde el objeto `config` del proyecto:
          - CROSSWALK_API_USERNAME
          - CROSSWALK_API_PASSWORD

        Una vez autenticado, todos los requests posteriores de la sesión
        incluirán automáticamente el header `Authorization: Bearer <token>`.

        Raises:
            CrosswalkApiError: Si las credenciales son inválidas o el
                               servidor no responde correctamente.
        """
        username = getattr(config, "CROSSWALK_API_USERNAME", None)
        password = getattr(config, "CROSSWALK_API_PASSWORD", None)

        if not username or not password:
            raise CrosswalkApiError(
                "Credenciales no configuradas. Definí CROSSWALK_API_USERNAME y "
                "CROSSWALK_API_PASSWORD en el archivo .env o en la configuración."
            )

        url = self._url("/api/auth/login/")
        logger.debug("[CrosswalkClient] Autenticando con usuario '%s'", username)

        response = self._session.post(
            url,
            json={"username": username, "password": password},
        )

        if not response.ok:
            raise CrosswalkApiError(
                f"Error de autenticación: {response.status_code} — {response.text}"
            )

        tokens = response.json()
        access_token = tokens.get("access")
        if not access_token:
            raise CrosswalkApiError(
                f"La respuesta de login no contiene 'access': {tokens}"
            )

        self._session.headers.update({"Authorization": f"Bearer {access_token}"})
        self._authenticated = True
        logger.info("[CrosswalkClient] Autenticación exitosa.")

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
        if not self._authenticated:
            self.login()

        job_id = self._submit_job(csv_path, config_path, description or {})

        return self._wait_and_download(job_id)

    # ------------------------------------------------------------------
    # Métodos internos
    # ------------------------------------------------------------------

    def _submit_job(
        self,
        csv_path: str,
        config_path: str,
        description: dict,
    ) -> int:
        """
        Sube el CSV y el config al endpoint POST /api/tool/crosswalk/.

        Args:
            csv_path:    Path al CSV fuente.
            config_path: Path al JSON de configuración.
            description: Diccionario de descripción del job.

        Returns:
            El id del CrosswalkJob creado.

        Raises:
            CrosswalkApiError: Si el servidor responde con un error.
        """
        url = self._url("/api/tool/crosswalk/")

        with open(csv_path, "rb") as csv_file, open(config_path, "rb") as config_file:
            files = {
                "csv": (csv_path.split("/")[-1], csv_file, "text/csv"),
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

    def _wait_and_download(self, job_id: int) -> bytes:
        """
        Hace polling intentando descargar el CSV resultado hasta obtener 200 OK.

        El thread del servidor escribe `result.csv` al terminar el crosswalk.
        Se intenta descargarlo periódicamente: cuando el archivo existe, la
        API responde 200 y se retornan los bytes. Si la respuesta es 404,
        el thread aún no terminó y se reintenta.

        Esta estrategia reemplaza el polling por `progress == 100` dado que
        el backend simplificado ya no actualiza ese campo.

        Args:
            job_id: ID del job a monitorear.

        Returns:
            Contenido del CSV transformado como bytes.

        Raises:
            CrosswalkApiError: Si se supera el timeout o la API retorna
                               un error distinto de 404.
        """
        url = self._url(f"/api/tool/crosswalk/jobs/{job_id}/resources/")
        elapsed = 0.0

        while elapsed < self.poll_timeout:
            time.sleep(self.poll_interval)
            elapsed += self.poll_interval

            response = self._session.get(url, params={"resource": "results"})

            if response.status_code == 200:
                print(f"[CrosswalkClient] Job {job_id} completado ({elapsed:.1f}s). Descargando resultado.")
                return response.content

            if response.status_code == 404:
                # El thread del servidor aún no escribió el archivo resultado
                continue

            # Cualquier otro código de error es un fallo real
            raise CrosswalkApiError(
                f"Error inesperado al descargar resultado del job {job_id}: "
                f"{response.status_code} — {response.text}"
            )

        raise CrosswalkApiError(
            f"Timeout: el resultado del job {job_id} no estuvo disponible "
            f"en {self.poll_timeout}s."
        )
