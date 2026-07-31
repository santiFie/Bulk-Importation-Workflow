"""
Springer MCP — Export Tools

Exposes two MCP tools for exporting search results to CSV:
  • export_search_to_csv      — Run a search and save results as a raw CSV
                                with all Springer fields.
  • export_for_deduplication  — Run a search and save results as a CSV in the
                                format expected by the Deduplicator MCP
                                (source, title, id, author, date).
"""

from __future__ import annotations

import csv
import io
import logging
import os
from typing import TYPE_CHECKING, Any, Optional

import httpx

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from springer_client import SpringerClient

logger = logging.getLogger(__name__)

# Default output directory inside the container (bind-mounted from the host)
_DEFAULT_OUTPUT_DIR = "/app/data"

# Separator used by the Deduplicator MCP for the `author` field
_AUTHOR_SEPARATOR = ";"

# Source label injected in the deduplication CSV
_SOURCE_LABEL = "SPRINGER"


def register(mcp: "FastMCP", client: "SpringerClient") -> None:

    @mcp.tool()
    def export_search_to_csv(
        q: str,
        output_filename: str = "springer_results.csv",
        output_dir: str = _DEFAULT_OUTPUT_DIR,
        page_size: int = 100,
        max_records: int = 500,
        use_metadata_api: bool = False,
    ) -> dict[str, Any]:
        """
        Search Springer Nature and export all results to a CSV file.

        Performs paginated requests until ``max_records`` are fetched or the
        result set is exhausted, then writes a CSV with the full Springer fields.

        CSV columns:
          identifier, title, doi, publicationDate, publicationName, publisher,
          publicationType, openaccess, subject, keyword, abstract, creators, url

        Args:
            q:                 Lucene query (see search_open_access for syntax).
            output_filename:   Name of the CSV file to write (default "springer_results.csv").
            output_dir:        Directory to write the CSV into (default "/app/data").
            page_size:         Records per API request (max 100, default 100).
            max_records:       Maximum total records to fetch (default 500).
            use_metadata_api:  If True, query /meta/v2/json instead of /openaccess/json.

        Returns:
            A dict with:
              - status      : "ok" on success
              - csv_path    : absolute path of the saved CSV
              - row_count   : number of data rows written (header excluded)
              - total_found : total results reported by the API
        """
        records, total_found = _fetch_all(
            client=client,
            q=q,
            page_size=page_size,
            max_records=max_records,
            use_metadata_api=use_metadata_api,
        )
        if isinstance(records, dict) and "error" in records:
            return records  # propagate error

        fieldnames = [
            "identifier", "title", "doi", "publicationDate",
            "publicationName", "publisher", "publicationType", "openaccess",
            "subject", "keyword", "abstract", "creators", "url",
        ]

        rows = [_record_to_full_row(r) for r in records]
        return _write_csv(rows, fieldnames, output_dir, output_filename, total_found)

    @mcp.tool()
    def export_for_deduplication(
        q: str,
        output_filename: str = "springer_dedup.csv",
        output_dir: str = _DEFAULT_OUTPUT_DIR,
        page_size: int = 100,
        max_records: int = 500,
        use_metadata_api: bool = False,
    ) -> dict[str, Any]:
        """
        Search Springer Nature and export results as a deduplication-ready CSV.

        The output format matches the input required by the ``detect_duplicates``
        tool in the Deduplicator MCP:

            source,title,id,author,date

        Where:
          - source  : always "SPRINGER"
          - title   : article title
          - id      : DOI (or Springer identifier if DOI is absent)
          - author  : authors joined by ";" with a trailing ";"
                      e.g. "Doe John;Smith Jane;"
          - date    : publication year extracted from publicationDate

        This file can be passed directly as ``csv_file1_path`` or
        ``csv_file2_path`` to the Deduplicator MCP's ``detect_duplicates`` tool.

        Args:
            q:                 Lucene query (see search_open_access for syntax).
            output_filename:   Name of the CSV file to write (default "springer_dedup.csv").
            output_dir:        Directory to write the CSV into (default "/app/data").
            page_size:         Records per API request (max 100, default 100).
            max_records:       Maximum total records to fetch (default 500).
            use_metadata_api:  If True, query /meta/v2/json instead of /openaccess/json.

        Returns:
            A dict with:
              - status      : "ok" on success
              - csv_path    : absolute path of the saved CSV
              - row_count   : number of data rows written (header excluded)
              - total_found : total results reported by the API
        """
        records, total_found = _fetch_all(
            client=client,
            q=q,
            page_size=page_size,
            max_records=max_records,
            use_metadata_api=use_metadata_api,
        )
        if isinstance(records, dict) and "error" in records:
            return records

        fieldnames = ["source", "title", "id", "author", "date"]
        rows = [_record_to_dedup_row(r) for r in records]
        return _write_csv(rows, fieldnames, output_dir, output_filename, total_found)


# ──────────────────────────────────────────────────────────────────────────────
# Private helpers
# ──────────────────────────────────────────────────────────────────────────────


def _fetch_all(
    client: "SpringerClient",
    q: str,
    page_size: int,
    max_records: int,
    use_metadata_api: bool,
) -> tuple[list[dict[str, Any]] | dict[str, Any], int]:
    """
    Paginate through the Springer API and collect up to *max_records* records.

    Returns:
        (records_list, total_found)  — or (error_dict, 0) on failure.
    """
    page_size = min(page_size, 100)
    records: list[dict[str, Any]] = []
    total_found = 0
    start = 1

    search_fn = client.search_metadata if use_metadata_api else client.search_open_access

    while len(records) < max_records:
        try:
            data = search_fn(q=q, page_size=page_size, start=start)
        except httpx.HTTPStatusError as exc:
            return {"error": f"Springer API error {exc.response.status_code}: {exc.response.text}"}, 0
        except httpx.RequestError as exc:
            return {"error": f"Network error: {exc}"}, 0

        result_meta = data.get("result", [{}])[0]
        total_found = int(result_meta.get("total", 0))
        batch = data.get("records", [])

        if not batch:
            break

        records.extend(batch)
        logger.info("Fetched %d / %d  (total available: %d)", len(records), max_records, total_found)

        if len(records) >= total_found:
            break

        start += len(batch)

    return records[:max_records], total_found


def _extract_authors(record: dict[str, Any]) -> str:
    """Return authors as 'Lastname Firstname;' separated string."""
    creators = record.get("creators", [])
    parts = []
    for c in creators:
        name = c.get("creator", "").strip()
        if name:
            parts.append(name)
    return _AUTHOR_SEPARATOR.join(parts) + (_AUTHOR_SEPARATOR if parts else "")


def _extract_year(record: dict[str, Any]) -> str:
    """Extract publication year from publicationDate (ISO format YYYY-MM-DD)."""
    date_str = record.get("publicationDate", "")
    if date_str and len(date_str) >= 4:
        return date_str[:4]
    return ""


def _record_to_full_row(record: dict[str, Any]) -> dict[str, Any]:
    """Convert a Springer API record to a flat CSV row with all key fields."""
    subjects = record.get("subject", [])
    if isinstance(subjects, list):
        subjects = " | ".join(subjects)

    keywords = record.get("keyword", [])
    if isinstance(keywords, list):
        keywords = " | ".join(keywords)

    url_list = record.get("url", [])
    url_str = url_list[0].get("value", "") if url_list else ""

    return {
        "identifier": record.get("identifier", ""),
        "title": record.get("title", ""),
        "doi": record.get("doi", ""),
        "publicationDate": record.get("publicationDate", ""),
        "publicationName": record.get("publicationName", ""),
        "publisher": record.get("publisher", ""),
        "publicationType": record.get("publicationType", ""),
        "openaccess": record.get("openaccess", ""),
        "subject": subjects,
        "keyword": keywords,
        "abstract": record.get("abstract", ""),
        "creators": _extract_authors(record),
        "url": url_str,
    }


def _record_to_dedup_row(record: dict[str, Any]) -> dict[str, Any]:
    """Convert a Springer API record to the Deduplicator MCP CSV format."""
    doi = record.get("doi", "").strip()
    identifier = record.get("identifier", "").strip()
    return {
        "source": _SOURCE_LABEL,
        "title": record.get("title", ""),
        "id": doi if doi else identifier,
        "author": _extract_authors(record),
        "date": _extract_year(record),
    }


def _write_csv(
    rows: list[dict[str, Any]],
    fieldnames: list[str],
    output_dir: str,
    filename: str,
    total_found: int,
) -> dict[str, Any]:
    """Write rows to a CSV file and return a status dict."""
    try:
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, filename)

        with open(output_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        row_count = len(rows)
        logger.info("Saved %d-row CSV to %s (total available: %d)", row_count, output_path, total_found)
        return {
            "status": "ok",
            "csv_path": output_path,
            "row_count": row_count,
            "total_found": total_found,
        }
    except OSError as exc:
        return {"error": f"Failed to write output file: {exc}"}
