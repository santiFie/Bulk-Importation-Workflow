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

_CROSSREF_BASE_URL = "https://api.crossref.org"


class CrossrefEnricherError(Exception):
    """Excepción lanzada ante errores con la API de Crossref."""
    pass

class CrossrefEnricher:
    """
    Cliente para enriquecer metadatos de documentos académicos vía Crossref.

    Utiliza el endpoint /works/{doi} para recuperar metadatos autoritativos
    como título, autores, fecha de publicación, ISSN, editorial, etc.
    """

    def __init__(self, email: Optional[str] = None) -> None:
        self._email = email or config.OPENALEX_EMAIL
        self._session = requests.Session()
        self._session.headers.update({
            # Identifica la aplicación ante Crossref para el polite pool
            "User-Agent": f"BulkImportPipeline/1.0 (mailto:{self._email})",
        })

    def _url(self, path: str) -> str:
        return f"{_CROSSREF_BASE_URL}{path}"

    @retry(
        retry=retry_if_exception_type(requests.RequestException),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _get(self, path: str) -> dict:
        """Realiza una petición GET con reintentos exponenciales."""
        response = self._session.get(self._url(path), timeout=15)
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        return response.json()

    def enrich_by_doi(self, doi: str) -> dict:
        """
        Recupera metadatos de un trabajo académico a partir de su DOI.

        Args:
            doi: Identificador DOI del documento (ej. "10.1000/xyz123").

        Returns:
            Diccionario con los metadatos enriquecidos, o vacío si no se
            encontró el DOI. Las claves relevantes incluyen:
            - title, author, published, publisher, ISSN, abstract, type
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
        return self._extract_relevant_fields(work)

    def _extract_relevant_fields(self, work: dict) -> dict:
        """
        Normaliza la respuesta de Crossref extrayendo sólo los campos relevantes
        para el proceso de enriquecimiento del pipeline.
        """
        # Título: Crossref devuelve una lista
        title_list = work.get("title", [])
        title = title_list[0] if title_list else ""

        # Autores: lista de {given, family, ...}
        authors = [
            f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")
            for a in work.get("author", [])
        ]

        # Fecha de publicación
        pub_date = work.get("published", {}).get("date-parts", [[]])[0]
        year = str(pub_date[0]) if pub_date else ""

        # ISSN
        issn_list = work.get("ISSN", [])
        issn = issn_list[0] if issn_list else ""

        return {
            "crossref_title":       title,
            "crossref_authors":     " || ".join(authors),
            "crossref_year":        year,
            "crossref_publisher":   work.get("publisher", ""),
            "crossref_issn":        issn,
            "crossref_type":        work.get("type", ""),
            "crossref_abstract":    work.get("abstract", ""),
            "crossref_journal":     work.get("container-title", [""])[0] if work.get("container-title") else "",
        }
