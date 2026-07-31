"""
Deduplicator MCP — HTTP Client

Wraps the Duplicate Detector Django REST API with a thin httpx client.
Handles multipart/form-data uploads and raw binary responses (downloadable CSVs).

Endpoints covered:
  POST /deduplicator/process/         → detect duplicate documents
  POST /deduplicator/check-authority/ → match document authors against a reference list
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from config import DEDUPLICATOR_BASE_URL

logger = logging.getLogger(__name__)

# Max time to wait for a response — deduplication can be slow for large CSVs
_DEFAULT_TIMEOUT = 120.0


class DeduplicatorClient:
    """Synchronous HTTP client for the Duplicate Detector Django REST API."""

    def __init__(self, base_url: str = DEDUPLICATOR_BASE_URL) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self._base_url, timeout=_DEFAULT_TIMEOUT)
        logger.info("DeduplicatorClient ready — base_url=%s", self._base_url)

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def detect_duplicates(
        self,
        csv_file1_path: str,
        csv_file2_path: str,
        source_name: str,
        output_type: str = "csv",
    ) -> bytes:
        """
        Send two CSV files to POST /deduplicator/process/ and return the
        raw response body (a downloadable duplicados.csv).

        Args:
            csv_file1_path: Absolute path to the source repository CSV (csv_file1).
            csv_file2_path: Absolute path to the destination repository CSV (csv_file2).
            source_name: Name of the source repository (e.g. "SEDICI").
            output_type: Output format — currently only "csv" is supported.

        Returns:
            Raw bytes of the resulting CSV file.

        Raises:
            httpx.HTTPStatusError: if the Django API returns a non-2xx status.
            FileNotFoundError: if either CSV path does not exist.
        """
        path1 = Path(csv_file1_path)
        path2 = Path(csv_file2_path)
        _assert_exists(path1)
        _assert_exists(path2)

        with path1.open("rb") as f1, path2.open("rb") as f2:
            files = {
                "csv_file1": (path1.name, f1, "text/csv"),
                "csv_file2": (path2.name, f2, "text/csv"),
            }
            data = {
                "source_name": source_name,
                "output_type": output_type,
            }
            logger.info(
                "POST /deduplicator/process/ — file1=%s  file2=%s  source=%s",
                path1.name,
                path2.name,
                source_name,
            )
            response = self._client.post(
                "/deduplicator/process/",
                files=files,
                data=data,
            )
            response.raise_for_status()
            return response.content

    def check_authority(
        self,
        resource_list_path: str,
        author_list_path: str,
        scopus: bool,
        output_type: str = "csv",
    ) -> bytes:
        """
        Send a resource CSV and an author list CSV to POST /deduplicator/check-authority/
        and return the raw response body (a downloadable coincidencias_autores.csv).

        Args:
            resource_list_path: Absolute path to the document list CSV (resource_list).
            author_list_path: Absolute path to the author reference list CSV (author_list).
            scopus: True if author_list is in Scopus format; False for simple Apellido/Nombre format.
            output_type: Output format — currently only "csv" is supported.

        Returns:
            Raw bytes of the resulting CSV file.

        Raises:
            httpx.HTTPStatusError: if the Django API returns a non-2xx status.
            FileNotFoundError: if either CSV path does not exist.
        """
        resource_path = Path(resource_list_path)
        author_path = Path(author_list_path)
        _assert_exists(resource_path)
        _assert_exists(author_path)

        with resource_path.open("rb") as fr, author_path.open("rb") as fa:
            files = {
                "resource_list": (resource_path.name, fr, "text/csv"),
                "author_list": (author_path.name, fa, "text/csv"),
            }
            data = {
                "output_type": output_type,
                "scopus": "True" if scopus else "False",
            }
            logger.info(
                "POST /deduplicator/check-authority/ — resources=%s  authors=%s  scopus=%s",
                resource_path.name,
                author_path.name,
                scopus,
            )
            response = self._client.post(
                "/deduplicator/check-authority/",
                files=files,
                data=data,
            )
            response.raise_for_status()
            return response.content


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _assert_exists(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")
