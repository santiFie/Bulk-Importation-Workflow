"""
Cliente HTTP para la API pública de OpenAlex.

Se usa como alternativa a Crossref cuando el documento no posee DOI.
Permite buscar por título o ISSN a través del endpoint /works con filtros.

Documentación: https://docs.openalex.org/api-entities/works/filter-works

Flujo típico:
  enricher = OpenAlexEnricher()
  metadata = enricher.enrich_by_title("Aprendizaje automático en repositorios")
  metadata = enricher.enrich_by_issn("0028-0836")
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

_OPENALEX_BASE_URL = "https://api.openalex.org"


class OpenAlexEnricherError(BaseEnricherError):
    """Excepción lanzada ante errores con la API de OpenAlex."""
    pass


class OpenAlexEnricher(BaseEnricher):
    """
    Cliente para enriquecer metadatos de documentos académicos vía OpenAlex.

    Se utiliza cuando el documento no posee DOI. Permite buscar por título
    (búsqueda semántica aproximada) o por ISSN.
    """

    def __init__(self, email: Optional[str] = None) -> None:
        super().__init__(base_url=_OPENALEX_BASE_URL)
        self._email = email or config.OPENALEX_EMAIL
        mailto = self._email.strip() if self._email else ""
        self._session.headers.update({
            "User-Agent": f"BulkImportPipeline/1.0 (mailto:{mailto})" if mailto else "BulkImportPipeline/1.0",
        })

    @property
    def provider_name(self) -> str:
        return "OpenAlex"

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    @retry(
        retry=retry_if_exception_type(requests.RequestException),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _get(self, path: str, params: dict) -> dict:
        """GET con reintentos exponenciales. Devuelve JSON o {} en 404."""
        response = self._session.get(self._url(path), params=params, timeout=self._timeout)
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        return response.json()

    def enrich_by_title(self, title: str) -> dict:
        """
        Busca el trabajo académico más relevante por título y retorna sus metadatos.

        Usa la búsqueda semántica de OpenAlex (`search`). Sólo retorna resultados
        con alta relevancia (primer resultado de la búsqueda, si existe).

        Args:
            title: Título del documento a buscar.

        Returns:
            Diccionario con metadatos enriquecidos o vacío si no se encontró.
        """
        if not title or not str(title).strip():
            return {}

        try:
            data = self._get("/works", params={
                "search": str(title).strip(),
                "per-page": 1,
                "mailto": self._email,
            })
        except requests.RequestException as exc:
            logger.warning("[OpenAlexEnricher] Error al buscar título '%s': %s", title[:60], exc)
            return {}

        results = data.get("results", [])
        if not results:
            return {}

        parsed = self.parse_response(results[0])
        return self.map_to_csv_columns(parsed)

    def enrich_by_issn(self, issn: str) -> dict:
        """
        Busca el trabajo académico por ISSN y retorna sus metadatos.

        Args:
            issn: ISSN de la revista (puede incluir guiones).

        Returns:
            Diccionario con metadatos enriquecidos o vacío si no se encontró.
        """
        if not issn or not str(issn).strip():
            return {}

        issn_clean = str(issn).replace("-", "").strip()

        try:
            data = self._get("/works", params={
                "filter": f"ids.issn:{issn_clean}",
                "per-page": 1,
                "mailto": self._email,
            })
        except requests.RequestException as exc:
            logger.warning("[OpenAlexEnricher] Error al buscar ISSN '%s': %s", issn_clean, exc)
            return {}

        results = data.get("results", [])
        if not results:
            return {}

        parsed = self.parse_response(results[0])
        return self.map_to_csv_columns(parsed)

    def parse_response(self, data: Any) -> dict:
        """
        Normaliza la respuesta de OpenAlex extrayendo un schema interno común.
        """
        work = data if isinstance(data, dict) else {}

        authors = [
            a.get("author", {}).get("display_name", "")
            for a in work.get("authorships", [])
        ]

        primary_location = work.get("primary_location") or {}
        source = primary_location.get("source") or {}

        return {
            "title": work.get("title", ""),
            "authors": " || ".join(filter(None, authors)),
            "year": str(work.get("publication_year", "")),
            "doi": work.get("doi", ""),
            "type": work.get("type", ""),
            "journal": source.get("display_name", ""),
            "issn": (source.get("issn_l") or ""),
            "open_access": str(work.get("open_access", {}).get("is_oa", "")),
            "citations": str(work.get("cited_by_count", "")),
        }

    def _extract_relevant_fields(self, work: dict) -> dict:
        """
        Normaliza la respuesta de OpenAlex extrayendo los campos relevantes
        para el proceso de enriquecimiento del pipeline.

        Método legacy mantenido por compatibilidad con tests existentes.
        Delega en parse_response + map_to_csv_columns.
        """
        parsed = self.parse_response(work)
        return self.map_to_csv_columns(parsed)
