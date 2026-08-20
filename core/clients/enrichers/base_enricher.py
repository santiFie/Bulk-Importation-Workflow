"""
Clase base abstracta para todos los enriquecedores de metadatos académicos.

Define el contrato que cumplen CrossrefEnricher, OpenAlexEnricher,
DOINegotiationEnricher y OpenLibraryEnricher. El nodo de enriquecimiento
trabaja contra esta interfaz, no contra implementaciones concretas.

Diseñado para procesamiento síncrono por lote (nodo LangGraph sobre CSV).
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Optional

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


class BaseEnricherError(Exception):
    """Excepción base para errores de enriquecimiento."""
    pass


class BaseEnricher(ABC):
    """
    Contrato para proveedores de enriquecimiento de metadatos.

    Cada subclase implementa las estrategias de consulta que soporta
    (DOI, título, ISSN, ISBN) y expone los resultados normalizados
    vía ``parse_response`` + ``map_to_csv_columns``.
    """

    # ------------------------------------------------------------------
    # Mapeo de clave interna → nombre de columna SEDICI/Dublin Core.
    # Las claves del schema interno (parse_response) que no aparezcan
    # aquí se omiten silenciosamente del CSV resultante.
    # ------------------------------------------------------------------
    SEDICI_COLUMN_MAP: dict[str, str] = {
        "title":            "dc.title",
        "authors":          "sedici.creator.person",
        "year":             "dc.date.issued",
        "doi":              "sedici.identifier.other",
        "type":             "dc.type",
        "journal":          "sedici.relation.journalTitle",
        "issn":             "sedici.identifier.issn",
        "abstract_es":      "dc.abstract[es]",
        "abstract_en":      "dc.abstract[en]",
        "pages":            "dc.format.extent",
        "language":         "dc.language",
        "subjects":         "sedici.subject.materias",
        "publisher":        "dc.publisher",
        "volume_and_issue": "sedici.relation.journalVolumeAndIssue",
        "isbn":             "sedici.identifier.isbn",
        # open_access y citations: omitidos intencionalmente
    }

    def __init__(self, base_url: str, timeout: int = 15) -> None:
        self.base_url = base_url
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": f"BulkImportPipeline/1.0",
            "Accept": "application/json",
        })

    # ------------------------------------------------------------------
    # Propiedad obligatoria: nombre legible del enriquecedor
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Nombre legible del proveedor (ej. 'Crossref', 'OpenAlex')."""
        ...

    # ------------------------------------------------------------------
    # Estrategias de consulta (cada subclase implementa las que aplica)
    # ------------------------------------------------------------------

    def enrich_by_doi(self, doi: str) -> dict:
        """Enriquecer a partir de un DOI. Devuelve {} si no soportado."""
        return {}

    def enrich_by_title(self, title: str) -> dict:
        """Enriquecer a partir de un título. Devuelve {} si no soportado."""
        return {}

    def enrich_by_issn(self, issn: str) -> dict:
        """Enriquecer a partir de un ISSN. Devuelve {} si no soportado."""
        return {}

    def enrich_by_isbn(self, isbn: str) -> dict:
        """Enriquecer a partir de un ISBN. Devuelve {} si no soportado."""
        return {}

    # ------------------------------------------------------------------
    # Parsing y mapeo a columnas CSV (separación de responsabilidades)
    # ------------------------------------------------------------------

    @abstractmethod
    def parse_response(self, data: Any) -> dict:
        """
        Normaliza la respuesta cruda de la API a un schema interno común.

        Devuelve un dict con claves normalizadas como:
        title, authors, year, publisher, issn, type, abstract, journal, etc.
        """
        ...

    def map_to_csv_columns(self, parsed: dict) -> dict:
        """
        Transforma el schema interno normalizado al formato de columnas SEDICI.

        Utiliza ``SEDICI_COLUMN_MAP`` para traducir cada clave interna
        (ej. ``authors``) al nombre de metadato SEDICI correspondiente
        (ej. ``sedici.creator.person``). Las claves internas sin mapeo
        (``open_access``, ``citations``, etc.) se omiten del resultado.
        Los valores vacíos o nulos también se omiten para no generar
        columnas vacías en el CSV.
        """
        return {
            self.SEDICI_COLUMN_MAP[k]: v
            for k, v in parsed.items()
            if k in self.SEDICI_COLUMN_MAP and v not in (None, "")
        }

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Verificar disponibilidad de la API (no bloqueante)."""
        try:
            response = self._session.get(self.base_url, timeout=self._timeout)
            return {
                "available": response.status_code < 500,
                "status_code": response.status_code,
                "provider": self.provider_name,
            }
        except Exception as exc:
            logger.error("Error verificando estado de %s: %s", self.provider_name, exc)
            return {
                "available": False,
                "status_code": None,
                "provider": self.provider_name,
                "error": str(exc),
            }

    # ------------------------------------------------------------------
    # HTTP con reintentos (síncrono, para procesamiento por lote)
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(requests.RequestException),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _get(self, url: str, **kwargs) -> dict:
        """GET con reintentos exponenciales. Devuelve dict (JSON) o {} en 404."""
        response = self._session.get(url, timeout=self._timeout, **kwargs)
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------
    # Utilidades comunes
    # ------------------------------------------------------------------

    def _create_enriched_base(self) -> dict:
        """Estructura base para datos enriquecidos."""
        return {
            "source": self.provider_name,
            "enriched_at": datetime.now().isoformat(),
        }

    def _handle_error(self, error: Exception, context: str = "") -> dict:
        """Manejar errores de forma consistente."""
        error_msg = f"Error en {self.provider_name}"
        if context:
            error_msg += f" ({context})"
        logger.error("%s: %s", error_msg, error)
        return {
            **self._create_enriched_base(),
            "error": error_msg,
            "success": False,
        }
