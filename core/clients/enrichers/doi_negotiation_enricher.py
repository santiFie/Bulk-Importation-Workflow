"""
Enriquecedor universal vía DOI Content Negotiation.

Resuelve metadata de cualquier DOI (Crossref, DataCite, mEDRA) usando
el servicio del DOI Foundation (https://doi.org/{doi}) con Accept
``application/vnd.citationstyles.csl+json``. Evita tener un enriquecedor
por cada agencia de registro.

Donde aporta: DOIs de Zenodo, OSF, figshare, Dryad, repositorios
institucionales (todos DataCite) y SciELO LatAm — que Crossref no cubre.

Limitaciones:
  - CSL JSON no expone PDF directo.
  - No hay endpoint de búsqueda por título (stub vacío).
  - Rate limit comunitario ~50 req/s (suficiente para nuestro volumen).

Referencias:
  - https://www.doi.org/doi-handbook/HTML/content-negotiation.html
  - https://citation.crosscite.org/docs.html
"""

import logging
import re
from typing import Any, Optional

import requests

from core.clients.enrichers.base_enricher import BaseEnricher, BaseEnricherError

logger = logging.getLogger(__name__)

_DOI_STRIP = re.compile(
    r"^\s*(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE
)


def _clean_doi(doi: str) -> str:
    return _DOI_STRIP.sub("", doi or "").strip()


class DOINegotiationEnricherError(BaseEnricherError):
    """Excepción lanzada ante errores en DOI Content Negotiation."""
    pass


class DOINegotiationEnricher(BaseEnricher):
    """Resuelve metadata de cualquier DOI vía content negotiation CSL JSON."""

    def __init__(self, timeout: int = 8) -> None:
        super().__init__(base_url="https://doi.org", timeout=timeout)
        self._session.headers.update({
            "Accept": "application/vnd.citationstyles.csl+json",
            "User-Agent": "BulkImportPipeline/1.0 DOI-CN client",
        })

    @property
    def provider_name(self) -> str:
        return "DOINegotiation"

    def enrich_by_doi(self, doi: str, schema: str = "sedici") -> dict:
        """
        Pide metadata CSL JSON al resolver doi.org.

        Devuelve {} si:
          - el DOI no existe (404),
          - la agencia no devolvió CSL JSON (406/415),
          - error de red.
        """
        doi_clean = _clean_doi(doi)
        if not doi_clean:
            return {}

        url = f"{self.base_url}/{doi_clean}"
        try:
            response = self._session.get(
                url, timeout=self._timeout, allow_redirects=True,
            )
            if response.status_code == 404:
                logger.info(
                    "[DOINegotiation] %s → 404 (DOI no registrado)", doi_clean,
                )
                return {}
            if response.status_code != 200:
                logger.warning(
                    "[DOINegotiation] %s → HTTP %s", doi_clean, response.status_code,
                )
                return {}
            try:
                data = response.json()
            except ValueError:
                logger.warning(
                    "[DOINegotiation] %s → respuesta no-JSON", doi_clean,
                )
                return {}
        except requests.RequestException as exc:
            logger.warning(
                "[DOINegotiation] Error para DOI %s: %s", doi_clean, exc,
            )
            return {}

        parsed = self.parse_response(data)
        return self.map_by_schema(parsed, schema)

    def parse_response(self, data: Any) -> dict:
        """Mapea CSL JSON al schema interno común."""
        csl = data if isinstance(data, dict) else {}

        title = csl.get("title")
        if isinstance(title, list) and title:
            title = title[0]

        authors: list[dict] = []
        for a in csl.get("author") or []:
            if isinstance(a, dict):
                family = a.get("family") or ""
                given = a.get("given") or ""
                literal = a.get("literal")
                name = literal if literal else f"{given} {family}".strip()
                if name:
                    authors.append(name)

        year: Optional[int] = None
        issued = csl.get("issued") or {}
        date_parts = issued.get("date-parts") or []
        if date_parts and date_parts[0]:
            try:
                year = int(date_parts[0][0])
            except (TypeError, ValueError):
                year = None

        container = csl.get("container-title")
        if isinstance(container, list) and container:
            container = container[0]

        # Combinar volumen y número en el formato esperado por SEDICI
        vol = csl.get("volume")
        issue = csl.get("issue")
        partes_vol = []
        if vol:
            partes_vol.append(f"vol. {vol}")
        if issue:
            partes_vol.append(f"no. {issue}")
        volume_and_issue = ", ".join(partes_vol) if partes_vol else None

        return {
            "title":            title,
            "authors":          " || ".join(authors),
            "year":             str(year) if year is not None else "",
            "publisher":        csl.get("publisher"),
            "journal":          container,
            "volume_and_issue": volume_and_issue,
            "pages":            csl.get("page"),
            "doi":              csl.get("DOI"),
            "type":             csl.get("type"),
            "language":         csl.get("language"),
            "abstract_en":      csl.get("abstract"),
        }
