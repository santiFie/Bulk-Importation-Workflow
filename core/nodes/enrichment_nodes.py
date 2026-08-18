"""
Nodos del subgrafo de Enriquecimiento de Metadatos.

Responsable de completar los metadatos de cada ítem a importar
consultando fuentes académicas externas luego de la deduplicación.

Estrategia de enriquecimiento por documento:
  1. Tiene DOI     → Crossref   (fuente más autoritativa)
  2. Sin DOI, ISBN → OpenAlex   (búsqueda por ISBN)
  3. Sin DOI ni ISBN, con título → OpenAlex (búsqueda por título)
  4. Sin información suficiente → se omite el enriquecimiento del ítem

Los enriquecedores son clientes HTTP directos (no agentes LLM) para
garantizar determinismo y eficiencia en lotes grandes.
"""

import logging
import os
from typing import Any

import pandas as pd

from core.clients.crossref_enricher import CrossrefEnricher, CrossrefEnricherError
from core.clients.openalex_enricher import OpenAlexEnricher, OpenAlexEnricherError
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
# Nodo principal de enriquecimiento
# ---------------------------------------------------------------------------

def enrich_metadata_node(state: State) -> dict[str, Any]:
    """
    Nodo EnrichMetadata — Enriquece los metadatos del CSV reconciliado.

    Lee `reconciled_csv_path`, aplica la estrategia de enriquecimiento
    a cada fila y escribe el resultado de vuelta sobre el mismo archivo.
    Al finalizar, actualiza `enrichment_stats` con un resumen de la operación.

    La estrategia por fila es:
      - Tiene DOI:              consulta Crossref.
      - Sin DOI, tiene ISBN:    consulta OpenAlex por ISBN.
      - Sin DOI ni ISBN:        consulta OpenAlex por título (si hay título).
      - Sin ningún campo útil:  omite el ítem.

    Los campos enriquecidos se agregan al DataFrame como columnas adicionales
    con prefijo `crossref_` u `openalex_` según la fuente utilizada.
    """
    reconciled_path = state["reconciled_csv_path"]

    if not os.path.isfile(reconciled_path):
        logger.error(
            "[EnrichMetadata] CSV reconciliado no encontrado: '%s'", reconciled_path
        )
        return {"enrichment_stats": {"error": f"Archivo no encontrado: {reconciled_path}"}}

    df = pd.read_csv(reconciled_path)
    total = len(df)
    logger.info("[EnrichMetadata] Enriqueciendo %d ítems desde '%s'...", total, reconciled_path)

    crossref = CrossrefEnricher()
    openalex = OpenAlexEnricher()

    stats = {"total": total, "enriched_crossref": 0, "enriched_openalex": 0, "skipped": 0, "errors": 0}

    enriched_rows: list[dict] = []

    for idx, row in df.iterrows():
        extra = _enrich_row(row, crossref, openalex, stats)
        enriched_rows.append(extra)

    # Agregar columnas enriquecidas al DataFrame
    enrichment_df = pd.DataFrame(enriched_rows, index=df.index)
    for col in enrichment_df.columns:
        df[col] = enrichment_df[col]

    # Escribir de vuelta al mismo path (enriquecimiento in-place)
    df.to_csv(reconciled_path, index=False)

    logger.info(
        "[EnrichMetadata] Completado. Crossref: %d, OpenAlex: %d, Omitidos: %d, Errores: %d",
        stats["enriched_crossref"], stats["enriched_openalex"],
        stats["skipped"], stats["errors"],
    )

    return {"enrichment_stats": stats}


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------

_DOI_CANDIDATES  = ["doi", "dc.identifier.doi", "DOI"]
_ISBN_CANDIDATES = ["isbn", "dc.identifier.isbn", "ISBN"]
_TITLE_CANDIDATES = ["title", "dc.title", "titulo", "Title"]


def _find_field(row: pd.Series, candidates: list[str]) -> str:
    """Devuelve el primer valor no vacío encontrado entre las columnas candidatas."""
    for col in candidates:
        val = row.get(col, "")
        if val and str(val).strip() not in ("", "nan", "None"):
            return str(val).strip()
    return ""


def _enrich_row(
    row: pd.Series,
    crossref: CrossrefEnricher,
    openalex: OpenAlexEnricher,
    stats: dict,
) -> dict:
    """
    Determina la estrategia de enriquecimiento para una fila y ejecuta la consulta.

    Modifica `stats` in-place para llevar la cuenta de resultados.

    Returns:
        Diccionario con los campos enriquecidos (puede estar vacío si se omite).
    """
    doi   = _find_field(row, _DOI_CANDIDATES)
    isbn  = _find_field(row, _ISBN_CANDIDATES)
    title = _find_field(row, _TITLE_CANDIDATES)

    try:
        if doi:
            enriched = crossref.enrich_by_doi(doi)
            if enriched:
                stats["enriched_crossref"] += 1
                return enriched

        if isbn:
            enriched = openalex.enrich_by_isbn(isbn)
            if enriched:
                stats["enriched_openalex"] += 1
                return enriched

        if title:
            enriched = openalex.enrich_by_title(title)
            if enriched:
                stats["enriched_openalex"] += 1
                return enriched

    except (CrossrefEnricherError, OpenAlexEnricherError, Exception) as exc:
        logger.warning("[EnrichMetadata] Error enriqueciendo fila (doi=%s): %s", doi or "N/A", exc)
        stats["errors"] += 1
        return {}

    stats["skipped"] += 1
    return {}
