"""
Crossref MCP — Members Tools

Expone tres herramientas:
  • search_members    — lista / búsqueda de miembros (editores).
  • get_member        — recupera un miembro por ID numérico.
  • get_member_works  — obras depositadas por un miembro.
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
    def search_members(
        query: Optional[str] = None,
        filter: Optional[str] = None,
        rows: int = 20,
        offset: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Lista o busca organizaciones (editores) registradas como miembros de Crossref.

        Args:
            query: Búsqueda libre por nombre del miembro / editorial.
                   Ejemplo: 'Elsevier', 'Springer', 'Taylor Francis'.
            filter: Filtros disponibles:
                   - 'backfile-doi-count:<N>' — miembros con N DOIs en archivo histórico.
                   - 'current-doi-count:<N>'  — miembros con N DOIs recientes.
                   - 'prefix:<prefix>'         — miembros con ese prefijo DOI.
            rows: Resultados por página, máx. 1000 (por defecto 20).
            offset: Saltar los primeros N resultados.
            cursor: Token de cursor para paginación (usar '*' en la primera llamada).

        Returns:
            Dict con total_results, items_per_page, next_cursor e items (lista de Member).
        """
        try:
            data = client.search_members(
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
    def get_member(member_id: int) -> dict[str, Any]:
        """
        Recupera metadatos detallados de un miembro Crossref por su ID numérico.

        Incluye nombre, prefijos DOI, recuentos de depósitos y métricas de cobertura
        de metadatos (resúmenes, ORCID, referencias, etc.).

        Args:
            member_id: ID numérico del miembro.
                       Ejemplos: 324 (Elsevier), 311 (Wiley), 297 (Springer).
                       Para encontrar el ID usar search_members primero.

        Returns:
            Objeto Member con metadatos completos, o dict de error.
        """
        try:
            data = client.get_member(member_id=member_id)
        except httpx.HTTPStatusError as exc:
            return {"error": f"Crossref API error {exc.response.status_code}: {exc.response.text}"}
        except httpx.RequestError as exc:
            return {"error": f"Error de red: {exc}"}

        return data.get("message", data)

    @mcp.tool()
    def get_member_works(
        member_id: int,
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
        Retorna las obras depositadas por el miembro Crossref especificado.

        Args:
            member_id: ID numérico del miembro (ej. 324 para Elsevier).
                       Obtener el ID usando search_members previamente.
            query: Búsqueda libre dentro de las obras del miembro.
            query_bibliographic: Búsqueda en campos bibliográficos (título, autor, ISSN, año).
            query_author: Búsqueda por nombre de autor.
            filter: Filtro Crossref adicional (ej. 'type:journal-article').
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
            data = client.get_member_works(
                member_id=member_id,
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
