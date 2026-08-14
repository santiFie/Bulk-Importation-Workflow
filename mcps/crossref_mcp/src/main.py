"""
Crossref MCP Server — Entrypoint

Inicia un servidor FastMCP vía transporte stdio.

Secuencia de arranque:
  1. Leer email y configuración desde el entorno (via config.py).
  2. Instanciar el CrossrefClient.
  3. Registrar todas las herramientas en la instancia MCP.
  4. Ejecutar via stdio (compatible con LangGraph / langchain-mcp-adapters).
"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,  # el transporte stdio usa stdout; mantener logs en stderr
)
logger = logging.getLogger(__name__)

from fastmcp import FastMCP
from crossref_client import CrossrefClient
from tools import register_all

client = CrossrefClient()

mcp = FastMCP(
    name="Crossref MCP",
    instructions=(
        "Este servidor MCP provee herramientas para consultar la API REST de Crossref, "
        "el mayor registro de metadatos bibliográficos del mundo con más de 150 millones de DOIs.\n\n"
        "Entidades disponibles:\n"
        "  • Works    — artículos, libros, actas, datasets, preprints, etc. Búsqueda por DOI,\n"
        "               texto libre, campos bibliográficos, autores y filtros avanzados.\n"
        "  • Journals — revistas científicas con métricas de cobertura de metadatos.\n"
        "  • Funders  — financiadores del Crossref Funder Registry (ej. NSF, CONICET).\n"
        "  • Members  — organizaciones (editores) registradas en Crossref.\n"
        "  • Prefixes — propietarios de prefijos DOI (ej. 10.1016 → Elsevier).\n"
        "  • Types    — tipos de contenido válidos (journal-article, book-chapter, etc.).\n"
        "  • Licenses — licencias de uso registradas (Creative Commons y otras).\n\n"
        "Guía de uso:\n"
        "  - Para buscar una obra por referencia bibliográfica: usar search_works con query_bibliographic.\n"
        "  - Para paginar conjuntos grandes (>1000 resultados): usar el parámetro cursor.\n"
        "  - Para reducir el tamaño del contexto: usar el parámetro select con los campos necesarios.\n"
        "  - Flujo típico: search_* → obtener ID → get_* o get_*_works."
    ),
)

register_all(mcp, client)

if __name__ == "__main__":
    logger.info("Iniciando Crossref MCP Server (transporte stdio)")
    mcp.run(transport="stdio")
