"""
Enriquecedor de LIBROS por ISBN vía OpenLibrary API.

Cierra el hueco que ningún otro enriquecedor cubre: libros sin DOI
(frecuentes en bibliografía de tesis, ej. clásicos en español). OpenAlex
no busca por ISBN y Crossref solo tiene libros que ya traen DOI.

Rol acotado: solo metadata bibliográfica. No resuelve DOI ni descarga
PDFs (los libros casi nunca son OA).

API: https://openlibrary.org/dev/docs/api/books
"""

import logging
import re
from typing import Any

import requests

from core.clients.enrichers.base_enricher import BaseEnricher, BaseEnricherError

logger = logging.getLogger(__name__)

_YEAR_RE = re.compile(r"(\d{4})")


def _normalize_isbn(isbn: str) -> str:
    """Deja solo dígitos y X (ISBN-10 puede terminar en X)."""
    return re.sub(r"[^0-9Xx]", "", isbn or "").upper()


class OpenLibraryEnricherError(BaseEnricherError):
    """Excepción lanzada ante errores con OpenLibrary."""
    pass


class OpenLibraryEnricher(BaseEnricher):
    """Cliente ISBN → metadata bibliográfica de libros."""

    def __init__(self, timeout: int = 10) -> None:
        super().__init__(base_url="https://openlibrary.org", timeout=timeout)
        self._session.headers.update({
            "User-Agent": "BulkImportPipeline/1.0 (book metadata via OpenLibrary)",
        })

    @property
    def provider_name(self) -> str:
        return "OpenLibrary"

    def enrich_by_isbn(self, isbn: str) -> dict:
        """Enriquecer un libro por ISBN.

        Usa el endpoint Books con jscmd=data (título, autores, editorial,
        fecha, páginas, materias). Devuelve {} si el ISBN no está en
        OpenLibrary.
        """
        norm = _normalize_isbn(isbn)
        if not norm:
            return {}

        url = f"{self.base_url}/api/books"
        params = {"bibkeys": f"ISBN:{norm}", "format": "json", "jscmd": "data"}
        try:
            response = self._session.get(url, params=params, timeout=self._timeout)
            if response.status_code != 200:
                logger.debug(
                    "[OpenLibrary] HTTP %s para ISBN %s", response.status_code, norm,
                )
                return {}
            payload = response.json()
            entry = payload.get(f"ISBN:{norm}")
            if not entry:
                logger.debug("[OpenLibrary] ISBN %s no encontrado", norm)
                return {}
        except requests.RequestException as exc:
            logger.warning("[OpenLibrary] Error para ISBN %s: %s", norm, exc)
            return {}

        parsed = self.parse_response(entry)
        parsed["isbn"] = norm
        return self.map_to_csv_columns(parsed)

    def parse_response(self, data: Any) -> dict:
        """Normaliza la respuesta de OpenLibrary al schema interno."""
        entry = data if isinstance(data, dict) else {}

        title = entry.get("title") or ""
        subtitle = entry.get("subtitle")
        if subtitle:
            title = f"{title}: {subtitle}"

        authors = [
            a.get("name")
            for a in (entry.get("authors") or [])
            if a.get("name")
        ]

        publishers = [
            p.get("name")
            for p in (entry.get("publishers") or [])
            if p.get("name")
        ]

        year = None
        m = _YEAR_RE.search(entry.get("publish_date") or "")
        if m:
            year = int(m.group(1))

        subjects = [
            s.get("name")
            for s in (entry.get("subjects") or [])
            if s.get("name")
        ]

        pages = entry.get("number_of_pages")

        return {
            "title": title or None,
            "authors": " || ".join(authors),
            "publisher": publishers[0] if publishers else None,
            "year": str(year) if year is not None else "",
            "pages": str(pages) if pages else None,
            "subjects": ", ".join(subjects[:10]),
            "type": "Libro",
        }
