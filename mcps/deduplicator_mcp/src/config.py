"""
Deduplicator MCP — Configuration

Reads the base URL of the Duplicate Detector Django service from the environment.
Set DEDUPLICATOR_BASE_URL in .env or as a Docker environment variable.
"""

import os
from dotenv import load_dotenv

# Load .env when running outside Docker (local development)
load_dotenv()

DEDUPLICATOR_BASE_URL: str = (
    os.environ.get("DEDUPLICATOR_BASE_URL", "http://host.docker.internal:9010").rstrip("/")
)
