"""
Cliente HTTP para la API pública de Crossref.

Permite enriquecer metadatos a partir de un DOI, consultando el endpoint
/works/{doi} de la API de Crossref.

Uso del "polite pool": se envía un email de contacto en el User-Agent para
obtener mayor tasa de peticiones (ver https://api.crossref.org/swagger-ui/index.html).

Flujo típico:
  enricher = CrossrefEnricher()
  metadata = enricher.enrich_by_doi("10.1000/xyz123")
"""

import logging
from typing import Any, Optional

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.clients.enrichers.base_enricher import BaseEnricher, BaseEnricherError
from core.utils.config import config

logger = logging.getLogger(__name__)

_CROSSREF_BASE_URL = "https://api.crossref.org"


class CrossrefEnricherError(BaseEnricherError):
    """Excepción lanzada ante errores con la API de Crossref."""
    pass


class CrossrefEnricher(BaseEnricher):
    """
    Cliente para enriquecer metadatos de documentos académicos vía Crossref.

    Utiliza el endpoint /works/{doi} para recuperar metadatos autoritativos
    como título, autores, fecha de publicación, ISSN, editorial, etc.
    """

    def __init__(self, email: Optional[str] = None) -> None:
        super().__init__(base_url=_CROSSREF_BASE_URL)
        self._email = email or config.OPENALEX_EMAIL
        mailto = self._email.strip() if self._email else ""
        self._session.headers.update({
            "User-Agent": f"BulkImportPipeline/1.0 (mailto:{mailto})" if mailto else "BulkImportPipeline/1.0",
        })

    @property
    def provider_name(self) -> str:
        return "Crossref"

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    @retry(
        retry=retry_if_exception_type(requests.RequestException),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _get(self, path: str) -> dict:
        """GET con reintentos exponenciales. Devuelve JSON o {} en 404."""
        response = self._session.get(self._url(path), timeout=self._timeout)
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        return response.json()

    def enrich_by_doi(self, doi: str, schema: str = "sedici") -> dict:
        """
        Recupera metadatos de un trabajo académico a partir de su DOI.

        Args:
            doi: Identificador DOI del documento (ej. "10.1000/xyz123").
            schema: Esquema de columnas de salida ("sedici" o "generic").

        Returns:
            Diccionario con los metadatos enriquecidos, o vacío si no se
            encontró el DOI.
        """
        if not doi or not str(doi).strip():
            return {}

        doi_clean = str(doi).strip().lstrip("https://doi.org/").lstrip("http://dx.doi.org/")

        try:
            data = self._get(f"/works/{doi_clean}")
        except requests.RequestException as exc:
            logger.warning("[CrossrefEnricher] Error al consultar DOI '%s': %s", doi_clean, exc)
            return {}

        if not data or data.get("status") != "ok":
            return {}

        work = data.get("message", {})
        parsed = self.parse_response(work)
        return self.map_by_schema(parsed, schema)

    def parse_response(self, data: Any) -> dict:
        """
        Normaliza la respuesta de Crossref extrayendo un schema interno común.

        Acepta el dict ``message`` de la respuesta de Crossref (no la envoltura
        completa con ``status``).
        """
        work = data if isinstance(data, dict) else {}

        title_list = work.get("title", [])
        title = title_list[0] if title_list else ""

        authors = [
            f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")
            for a in work.get("author", [])
        ]

        pub_date = work.get("published", {}).get("date-parts", [[]])[0]
        year = str(pub_date[0]) if pub_date else ""

        issn_list = work.get("ISSN", [])
        issn = issn_list[0] if issn_list else ""

        return {
            "title":       title,
            "authors":     " || ".join(authors),
            "year":        year,
            "publisher":   work.get("publisher", ""),
            "issn":        issn,
            "type":        work.get("type", ""),
            "abstract_en": work.get("abstract", ""),
            "journal": (
                work.get("container-title", [""])[0]
                if work.get("container-title")
                else ""
            ),
        }

    def _extract_relevant_fields(self, work: dict) -> dict:
        """
        Normaliza la respuesta de Crossref extrayendo sólo los campos relevantes
        para el proceso de enriquecimiento del pipeline.

        Método legacy mantenido por compatibilidad con tests existentes.
        Delega en parse_response + map_to_csv_columns.
        """
        parsed = self.parse_response(work)
        return self.map_to_csv_columns(parsed)
