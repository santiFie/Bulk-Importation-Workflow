"""
OpenAlex MCP — Configuration
Reads the API key from the environment (.env or Docker).
"""

import os
from dotenv import load_dotenv

load_dotenv()

OPENALEX_BASE_URL: str = "https://api.openalex.org"

# Optional: polite pool email (recommended by OpenAlex for higher rate limits)
OPENALEX_API_KEY: str = os.environ.get("OPENALEX_API_KEY", "")
OPENALEX_EMAIL: str = os.environ.get("OPENALEX_EMAIL", "") 
