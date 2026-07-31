"""
Springer MCP — Configuration

Reads the Springer Nature API key from the environment (.env or Docker).
Register your free API key at: https://dev.springernature.com/
"""

import os
from dotenv import load_dotenv

# Load .env when running outside Docker (local development)
load_dotenv()

SPRINGER_BASE_URL: str = "https://api.springernature.com"

# Required: Springer Nature Open Access API key
SPRINGER_API_KEY: str = os.environ.get("SPRINGER_API_KEY", "")
