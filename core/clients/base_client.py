"""
Cliente HTTP base con autenticación JWT lazy y polling genérico.

Define la clase `JwtRestClient` como Template Method para los servicios
REST del backend (Crosswalk, Deduplicador). Elimina la duplicación de:
  - Manejo de sesión HTTP con `requests.Session`
  - Autenticación lazy via JWT (POST /api/auth/login/)
  - Lógica de polling hasta completar un job

Los clientes concretos solo implementan los métodos específicos de su dominio.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Callable, Optional

import requests

from core.utils.config import config

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:8000"


class JwtRestClientError(Exception):
    """Excepción base para errores de los clientes REST JWT."""
    pass


class JwtRestClient(ABC):
    """
    Cliente HTTP base con autenticación JWT lazy y soporte de polling.

    Template Method para el flujo común de los servicios REST del backend:
      1. Autenticación lazy: el token JWT se obtiene en el primer request real.
      2. Submit: sube archivos y datos via multipart/form-data.
      3. Polling: espera a que el job del servidor termine.
      4. Download: descarga el resultado del job.

    Las subclases concretan el endpoint de login, las URLs de los jobs y
    la lógica de verificación de completitud.

    Attributes:
        base_url:      URL base del backend (ej. http://localhost:8000).
        poll_interval: Segundos entre cada intento de polling.
        poll_timeout:  Tiempo máximo de espera total en segundos.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        poll_interval: float = 2.0,
        poll_timeout: float = 300.0,
    ) -> None:
        self.base_url = (base_url or self._default_base_url()).rstrip("/")
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self._session = requests.Session()
        self._authenticated = False

    # ------------------------------------------------------------------
    # Métodos abstractos: las subclases definen el dominio específico
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def error_class(self) -> type[JwtRestClientError]:
        """Clase de excepción específica del dominio del cliente."""
        ...

    @property
    def login_endpoint(self) -> str:
        """Endpoint de autenticación JWT. Sobrescribible si difiere."""
        return "/api/auth/login/"

    def _default_base_url(self) -> str:
        """URL base por defecto, configurable via CROSSWALK_API_URL."""
        return getattr(config, "CROSSWALK_API_URL", _DEFAULT_BASE_URL).rstrip("/")

    # ------------------------------------------------------------------
    # API pública: autenticación
    # ------------------------------------------------------------------

    def login(self) -> None:
        """
        Obtiene tokens JWT y los inyecta en la sesión HTTP.

        Lee las credenciales desde el objeto `config` del proyecto:
          - CROSSWALK_API_USERNAME
          - CROSSWALK_API_PASSWORD

        Raises:
            JwtRestClientError: Si las credenciales están ausentes o son inválidas.
        """
        username = getattr(config, "CROSSWALK_API_USERNAME", None)
        password = getattr(config, "CROSSWALK_API_PASSWORD", None)

        if not username or not password:
            raise self.error_class(
                "Credenciales no configuradas. Definí CROSSWALK_API_USERNAME y "
                "CROSSWALK_API_PASSWORD en el archivo .env o en la configuración."
            )

        url = self._url(self.login_endpoint)
        logger.debug("[%s] Autenticando con usuario '%s'", self.__class__.__name__, username)

        response = self._session.post(url, json={"username": username, "password": password})

        if not response.ok:
            raise self.error_class(
                f"Error de autenticación: {response.status_code} — {response.text}"
            )

        access_token = response.json().get("access")
        if not access_token:
            raise self.error_class(
                f"La respuesta de login no contiene 'access': {response.json()}"
            )

        self._session.headers.update({"Authorization": f"Bearer {access_token}"})
        self._authenticated = True
        logger.info("[%s] Autenticación exitosa.", self.__class__.__name__)

    def _ensure_authenticated(self) -> None:
        """Autentica si todavía no se ha hecho (lazy auth)."""
        if not self._authenticated:
            self.login()

    # ------------------------------------------------------------------
    # Métodos auxiliares protegidos
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        """Construye la URL completa para un path dado."""
        return f"{self.base_url}{path}"

    def _poll_until(
        self,
        poll_url: str,
        is_done: Callable[[requests.Response], bool],
        on_error: Callable[[requests.Response], None],
    ) -> requests.Response:
        """
        Hace polling sobre `poll_url` hasta que `is_done` sea verdadero.

        Implementa el loop de espera genérico con timeout. La condición de
        finalización y el manejo de errores son delegados a los callables.

        Args:
            poll_url: URL a consultar repetidamente.
            is_done:  Función que recibe la Response y devuelve True si el job terminó.
            on_error: Función que recibe la Response cuando hay un error real (no transitorio).

        Returns:
            La última Response que hizo que `is_done` sea True.

        Raises:
            JwtRestClientError: Si se supera el timeout.
        """
        elapsed = 0.0
        while elapsed < self.poll_timeout:
            time.sleep(self.poll_interval)
            elapsed += self.poll_interval

            response = self._session.get(poll_url)

            if is_done(response):
                return response

            on_error(response)

        raise self.error_class(
            f"Timeout: el job en '{poll_url}' no terminó en {self.poll_timeout}s."
        )
