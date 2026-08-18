"""
Cliente HTTP para la API pública de OpenAlex.

Se usa como alternativa a Crossref cuando el documento no posee DOI.
Permite buscar por título o ISBN a través del endpoint /works con filtros.

Documentación: https://docs.openalex.org/api-entities/works/filter-works

Flujo típico:
  enricher = OpenAlexEnricher()
  metadata = enricher.enrich_by_title("Aprendizaje automático en repositorios")
  metadata = enricher.enrich_by_isbn("978-3-16-148410-0")
"""

import logging
from typing import Optional

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.utils.config import config

logger = logging.getLogger(__name__)

_OPENALEX_BASE_URL = "https://api.openalex.org"


class OpenAlexEnricherError(Exception):
    """Excepción lanzada ante errores con la API de OpenAlex."""
    pass


class OpenAlexEnricher:
    """
    Cliente para enriquecer metadatos de documentos académicos vía OpenAlex.

    Se utiliza cuando el documento no posee DOI. Permite buscar por título
    (búsqueda semántica aproximada) o por ISBN.
    """

    def __init__(self, email: Optional[str] = None) -> None:
        self._email = email or config.OPENALEX_EMAIL
        self._session = requests.Session()
        self._session.headers.update({
            # Polite pool de OpenAlex: email en User-Agent mejora la tasa de peticiones
            "User-Agent": f"BulkImportPipeline/1.0 (mailto:{self._email})",
        })

    def _url(self, path: str) -> str:
        return f"{_OPENALEX_BASE_URL}{path}"

    @retry(
        retry=retry_if_exception_type(requests.RequestException),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _get(self, path: str, params: dict) -> dict:
        """Realiza una petición GET con reintentos exponenciales."""
        response = self._session.get(self._url(path), params=params, timeout=15)
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

        return self._extract_relevant_fields(results[0])

    def enrich_by_isbn(self, isbn: str) -> dict:
        """
        Busca el trabajo académico por ISBN y retorna sus metadatos.

        Args:
            isbn: ISBN del documento (puede incluir guiones).

        Returns:
            Diccionario con metadatos enriquecidos o vacío si no se encontró.
        """
        if not isbn or not str(isbn).strip():
            return {}

        isbn_clean = str(isbn).replace("-", "").strip()

        try:
            data = self._get("/works", params={
                "filter": f"ids.openalex:https://openalex.org/works?filter=ids.isbn:{isbn_clean}",
                "per-page": 1,
                "mailto": self._email,
            })
        except requests.RequestException as exc:
            logger.warning("[OpenAlexEnricher] Error al buscar ISBN '%s': %s", isbn_clean, exc)
            return {}

        results = data.get("results", [])
        if not results:
            return {}

        return self._extract_relevant_fields(results[0])

    def _extract_relevant_fields(self, work: dict) -> dict:
        """
        Normaliza la respuesta de OpenAlex extrayendo los campos relevantes
        para el proceso de enriquecimiento del pipeline.
        """
        # Autores: lista de authorships
        authors = [
            a.get("author", {}).get("display_name", "")
            for a in work.get("authorships", [])
        ]

        # Fuente (revista/conferencia)
        primary_location = work.get("primary_location") or {}
        source = primary_location.get("source") or {}

        return {
            "openalex_title":       work.get("title", ""),
            "openalex_authors":     " || ".join(filter(None, authors)),
            "openalex_year":        str(work.get("publication_year", "")),
            "openalex_doi":         work.get("doi", ""),
            "openalex_type":        work.get("type", ""),
            "openalex_journal":     source.get("display_name", ""),
            "openalex_issn":        (source.get("issn_l") or ""),
            "openalex_open_access": str(work.get("open_access", {}).get("is_oa", "")),
            "openalex_citations":   str(work.get("cited_by_count", "")),
        }
