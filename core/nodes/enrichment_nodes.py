"""
Nodos del subgrafo de Enriquecimiento de Metadatos.

Responsable de completar los metadatos de cada ítem a importar
consultando fuentes académicas externas luego de la deduplicación.

Estrategia de enriquecimiento por documento (cadena de responsabilidad):
  1. Tiene DOI     → Crossref   (fuente más autoritativa)
  2. DOI no hallado → DOI Negotiation (DataCite, Zenodo, SciELO, etc.)
  3. Sin DOI, ISSN → OpenAlex   (búsqueda por ISSN)
  4. Sin DOI ni ISSN, con título → OpenAlex (búsqueda por título)
  5. Tiene ISBN    → OpenLibrary (libros sin DOI)
  6. Sin información suficiente → se omite el enriquecimiento del ítem

Los enriquecedores son clientes HTTP directos (no agentes LLM) para
garantizar determinismo y eficiencia en lotes grandes.

Se realiza un health check pre-lote para detectar APIs indisponibles
antes de iterar sobre el CSV.
"""

import logging
import os
from typing import Any

import pandas as pd

from core.clients.enrichers.base_enricher import BaseEnricher, BaseEnricherError
from core.clients.enrichers.provider_factory import EnricherFactory
from core.clients.enrichers.crossref_enricher import CrossrefEnricherError
from core.clients.enrichers.openalex_enricher import OpenAlexEnricherError
from core.clients.enrichers.doi_negotiation_enricher import DOINegotiationEnricherError
from core.clients.enrichers.openlibrary_enricher import OpenLibraryEnricherError
from core.state import State

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Función de enrutamiento (usada como conditional_edge en el subgrafo)
# ---------------------------------------------------------------------------

def route_enrichment(state: State) -> str:
    """
    Determina si el enriquecimiento de metadatos está habilitado.

    Returns:
        "enrich" → proceder con el enriquecimiento.
        "skip"   → saltar el subgrafo directamente al ExportSubgraph.
    """
    if state.get("enrichment_enabled", False):
        return "enrich"
    return "skip"


# ---------------------------------------------------------------------------
# Health check pre-lote
# ---------------------------------------------------------------------------

def _check_provider_health() -> dict[str, bool]:
    """
    Verifica disponibilidad de los enriquecedores antes del procesamiento.

    Returns:
        Dict con True/False por nombre de provider. Los indisponibles
        se saltan silenciosamente durante el enriquecimiento.
    """
    status = EnricherFactory.health_check_all()
    availability = {}
    for name, info in status.items():
        available = info.get("available", False)
        availability[name] = available
        if not available:
            logger.warning(
                "[EnrichMetadata] Provider '%s' no disponible: %s",
                name, info.get("error", f"HTTP {info.get('status_code')}"),
            )
    return availability


# ---------------------------------------------------------------------------
# Nodo principal de enriquecimiento
# ---------------------------------------------------------------------------

def enrich_metadata_node(state: State) -> dict[str, Any]:
    """
    Nodo EnrichMetadata — Enriquece los metadatos del CSV reconciliado.

    Lee `reconciled_csv_path`, aplica la estrategia de enriquecimiento
    a cada fila y escribe el resultado de vuelta sobre el mismo archivo.
    Al finalizar, actualiza `enrichment_stats` con un resumen de la operación.

    La estrategia por fila es:
      - Tiene DOI:              Crossref → DOI Negotiation (fallback).
      - Sin DOI, tiene ISSN:    OpenAlex por ISSN.
      - Sin DOI ni ISSN:        OpenAlex por título (si hay título).
      - Tiene ISBN:             OpenLibrary (libros).
      - Sin ningún campo útil:  omite el ítem.

    Los campos enriquecidos se agregan al DataFrame como columnas adicionales
    con prefijo `crossref_`, `openalex_`, `doi_negotiation_` o `openlibrary_`
    según la fuente utilizada.
    """
    reconciled_path = state["reconciled_csv_path"]

    if not os.path.isfile(reconciled_path):
        logger.error(
            "[EnrichMetadata] CSV reconciliado no encontrado: '%s'", reconciled_path
        )
        return {"enrichment_stats": {"error": f"Archivo no encontrado: {reconciled_path}"}}

    # Health check pre-lote: detectar APIs indisponibles antes de iterar
    provider_health = _check_provider_health()

    crossref = EnricherFactory.create("crossref")
    openalex = EnricherFactory.create("openalex")
    doi_negotiation = EnricherFactory.create("doi_negotiation")
    openlibrary = EnricherFactory.create("openlibrary")

    df = pd.read_csv(reconciled_path)
    total = len(df)
    logger.info("[EnrichMetadata] Enriqueciendo %d ítems desde '%s'...", total, reconciled_path)

    stats: dict[str, int] = {
        "total": total,
        "enriched_crossref": 0,
        "enriched_doi_negotiation": 0,
        "enriched_openalex": 0,
        "enriched_openlibrary": 0,
        "skipped": 0,
        "errors": 0,
    }

    enriched_rows: list[dict] = []

    for idx, row in df.iterrows():
        extra = _enrich_row(
            row, crossref, openalex, doi_negotiation,
            openlibrary, provider_health, stats,
        )
        enriched_rows.append(extra)

    # Agregar columnas enriquecidas al DataFrame
    enrichment_df = pd.DataFrame(enriched_rows, index=df.index)
    for col in enrichment_df.columns:
        df[col] = enrichment_df[col]

    # Escribir de vuelta al mismo path (enriquecimiento in-place)
    df.to_csv(reconciled_path, index=False)

    logger.info(
        "[EnrichMetadata] Completado. Crossref: %d, DOI Negotiation: %d, "
        "OpenAlex: %d, OpenLibrary: %d, Omitidos: %d, Errores: %d",
        stats["enriched_crossref"], stats["enriched_doi_negotiation"],
        stats["enriched_openalex"], stats["enriched_openlibrary"],
        stats["skipped"], stats["errors"],
    )

    return {"enrichment_stats": stats}


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------

_DOI_CANDIDATES   = ["doi", "dc.identifier.doi", "DOI"]
_ISSN_CANDIDATES  = ["issn", "dc.identifier.issn", "ISSN"]
_TITLE_CANDIDATES = ["title", "dc.title", "titulo", "Title"]
_ISBN_CANDIDATES  = ["isbn", "dc.identifier.isbn", "ISBN"]


def _find_field(row: pd.Series, candidates: list[str]) -> str:
    """Devuelve el primer valor no vacío encontrado entre las columnas candidatas."""
    for col in candidates:
        val = row.get(col, "")
        if val and str(val).strip() not in ("", "nan", "None"):
            return str(val).strip()
    return ""


_ENRICHABLE_ERRORS = (
    BaseEnricherError,
    CrossrefEnricherError,
    OpenAlexEnricherError,
    DOINegotiationEnricherError,
    OpenLibraryEnricherError,
)


def _enrich_row(
    row: pd.Series,
    crossref: BaseEnricher,
    openalex: BaseEnricher,
    doi_negotiation: BaseEnricher,
    openlibrary: BaseEnricher,
    provider_health: dict[str, bool],
    stats: dict,
) -> dict:
    """
    Determina la estrategia de enriquecimiento para una fila y ejecuta la consulta.

    Cadena de responsabilidad:
      DOI → Crossref → DOI Negotiation → ISSN → Title → ISBN → skip

    Modifica `stats` in-place para llevar la cuenta de resultados.

    Returns:
        Diccionario con los campos enriquecidos (puede estar vacío si se omite).
    """
    doi   = _find_field(row, _DOI_CANDIDATES)
    issn  = _find_field(row, _ISSN_CANDIDATES)
    title = _find_field(row, _TITLE_CANDIDATES)
    isbn  = _find_field(row, _ISBN_CANDIDATES)

    try:
        # --- DOI: Crossref (fuente autoritativa) ---
        if doi and provider_health.get("crossref", True):
            enriched = crossref.enrich_by_doi(doi)
            if enriched:
                stats["enriched_crossref"] += 1
                return enriched

        # --- DOI: DOI Negotiation (fallback para DataCite, Zenodo, etc.) ---
        if doi and provider_health.get("doi_negotiation", True):
            enriched = doi_negotiation.enrich_by_doi(doi)
            if enriched:
                stats["enriched_doi_negotiation"] += 1
                return enriched

        # --- ISSN: OpenAlex ---
        if issn and provider_health.get("openalex", True):
            enriched = openalex.enrich_by_issn(issn)
            if enriched:
                stats["enriched_openalex"] += 1
                return enriched

        # --- Título: OpenAlex ---
        if title and provider_health.get("openalex", True):
            enriched = openalex.enrich_by_title(title)
            if enriched:
                stats["enriched_openalex"] += 1
                return enriched

        # --- ISBN: OpenLibrary (libros) ---
        if isbn and provider_health.get("openlibrary", True):
            enriched = openlibrary.enrich_by_isbn(isbn)
            if enriched:
                stats["enriched_openlibrary"] += 1
                return enriched

    except _ENRICHABLE_ERRORS as exc:
        logger.warning(
            "[EnrichMetadata] Error enriqueciendo fila (doi=%s, isbn=%s): %s",
            doi or "N/A", isbn or "N/A", exc,
        )
        stats["errors"] += 1
        return {}

    stats["skipped"] += 1
    return {}
