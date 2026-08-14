"""
Crossref MCP — Journals Tools

Expone tres herramientas:
  • search_journals      — lista / búsqueda de revistas.
  • get_journal          — recupera una revista por ISSN.
  • get_journal_works    — obras publicadas en una revista.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

import httpx

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from crossref_client import CrossrefClient

logger = logging.getLogger(__name__)


def register(mcp: "FastMCP", client: "CrossrefClient") -> None:

    @mcp.tool()
    def search_journals(
        query: Optional[str] = None,
        rows: int = 20,
        offset: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Lista o busca revistas científicas registradas en la base de datos Crossref.

        Args:
            query: Búsqueda libre por nombre de la revista.
                   Ejemplo: 'Nature', 'Journal of Machine Learning Research'.
            rows: Resultados por página, máx. 1000 (por defecto 20).
            offset: Saltar los primeros N resultados.
            cursor: Token de cursor para paginación (usar '*' en la primera llamada).

        Returns:
            Dict con:
              - total_results  : total de revistas encontradas
              - items          : lista de objetos Journal
              - next_cursor    : token para la página siguiente (si aplica)
        """
        try:
            data = client.search_journals(query=query, rows=rows, offset=offset, cursor=cursor)
        except httpx.HTTPStatusError as exc:
            return {"error": f"Crossref API error {exc.response.status_code}: {exc.response.text}"}
        except httpx.RequestError as exc:
            return {"error": f"Error de red: {exc}"}

        msg = data.get("message", {})
        return {
            "total_results": msg.get("total-results"),
            "items_per_page": msg.get("items-per-page"),
            "next_cursor": msg.get("next-cursor"),
            "items": msg.get("items", []),
        }

    @mcp.tool()
    def get_journal(issn: str) -> dict[str, Any]:
        """
        Recupera información detallada de una revista a partir de su ISSN.

        Incluye título, editorial, recuentos de DOIs, cobertura de metadatos
        y banderas de depósito.

        Args:
            issn: ISSN de la revista, con o sin guión (ej. '0302-9743' o '03029743').

        Returns:
            Objeto Journal con metadatos completos, o dict de error.
        """
        try:
            data = client.get_journal(issn=issn)
        except httpx.HTTPStatusError as exc:
            return {"error": f"Crossref API error {exc.response.status_code}: {exc.response.text}"}
        except httpx.RequestError as exc:
            return {"error": f"Error de red: {exc}"}

        return data.get("message", data)

    @mcp.tool()
    def get_journal_works(
        issn: str,
        query: Optional[str] = None,
        query_bibliographic: Optional[str] = None,
        query_author: Optional[str] = None,
        filter: Optional[str] = None,
        select: Optional[str] = None,
        sort: Optional[str] = None,
        order: Optional[str] = None,
        rows: int = 20,
        offset: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Retorna las obras publicadas en la revista identificada por su ISSN.

        Args:
            issn: ISSN de la revista (ej. '0302-9743').
            query: Búsqueda libre dentro de las obras de la revista.
            query_bibliographic: Búsqueda en campos bibliográficos.
            query_author: Búsqueda por nombre de autor.
            filter: Filtro Crossref adicional (ej. 'from-pub-date:2020').
            select: Campos a retornar separados por coma.
            sort: Campo de ordenamiento (ej. 'published', 'is-referenced-by-count').
            order: 'asc' o 'desc'.
            rows: Resultados por página (máx. 1000).
            offset: Saltar los primeros N resultados.
            cursor: Token de cursor; usar '*' en la primera llamada.

        Returns:
            Dict con total_results, items_per_page, next_cursor e items.
        """
        try:
            data = client.get_journal_works(
                issn=issn,
                query=query,
                query_bibliographic=query_bibliographic,
                query_author=query_author,
                filter=filter,
                select=select,
                sort=sort,
                order=order,
                rows=rows,
                offset=offset,
                cursor=cursor,
            )
        except httpx.HTTPStatusError as exc:
            return {"error": f"Crossref API error {exc.response.status_code}: {exc.response.text}"}
        except httpx.RequestError as exc:
            return {"error": f"Error de red: {exc}"}

        msg = data.get("message", {})
        return {
            "total_results": msg.get("total-results"),
            "items_per_page": msg.get("items-per-page"),
            "next_cursor": msg.get("next-cursor"),
            "items": msg.get("items", []),
        }
