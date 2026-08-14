"""
Crossref MCP — Works Tools

Expone cuatro herramientas:
  • search_works      — búsqueda libre / filtrada de obras.
  • get_work          — recupera una obra por DOI.
  • get_work_agency   — retorna la agencia registrante de un DOI.
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
    def search_works(
        query: Optional[str] = None,
        query_bibliographic: Optional[str] = None,
        query_author: Optional[str] = None,
        query_title: Optional[str] = None,
        filter: Optional[str] = None,
        select: Optional[str] = None,
        sort: Optional[str] = None,
        order: Optional[str] = None,
        rows: int = 20,
        offset: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Busca obras académicas en el índice Crossref (artículos, libros, actas, etc.).

        Al menos uno de los parámetros de búsqueda o filtro debe proporcionarse.

        Args:
            query: Búsqueda libre en todos los campos de las obras.
                   Ejemplo: 'machine learning climate change'.
            query_bibliographic: Búsqueda en campos bibliográficos (título, autores,
                   ISSNs, año de publicación). Ideal para lookup de citas.
                   Ejemplo: 'Attention is all you need Vaswani 2017'.
            query_author: Búsqueda por nombre y apellido de autor.
                   Ejemplo: 'Yoshua Bengio'.
            query_title: Búsqueda solo en el campo título.
                   Ejemplo: 'deep learning'.
            filter: Filtro Crossref con uno o más criterios separados por coma.
                   Ejemplos:
                   - 'type:journal-article'
                   - 'from-pub-date:2020-01-01,until-pub-date:2023-12-31'
                   - 'has-abstract:true'
                   - 'is-referenced-by-count:100'
                   Ver filtros completos en:
                   https://www.crossref.org/documentation/retrieve-metadata/rest-api/rest-api-filters/
            select: Campos a retornar, separados por coma.
                   Campos comunes: DOI, title, author, published, container-title,
                   abstract, is-referenced-by-count, references-count, type,
                   publisher, ISSN, URL.
            sort: Campo de ordenamiento. Opciones: 'published', 'indexed',
                  'deposited', 'is-referenced-by-count', 'relevance', 'score'.
            order: Dirección del ordenamiento: 'asc' o 'desc' (por defecto 'desc').
            rows: Resultados por página, máx. 1000 (por defecto 20).
            offset: Saltar los primeros N resultados (máx. 10000). Preferir cursor.
            cursor: Token de paginación. Usar '*' en la primera llamada para obtener
                    el primer bloque; luego usar el valor de 'next-cursor' del response.

        Returns:
            Dict con:
              - message.total-results : total de obras encontradas
              - message.items         : lista de obras
              - message.next-cursor   : token para la página siguiente (si aplica)
        """
        try:
            data = client.search_works(
                query=query,
                query_bibliographic=query_bibliographic,
                query_author=query_author,
                query_title=query_title,
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

    @mcp.tool()
    def get_work(doi: str) -> dict[str, Any]:
        """
        Recupera los metadatos completos de una obra a partir de su DOI.

        Args:
            doi: Identificador DOI de la obra (ej. '10.5555/12345678').
                 No incluir el prefijo 'https://doi.org/'.

        Returns:
            Objeto Work con todos los metadatos disponibles, o un dict de error.
        """
        try:
            data = client.get_work(doi=doi)
        except httpx.HTTPStatusError as exc:
            return {"error": f"Crossref API error {exc.response.status_code}: {exc.response.text}"}
        except httpx.RequestError as exc:
            return {"error": f"Error de red: {exc}"}

        return data.get("message", data)

    @mcp.tool()
    def get_work_agency(doi: str) -> dict[str, Any]:
        """
        Determina qué agencia de registro es responsable de un DOI dado.

        Útil para saber si un DOI pertenece a Crossref, DataCite, JALC, etc.

        Args:
            doi: Identificador DOI (ej. '10.5555/12345678').

        Returns:
            Dict con el DOI y el objeto 'agency' (id + label).
        """
        try:
            data = client.get_work_agency(doi=doi)
        except httpx.HTTPStatusError as exc:
            return {"error": f"Crossref API error {exc.response.status_code}: {exc.response.text}"}
        except httpx.RequestError as exc:
            return {"error": f"Error de red: {exc}"}

        return data.get("message", data)
