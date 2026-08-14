"""
Crossref MCP — Configuración
Lee parámetros de conexión desde el entorno (.env o Docker).
"""

import os
from dotenv import load_dotenv

load_dotenv()

CROSSREF_BASE_URL: str = "https://api.crossref.org"

# Email para el «polite pool» — mayor límite de tasa según Crossref
# https://www.crossref.org/documentation/retrieve-metadata/rest-api/
CROSSREF_EMAIL: str = os.environ.get("CROSSREF_EMAIL", "")

# User-Agent personalizado (recomendado por Crossref)
CROSSREF_USER_AGENT: str = os.environ.get(
    "CROSSREF_USER_AGENT",
    "CrossrefMCP/1.0 (LangGraph orchestrator; mailto:{email})",
)