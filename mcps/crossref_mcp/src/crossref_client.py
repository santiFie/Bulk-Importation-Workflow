"""
Crossref MCP — Cliente HTTP
Envuelve la REST API de Crossref con un cliente httpx sincrónico y reutilizable.

Endpoints cubiertos:
  • /works                    — lista / búsqueda de obras
  • /works/{doi}              — obra por DOI
  • /works/{doi}/agency       — agencia registrante del DOI
  • /journals                 — lista de revistas
  • /journals/{issn}          — revista por ISSN
  • /journals/{issn}/works    — obras de una revista
  • /funders                  — lista de financiadores
  • /funders/{id}             — financiador por ID
  • /funders/{id}/works       — obras de un financiador
  • /members                  — lista de miembros Crossref
  • /members/{id}             — miembro por ID
  • /members/{id}/works       — obras de un miembro
  • /prefixes/{prefix}        — prefijo DOI
  • /prefixes/{prefix}/works  — obras de un prefijo
  • /types                    — lista de tipos de contenido
  • /types/{id}               — tipo de contenido por ID
  • /types/{id}/works         — obras de un tipo
  • /licenses                 — lista de licencias
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import quote

import httpx

from config import CROSSREF_BASE_URL, CROSSREF_EMAIL, CROSSREF_USER_AGENT

logger = logging.getLogger(__name__)

# Límites de paginación
DEFAULT_ROWS = 20
MAX_ROWS = 1000


class CrossrefClient:
    """Cliente HTTP sincrónico para la REST API de Crossref."""

    def __init__(self) -> None:
        # Construye el User-Agent interpolando el email si está disponible
        user_agent = CROSSREF_USER_AGENT.format(email=CROSSREF_EMAIL or "not-set")

        self._default_params: dict[str, str] = {}
        if CROSSREF_EMAIL:
            # El «polite pool» otorga mayor límite de tasa
            self._default_params["mailto"] = CROSSREF_EMAIL

        self._client = httpx.Client(
            base_url=CROSSREF_BASE_URL,
            headers={"User-Agent": user_agent},
            timeout=30.0,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Helper de bajo nivel
    # ──────────────────────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """Ejecuta un GET y retorna el cuerpo JSON como dict."""
        merged = {**self._default_params, **params}
        # Eliminar claves con valores None / cadena vacía para no contaminar la URL
        merged = {k: v for k, v in merged.items() if v is not None and v != ""}
        logger.debug("GET %s  params=%s", path, merged)
        response = self._client.get(path, params=merged)
        response.raise_for_status()
        return response.json()

    # ──────────────────────────────────────────────────────────────────────────
    # Works
    # ──────────────────────────────────────────────────────────────────────────

    def search_works(
        self,
        query: Optional[str] = None,
        query_bibliographic: Optional[str] = None,
        query_author: Optional[str] = None,
        query_title: Optional[str] = None,
        filter: Optional[str] = None,
        select: Optional[str] = None,
        sort: Optional[str] = None,
        order: Optional[str] = None,
        rows: int = DEFAULT_ROWS,
        offset: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Busca obras en el índice Crossref.

        Args:
            query: Búsqueda libre en todos los campos.
            query_bibliographic: Búsqueda en campos bibliográficos (título, autores, ISSN, año).
            query_author: Búsqueda por nombre de autor (nombre y apellido).
            query_title: Búsqueda solo en el título.
            filter: Filtro Crossref (ej. 'from-pub-date:2020,type:journal-article').
            select: Campos a retornar, separados por coma (ej. 'DOI,title,author').
            sort: Campo de ordenamiento (ej. 'published', 'is-referenced-by-count').
            order: Dirección del ordenamiento: 'asc' o 'desc'.
            rows: Cantidad de resultados por página (máx. 1000).
            offset: Saltar los primeros N resultados (máx. 10000; preferir cursor).
            cursor: Token para paginar conjuntos grandes (usar '*' en la primera llamada).
        """
        params: dict[str, Any] = {
            "query": query,
            "query.bibliographic": query_bibliographic,
            "query.author": query_author,
            "query.title": query_title,
            "filter": filter,
            "select": select,
            "sort": sort,
            "order": order,
            "rows": min(rows, MAX_ROWS),
            "offset": offset,
            "cursor": cursor,
        }
        return self._get("/works", params)

    def get_work(self, doi: str) -> dict[str, Any]:
        """
        Recupera los metadatos de una obra por su DOI.

        Args:
            doi: Identificador DOI (ej. '10.5555/12345678').
        """
        encoded = quote(doi, safe="")
        return self._get(f"/works/{encoded}", {})

    def get_work_agency(self, doi: str) -> dict[str, Any]:
        """
        Retorna la agencia registrante del DOI (ej. Crossref, DataCite).

        Args:
            doi: Identificador DOI (ej. '10.5555/12345678').
        """
        encoded = quote(doi, safe="")
        return self._get(f"/works/{encoded}/agency", {})

    # ──────────────────────────────────────────────────────────────────────────
    # Journals
    # ──────────────────────────────────────────────────────────────────────────

    def search_journals(
        self,
        query: Optional[str] = None,
        rows: int = DEFAULT_ROWS,
        offset: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Lista o busca revistas en la base de datos Crossref.

        Args:
            query: Búsqueda libre por nombre de revista.
            rows: Cantidad de resultados por página.
            offset: Saltar los primeros N resultados.
            cursor: Token de cursor para paginación.
        """
        params: dict[str, Any] = {
            "query": query,
            "rows": min(rows, MAX_ROWS),
            "offset": offset,
            "cursor": cursor,
        }
        return self._get("/journals", params)

    def get_journal(self, issn: str) -> dict[str, Any]:
        """
        Recupera información de una revista por su ISSN.

        Args:
            issn: ISSN de la revista (ej. '0302-9743').
        """
        return self._get(f"/journals/{issn}", {})

    def get_journal_works(
        self,
        issn: str,
        query: Optional[str] = None,
        query_bibliographic: Optional[str] = None,
        query_author: Optional[str] = None,
        filter: Optional[str] = None,
        select: Optional[str] = None,
        sort: Optional[str] = None,
        order: Optional[str] = None,
        rows: int = DEFAULT_ROWS,
        offset: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Retorna las obras publicadas en la revista identificada por {issn}.

        Args:
            issn: ISSN de la revista.
            query: Búsqueda libre dentro de las obras de la revista.
            query_bibliographic: Búsqueda en campos bibliográficos.
            query_author: Búsqueda por nombre de autor.
            filter: Filtro Crossref.
            select: Campos a retornar.
            sort: Campo de ordenamiento.
            order: Dirección del ordenamiento.
            rows: Cantidad de resultados por página.
            offset: Saltar los primeros N resultados.
            cursor: Token de cursor para paginación.
        """
        params: dict[str, Any] = {
            "query": query,
            "query.bibliographic": query_bibliographic,
            "query.author": query_author,
            "filter": filter,
            "select": select,
            "sort": sort,
            "order": order,
            "rows": min(rows, MAX_ROWS),
            "offset": offset,
            "cursor": cursor,
        }
        return self._get(f"/journals/{issn}/works", params)

    # ──────────────────────────────────────────────────────────────────────────
    # Funders
    # ──────────────────────────────────────────────────────────────────────────

    def search_funders(
        self,
        query: Optional[str] = None,
        filter: Optional[str] = None,
        rows: int = DEFAULT_ROWS,
        offset: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Lista o busca financiadores en el Funder Registry.

        Args:
            query: Búsqueda libre por nombre de financiador.
            filter: Filtro por ubicación (ej. 'location:Argentina').
            rows: Cantidad de resultados por página.
            offset: Saltar los primeros N resultados.
            cursor: Token de cursor para paginación.
        """
        params: dict[str, Any] = {
            "query": query,
            "filter": filter,
            "rows": min(rows, MAX_ROWS),
            "offset": offset,
            "cursor": cursor,
        }
        return self._get("/funders", params)

    def get_funder(self, funder_id: str) -> dict[str, Any]:
        """
        Recupera metadatos de un financiador y sus suborganizaciones.

        Args:
            funder_id: ID del financiador en el Funder Registry (ej. '501100006004').
        """
        return self._get(f"/funders/{funder_id}", {})

    def get_funder_works(
        self,
        funder_id: str,
        query: Optional[str] = None,
        query_bibliographic: Optional[str] = None,
        query_author: Optional[str] = None,
        filter: Optional[str] = None,
        select: Optional[str] = None,
        sort: Optional[str] = None,
        order: Optional[str] = None,
        rows: int = DEFAULT_ROWS,
        offset: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Retorna las obras asociadas al financiador {funder_id}.

        Args:
            funder_id: ID del financiador.
            query: Búsqueda libre.
            query_bibliographic: Búsqueda en campos bibliográficos.
            query_author: Búsqueda por nombre de autor.
            filter: Filtro Crossref.
            select: Campos a retornar.
            sort: Campo de ordenamiento.
            order: Dirección del ordenamiento.
            rows: Cantidad de resultados por página.
            offset: Saltar los primeros N resultados.
            cursor: Token de cursor para paginación.
        """
        params: dict[str, Any] = {
            "query": query,
            "query.bibliographic": query_bibliographic,
            "query.author": query_author,
            "filter": filter,
            "select": select,
            "sort": sort,
            "order": order,
            "rows": min(rows, MAX_ROWS),
            "offset": offset,
            "cursor": cursor,
        }
        return self._get(f"/funders/{funder_id}/works", params)

    # ──────────────────────────────────────────────────────────────────────────
    # Members
    # ──────────────────────────────────────────────────────────────────────────

    def search_members(
        self,
        query: Optional[str] = None,
        filter: Optional[str] = None,
        rows: int = DEFAULT_ROWS,
        offset: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Lista o busca miembros (editores) registrados en Crossref.

        Args:
            query: Búsqueda libre por nombre del miembro.
            filter: Filtros soportados: 'backfile-doi-count', 'current-doi-count', 'prefix'.
            rows: Cantidad de resultados por página.
            offset: Saltar los primeros N resultados.
            cursor: Token de cursor para paginación.
        """
        params: dict[str, Any] = {
            "query": query,
            "filter": filter,
            "rows": min(rows, MAX_ROWS),
            "offset": offset,
            "cursor": cursor,
        }
        return self._get("/members", params)

    def get_member(self, member_id: int) -> dict[str, Any]:
        """
        Recupera metadatos de un miembro Crossref por su ID numérico.

        Args:
            member_id: ID numérico del miembro (ej. 324 para Elsevier).
        """
        return self._get(f"/members/{member_id}", {})

    def get_member_works(
        self,
        member_id: int,
        query: Optional[str] = None,
        query_bibliographic: Optional[str] = None,
        query_author: Optional[str] = None,
        filter: Optional[str] = None,
        select: Optional[str] = None,
        sort: Optional[str] = None,
        order: Optional[str] = None,
        rows: int = DEFAULT_ROWS,
        offset: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Retorna las obras depositadas por el miembro {member_id}.

        Args:
            member_id: ID numérico del miembro.
            query: Búsqueda libre.
            query_bibliographic: Búsqueda en campos bibliográficos.
            query_author: Búsqueda por nombre de autor.
            filter: Filtro Crossref.
            select: Campos a retornar.
            sort: Campo de ordenamiento.
            order: Dirección del ordenamiento.
            rows: Cantidad de resultados por página.
            offset: Saltar los primeros N resultados.
            cursor: Token de cursor para paginación.
        """
        params: dict[str, Any] = {
            "query": query,
            "query.bibliographic": query_bibliographic,
            "query.author": query_author,
            "filter": filter,
            "select": select,
            "sort": sort,
            "order": order,
            "rows": min(rows, MAX_ROWS),
            "offset": offset,
            "cursor": cursor,
        }
        return self._get(f"/members/{member_id}/works", params)

    # ──────────────────────────────────────────────────────────────────────────
    # Prefixes
    # ──────────────────────────────────────────────────────────────────────────

    def get_prefix(self, prefix: str) -> dict[str, Any]:
        """
        Recupera metadatos del propietario de un prefijo DOI.

        Args:
            prefix: Prefijo DOI (ej. '10.1016').
        """
        return self._get(f"/prefixes/{prefix}", {})

    def get_prefix_works(
        self,
        prefix: str,
        query: Optional[str] = None,
        query_bibliographic: Optional[str] = None,
        query_author: Optional[str] = None,
        filter: Optional[str] = None,
        select: Optional[str] = None,
        sort: Optional[str] = None,
        order: Optional[str] = None,
        rows: int = DEFAULT_ROWS,
        offset: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Retorna las obras asociadas al prefijo DOI {prefix}.

        Args:
            prefix: Prefijo DOI (ej. '10.1016').
            query: Búsqueda libre.
            query_bibliographic: Búsqueda en campos bibliográficos.
            query_author: Búsqueda por nombre de autor.
            filter: Filtro Crossref.
            select: Campos a retornar.
            sort: Campo de ordenamiento.
            order: Dirección del ordenamiento.
            rows: Cantidad de resultados por página.
            offset: Saltar los primeros N resultados.
            cursor: Token de cursor para paginación.
        """
        params: dict[str, Any] = {
            "query": query,
            "query.bibliographic": query_bibliographic,
            "query.author": query_author,
            "filter": filter,
            "select": select,
            "sort": sort,
            "order": order,
            "rows": min(rows, MAX_ROWS),
            "offset": offset,
            "cursor": cursor,
        }
        return self._get(f"/prefixes/{prefix}/works", params)

    # ──────────────────────────────────────────────────────────────────────────
    # Types
    # ──────────────────────────────────────────────────────────────────────────

    def list_types(self, rows: int = DEFAULT_ROWS, offset: Optional[int] = None) -> dict[str, Any]:
        """
        Lista todos los tipos de contenido válidos en Crossref.

        Args:
            rows: Cantidad de resultados por página.
            offset: Saltar los primeros N resultados.
        """
        params: dict[str, Any] = {
            "rows": min(rows, MAX_ROWS),
            "offset": offset,
        }
        return self._get("/types", params)

    def get_type(self, type_id: str) -> dict[str, Any]:
        """
        Recupera información sobre un tipo de contenido Crossref.

        Args:
            type_id: Identificador del tipo (ej. 'journal-article', 'book-chapter').
        """
        return self._get(f"/types/{type_id}", {})

    def get_type_works(
        self,
        type_id: str,
        query: Optional[str] = None,
        query_bibliographic: Optional[str] = None,
        query_author: Optional[str] = None,
        filter: Optional[str] = None,
        select: Optional[str] = None,
        sort: Optional[str] = None,
        order: Optional[str] = None,
        rows: int = DEFAULT_ROWS,
        offset: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Retorna obras del tipo de contenido especificado.

        Args:
            type_id: Identificador del tipo (ej. 'journal-article').
            query: Búsqueda libre.
            query_bibliographic: Búsqueda en campos bibliográficos.
            query_author: Búsqueda por nombre de autor.
            filter: Filtro Crossref.
            select: Campos a retornar.
            sort: Campo de ordenamiento.
            order: Dirección del ordenamiento.
            rows: Cantidad de resultados por página.
            offset: Saltar los primeros N resultados.
            cursor: Token de cursor para paginación.
        """
        params: dict[str, Any] = {
            "query": query,
            "query.bibliographic": query_bibliographic,
            "query.author": query_author,
            "filter": filter,
            "select": select,
            "sort": sort,
            "order": order,
            "rows": min(rows, MAX_ROWS),
            "offset": offset,
            "cursor": cursor,
        }
        return self._get(f"/types/{type_id}/works", params)

    # ──────────────────────────────────────────────────────────────────────────
    # Licenses
    # ──────────────────────────────────────────────────────────────────────────

    def list_licenses(
        self,
        query: Optional[str] = None,
        rows: int = DEFAULT_ROWS,
        offset: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Lista las licencias registradas en Crossref.

        Args:
            query: Búsqueda libre por nombre de licencia.
            rows: Cantidad de resultados por página.
            offset: Saltar los primeros N resultados.
            cursor: Token de cursor para paginación.
        """
        params: dict[str, Any] = {
            "query": query,
            "rows": min(rows, MAX_ROWS),
            "offset": offset,
            "cursor": cursor,
        }
        return self._get("/licenses", params)
