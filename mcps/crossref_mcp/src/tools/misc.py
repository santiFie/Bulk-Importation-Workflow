"""
Crossref MCP — Prefixes, Types & Licenses Tools

Expone cinco herramientas:
  • get_prefix         — metadatos del propietario de un prefijo DOI.
  • get_prefix_works   — obras asociadas a un prefijo DOI.
  • list_types         — lista todos los tipos de contenido Crossref.
  • get_type           — información de un tipo de contenido por ID.
  • get_type_works     — obras de un tipo de contenido específico.
  • list_licenses      — lista licencias registradas en Crossref.
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

    # ── Prefixes ──────────────────────────────────────────────────────────────

    @mcp.tool()
    def get_prefix(prefix: str) -> dict[str, Any]:
        """
        Recupera metadatos del propietario de un prefijo DOI.

        Args:
            prefix: Prefijo DOI (ej. '10.1016' para Elsevier, '10.1038' para Nature).

        Returns:
            Objeto Prefix con miembro propietario y nombre del prefijo.
        """
        try:
            data = client.get_prefix(prefix=prefix)
        except httpx.HTTPStatusError as exc:
            return {"error": f"Crossref API error {exc.response.status_code}: {exc.response.text}"}
        except httpx.RequestError as exc:
            return {"error": f"Error de red: {exc}"}

        return data.get("message", data)

    @mcp.tool()
    def get_prefix_works(
        prefix: str,
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
        Retorna las obras asociadas al prefijo DOI especificado.

        Args:
            prefix: Prefijo DOI (ej. '10.1016', '10.1038').
            query: Búsqueda libre dentro de las obras del prefijo.
            query_bibliographic: Búsqueda en campos bibliográficos.
            query_author: Búsqueda por nombre de autor.
            filter: Filtro Crossref adicional.
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
            data = client.get_prefix_works(
                prefix=prefix,
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

    # ── Types ─────────────────────────────────────────────────────────────────

    @mcp.tool()
    def list_types(rows: int = 50, offset: Optional[int] = None) -> dict[str, Any]:
        """
        Lista todos los tipos de contenido válidos en Crossref.

        Los tipos son usados en filtros con 'type:<id>' (ej. 'type:journal-article').

        Args:
            rows: Resultados por página (por defecto 50; hay ~20 tipos en total).
            offset: Saltar los primeros N resultados.

        Returns:
            Dict con total_results e items (lista de objetos Type con id y label).
        """
        try:
            data = client.list_types(rows=rows, offset=offset)
        except httpx.HTTPStatusError as exc:
            return {"error": f"Crossref API error {exc.response.status_code}: {exc.response.text}"}
        except httpx.RequestError as exc:
            return {"error": f"Error de red: {exc}"}

        msg = data.get("message", {})
        return {
            "total_results": msg.get("total-results"),
            "items": msg.get("items", []),
        }

    @mcp.tool()
    def get_type(type_id: str) -> dict[str, Any]:
        """
        Recupera información sobre un tipo de contenido Crossref por su ID.

        Args:
            type_id: Identificador del tipo. Ejemplos:
                     'journal-article', 'book-chapter', 'proceedings-article',
                     'dataset', 'monograph', 'report', 'preprint'.
                     Usar list_types para ver todos los tipos disponibles.

        Returns:
            Objeto Type con id y label descriptivo.
        """
        try:
            data = client.get_type(type_id=type_id)
        except httpx.HTTPStatusError as exc:
            return {"error": f"Crossref API error {exc.response.status_code}: {exc.response.text}"}
        except httpx.RequestError as exc:
            return {"error": f"Error de red: {exc}"}

        return data.get("message", data)

    @mcp.tool()
    def get_type_works(
        type_id: str,
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
        Retorna obras del tipo de contenido especificado.

        Args:
            type_id: Identificador del tipo (ej. 'journal-article', 'book-chapter').
                     Usar list_types para ver todos los tipos disponibles.
            query: Búsqueda libre dentro de las obras del tipo.
            query_bibliographic: Búsqueda en campos bibliográficos.
            query_author: Búsqueda por nombre de autor.
            filter: Filtro Crossref adicional.
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
            data = client.get_type_works(
                type_id=type_id,
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

    # ── Licenses ──────────────────────────────────────────────────────────────

    @mcp.tool()
    def list_licenses(
        query: Optional[str] = None,
        rows: int = 20,
        offset: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Lista las licencias de uso registradas en Crossref.

        Incluye licencias Creative Commons, licencias de editores y otras.
        Útil para filtrar obras por licencia usando filter='license:<url>'.

        Args:
            query: Búsqueda libre por nombre o URL de licencia.
            rows: Resultados por página (por defecto 20).
            offset: Saltar los primeros N resultados.
            cursor: Token de cursor para paginación.

        Returns:
            Dict con total_results e items (lista de objetos License con URL y work-count).
        """
        try:
            data = client.list_licenses(query=query, rows=rows, offset=offset, cursor=cursor)
        except httpx.HTTPStatusError as exc:
            return {"error": f"Crossref API error {exc.response.status_code}: {exc.response.text}"}
        except httpx.RequestError as exc:
            return {"error": f"Error de red: {exc}"}

        msg = data.get("message", {})
        return {
            "total_results": msg.get("total-results"),
            "items": msg.get("items", []),
        }
