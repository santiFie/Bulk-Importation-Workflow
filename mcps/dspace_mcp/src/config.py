"""
Configuration module for the DSpace MCP Server.
Reads connection settings from environment variables (populated via .env / Docker).
"""

import os
from dotenv import load_dotenv

# Load .env when running outside Docker (local development)
load_dotenv()

BASE_URL: str = os.environ.get("DSPACE_BASE_URL", "http://host.docker.internal:8080/server").rstrip("/")
EMAIL: str = os.environ.get("DSPACE_EMAIL", "")
PASSWORD: str = os.environ.get("DSPACE_PASSWORD", "")

if not EMAIL or not PASSWORD:
    raise EnvironmentError(
        "DSPACE_EMAIL and DSPACE_PASSWORD must be set in the environment or .env file."
    )
