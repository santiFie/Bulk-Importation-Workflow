"""
Deduplicator MCP — Deduplicator Tool

Exposes the `detect_duplicates` MCP tool which calls POST /deduplicator/process/
on the Django REST API and saves the resulting CSV to disk.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from deduplicator_client import DeduplicatorClient

logger = logging.getLogger(__name__)

# Default output directory inside the container (bind-mounted from the host)
_DEFAULT_OUTPUT_DIR = "/app/data"


def register(mcp: "FastMCP", client: "DeduplicatorClient") -> None:

    @mcp.tool()
    def detect_duplicates(
        csv_file1_path: str,
        csv_file2_path: str,
        source_name: str,
        output_filename: str = "duplicados.csv",
        output_dir: str = _DEFAULT_OUTPUT_DIR,
    ) -> dict[str, Any]:
        """
        Detect duplicate documents between two CSV repositories using Levenshtein
        similarity rules on title, authors, and publication date.

        Calls POST /deduplicator/process/ on the Duplicate Detector Django service
        with both CSV files and saves the resulting duplicate report to disk.

        CSV input format (each file):
            source,title,id,author,date
            SEDICI,Introduction to Machine Learning,123,Doe John;Smith Jane;,2020

          - `author`: authors separated by ";" with trailing ";"
          - `date`: publication year (or comma-separated list — the minimum is used)

        Output CSV columns:
            title, duplicate, original_id, duplicate_id, author, <rule_columns...>, total

        Args:
            csv_file1_path: Absolute path to the source repository CSV (csv_file1).
            csv_file2_path: Absolute path to the destination repository CSV (csv_file2).
            source_name: Name of the source repository (e.g. "SEDICI").
            output_filename: Name for the output file (default "duplicados.csv").
            output_dir: Directory where the output CSV will be saved (default "/app/data").

        Returns:
            A dict with:
              - "csv_path"   — absolute path of the saved CSV file
              - "row_count"  — number of data rows in the result (header excluded)
              - "status"     — "ok" on success
            Or a dict with "error" on failure.
        """
        try:
            csv_bytes = client.detect_duplicates(
                csv_file1_path=csv_file1_path,
                csv_file2_path=csv_file2_path,
                source_name=source_name,
                output_type="csv",
            )
        except FileNotFoundError as exc:
            return {"error": str(exc)}
        except Exception as exc:
            return {"error": f"Deduplicator API error: {exc}"}

        return _save_csv(csv_bytes, output_dir, output_filename)


# ──────────────────────────────────────────────────────────────────────────────
# Shared helper
# ──────────────────────────────────────────────────────────────────────────────


def _save_csv(csv_bytes: bytes, output_dir: str, filename: str) -> dict[str, Any]:
    """Write *csv_bytes* to *output_dir/filename* and return a status dict."""
    try:
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, filename)
        with open(output_path, "wb") as fh:
            fh.write(csv_bytes)
        # Count data rows (subtract 1 for the header line)
        row_count = max(0, len(csv_bytes.split(b"\n")) - 2)
        logger.info("Saved %d-row CSV to %s", row_count, output_path)
        return {"status": "ok", "csv_path": output_path, "row_count": row_count}
    except OSError as exc:
        return {"error": f"Failed to write output file: {exc}"}
