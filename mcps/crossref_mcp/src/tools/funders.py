"""
Crossref MCP — Funders Tools

Expone tres herramientas:
  • search_funders    — lista / búsqueda de financiadores.
  • get_funder        — recupera un financiador por ID.
  • get_funder_works  — obras financiadas por un financiador.
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
    def search_funders(
        query: Optional[str] = None,
        filter: Optional[str] = None,
        rows: int = 20,
        offset: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Lista o busca financiadores registrados en el Crossref Funder Registry.

        Args:
            query: Búsqueda libre por nombre del financiador.
                   Ejemplo: 'National Science Foundation', 'CONICET'.
            filter: Filtrar por ubicación geográfica.
                   Formato: 'location:<país>' (ej. 'location:Argentina').
            rows: Resultados por página, máx. 1000 (por defecto 20).
            offset: Saltar los primeros N resultados.
            cursor: Token de cursor para paginación (usar '*' en la primera llamada).

        Returns:
            Dict con total_results, items_per_page, next_cursor e items (lista de Funder).
        """
        try:
            data = client.search_funders(
                query=query, filter=filter, rows=rows, offset=offset, cursor=cursor
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

    @mcp.tool()
    def get_funder(funder_id: str) -> dict[str, Any]:
        """
        Recupera metadatos de un financiador y sus suborganizaciones.

        Args:
            funder_id: ID del financiador en el Funder Registry.
                       Ejemplo: '501100006004' (CONICET), '100000001' (NSF).
                       Para encontrar el ID usar search_funders primero.

        Returns:
            Objeto FunderFull con nombre, jerarquía, cantidad de obras y suborganizaciones.
        """
        try:
            data = client.get_funder(funder_id=funder_id)
        except httpx.HTTPStatusError as exc:
            return {"error": f"Crossref API error {exc.response.status_code}: {exc.response.text}"}
        except httpx.RequestError as exc:
            return {"error": f"Error de red: {exc}"}

        return data.get("message", data)

    @mcp.tool()
    def get_funder_works(
        funder_id: str,
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
        Retorna las obras académicas asociadas al financiador especificado.

        Args:
            funder_id: ID del financiador (ej. '501100006004').
                       Obtener el ID usando search_funders previamente.
            query: Búsqueda libre dentro de las obras del financiador.
            query_bibliographic: Búsqueda en campos bibliográficos.
            query_author: Búsqueda por nombre de autor.
            filter: Filtro Crossref adicional (ej. 'from-pub-date:2020').
            select: Campos a retornar separados por coma.
            sort: Campo de ordenamiento.
            order: 'asc' o 'desc'.
            rows: Resultados por página (máx. 1000).
            offset: Saltar los primeros N resultados.
            cursor: Token de cursor; usar '*' en la primera llamada.

        Returns:
            Dict con total_results, items_per_page, next_cursor e items.
        """
        try:
            data = client.get_funder_works(
                funder_id=funder_id,
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
