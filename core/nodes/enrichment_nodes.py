"""
Nodos del subgrafo de Enriquecimiento de Metadatos.

Responsable de completar los metadatos de cada ítem consultando fuentes
académicas externas luego de la deduplicación.

Estrategia de enriquecimiento (Cadena de Responsabilidad):
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

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd
from langsmith import traceable

from core.clients.enrichers.base_enricher import BaseEnricher, BaseEnricherError
from core.clients.enrichers.provider_factory import EnricherFactory
from core.clients.enrichers.crossref_enricher import CrossrefEnricherError
from core.clients.enrichers.openalex_enricher import OpenAlexEnricherError
from core.clients.enrichers.doi_negotiation_enricher import DOINegotiationEnricherError
from core.clients.enrichers.openlibrary_enricher import OpenLibraryEnricherError
from core.state import State

logger = logging.getLogger(__name__)

# Errores de los enriquecedores que se capturan y cuentan como warnings
_ENRICHABLE_ERRORS = (
    BaseEnricherError,
    CrossrefEnricherError,
    OpenAlexEnricherError,
    DOINegotiationEnricherError,
    OpenLibraryEnricherError,
)

# Valores considerados vacíos en un DataFrame de pandas (dtype=str)
_EMPTY_VALUES = {"", "nan", "None"}

# Candidatos de columna para cada campo de búsqueda
_DOI_CANDIDATES   = ["doi",  "dc.identifier.doi",  "DOI"]
_ISSN_CANDIDATES  = ["issn", "dc.identifier.issn", "ISSN"]
_TITLE_CANDIDATES = ["title", "dc.title", "titulo",  "Title"]
_ISBN_CANDIDATES  = ["isbn",  "dc.identifier.isbn", "ISBN"]


# ---------------------------------------------------------------------------
# Función de enrutamiento (conditional_edge del subgrafo)
# ---------------------------------------------------------------------------

def route_enrichment(state: State) -> str:
    """
    Determina si el enriquecimiento de metadatos está habilitado.

    Returns:
        "enrich" → proceder con el enriquecimiento.
        "skip"   → saltar el subgrafo directamente al ExportSubgraph.
    """
    return "enrich" if state.get("enrichment_enabled", False) else "skip"


# ---------------------------------------------------------------------------
# Chain of Responsibility: estrategias de enriquecimiento
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EnrichmentStrategy:
    """
    Estrategia individual de enriquecimiento en la cadena de responsabilidad.

    Encapsula un proveedor, el campo de búsqueda que requiere (ej. 'doi'),
    el método a invocar (ej. 'enrich_by_doi') y la clave de estadística
    a incrementar en caso de éxito.

    Attributes:
        provider_name: Nombre del proveedor (clave en el health check dict).
        field_candidates: Lista de nombres de columna candidatos para el campo.
        method_name:   Método del enriquecedor a invocar.
        stat_key:      Clave del dict de stats a incrementar en caso de éxito.
    """
    provider_name: str
    field_candidates: list[str]
    method_name: str
    stat_key: str


class EnrichmentChain:
    """
    Cadena de responsabilidad para enriquecer una fila de metadatos.

    Itera sobre las estrategias en orden y retorna el primer resultado
    no vacío. Si ninguna estrategia produce datos, la fila se omite.

    Agregar un nuevo proveedor es tan simple como añadir una nueva
    `EnrichmentStrategy` a la lista y registrar el enriquecedor en `EnricherFactory`.
    """

    def __init__(
        self,
        strategies: list[EnrichmentStrategy],
        providers: dict[str, BaseEnricher],
        health: dict[str, bool],
    ) -> None:
        self._strategies = strategies
        self._providers = providers
        self._health = health

    def enrich(self, row: pd.Series, stats: dict[str, int], schema: str = "generic") -> dict:
        """
        Aplica la cadena de estrategias a una fila y retorna el primer resultado exitoso.

        Args:
            row:    Fila del DataFrame a enriquecer.
            stats:  Dict de estadísticas (modificado in-place).
            schema: Esquema de columnas de salida ("generic" o "sedici").

        Returns:
            Dict con los campos enriquecidos, o vacío si se omite el ítem.
        """
        try:
            for strategy in self._strategies:
                enriched = self._try_strategy(strategy, row, schema)
                if enriched:
                    stats[strategy.stat_key] += 1
                    return enriched

        except _ENRICHABLE_ERRORS as exc:
            field_val = self._find_field(row, _DOI_CANDIDATES) or self._find_field(row, _ISBN_CANDIDATES)
            logger.warning(
                "[EnrichmentChain] Error enriqueciendo fila (ref=%s): %s",
                field_val or "N/A", exc,
            )
            stats["errors"] += 1
            return {}

        stats["skipped"] += 1
        return {}

    def _try_strategy(self, strategy: EnrichmentStrategy, row: pd.Series, schema: str) -> dict:
        """
        Intenta aplicar una estrategia concreta. Retorna vacío si:
          - El campo requerido está vacío en la fila.
          - El proveedor no está disponible (según health check).
          - El proveedor no devuelve datos.
        """
        field_value = self._find_field(row, strategy.field_candidates)
        if not field_value:
            return {}
        if not self._health.get(strategy.provider_name, True):
            return {}

        provider = self._providers.get(strategy.provider_name)
        if provider is None:
            return {}

        method = getattr(provider, strategy.method_name)
        return method(field_value, schema=schema)

    @staticmethod
    def _find_field(row: pd.Series, candidates: list[str]) -> str:
        """Devuelve el primer valor no vacío encontrado entre las columnas candidatas."""
        for col in candidates:
            val = row.get(col, "")
            if val and str(val).strip() not in _EMPTY_VALUES:
                return str(val).strip()
        return ""


# ---------------------------------------------------------------------------
# Actualizador de DataFrame enriquecido
# ---------------------------------------------------------------------------

class DataFrameEnricher:
    """
    Aplica los resultados de enriquecimiento al DataFrame, rellenando solo
    los campos vacíos para no sobreescribir datos existentes.
    """

    def apply(self, df: pd.DataFrame, idx: int, enriched_fields: dict) -> pd.DataFrame:
        """
        Rellena los campos vacíos en la fila `idx` con los valores de `enriched_fields`.

        Args:
            df:             DataFrame a modificar.
            idx:            Índice de la fila a actualizar.
            enriched_fields: Campos enriquecidos del proveedor.

        Returns:
            DataFrame con los campos actualizados.
        """
        for col, val in enriched_fields.items():
            if val is None or str(val).strip() in _EMPTY_VALUES:
                continue
            if col not in df.columns:
                df[col] = ""
            curr_val = df.at[idx, col]
            if pd.isna(curr_val) or str(curr_val).strip() in _EMPTY_VALUES:
                if df[col].dtype != "object":
                    df[col] = df[col].astype("object")
                df.at[idx, col] = str(val)
        return df


# ---------------------------------------------------------------------------
# Health check pre-lote
# ---------------------------------------------------------------------------

def _check_provider_health() -> dict[str, bool]:
    """
    Verifica disponibilidad de los enriquecedores antes del procesamiento.

    Returns:
        Dict con True/False por nombre de provider. Los indisponibles
        se saltean silenciosamente durante el enriquecimiento.
    """
    status = EnricherFactory.health_check_all()
    availability: dict[str, bool] = {}
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
# Cadena de estrategias por defecto
# ---------------------------------------------------------------------------

def _build_default_chain(providers: dict[str, BaseEnricher], health: dict[str, bool]) -> EnrichmentChain:
    """
    Construye la cadena de estrategias de enriquecimiento con el orden por defecto.

    El orden refleja la confiabilidad de cada fuente:
      Crossref (DOI) > DOI Negotiation (DOI fallback) > OpenAlex (ISSN) >
      OpenAlex (título) > OpenLibrary (ISBN).
    """
    strategies = [
        EnrichmentStrategy("crossref",        _DOI_CANDIDATES,   "enrich_by_doi",   "enriched_crossref"),
        EnrichmentStrategy("doi_negotiation", _DOI_CANDIDATES,   "enrich_by_doi",   "enriched_doi_negotiation"),
        EnrichmentStrategy("openalex",        _ISSN_CANDIDATES,  "enrich_by_issn",  "enriched_openalex"),
        EnrichmentStrategy("openalex",        _TITLE_CANDIDATES, "enrich_by_title", "enriched_openalex"),
        EnrichmentStrategy("openlibrary",     _ISBN_CANDIDATES,  "enrich_by_isbn",  "enriched_openlibrary"),
    ]
    return EnrichmentChain(strategies=strategies, providers=providers, health=health)


# ---------------------------------------------------------------------------
# Nodo principal de enriquecimiento
# ---------------------------------------------------------------------------

@traceable(name="EnrichMetadata", run_type="chain")
def enrich_metadata_node(state: State) -> dict[str, Any]:
    """
    Nodo EnrichMetadata — Enriquece los metadatos del CSV en formato genérico.

    Lee `generic_source_csv_path`, aplica la cadena de enriquecimiento
    a cada fila y escribe los campos completados directamente sobre el mismo archivo.
    Al finalizar, actualiza `enrichment_stats` con un resumen de la operación.
    """
    generic_path = state.get("generic_source_csv_path")

    if not generic_path or not os.path.isfile(generic_path):
        logger.error(
            "[EnrichMetadata] CSV genérico de origen no encontrado: '%s'", generic_path
        )
        return {"enrichment_stats": {"error": f"Archivo genérico no encontrado: {generic_path}"}}

    provider_health = _check_provider_health()
    providers = {name: EnricherFactory.create(name) for name in EnricherFactory.get_available()}
    chain = _build_default_chain(providers, provider_health)
    df_enricher = DataFrameEnricher()

    df = pd.read_csv(generic_path, dtype=str).fillna("")
    total = len(df)
    logger.info("[EnrichMetadata] Enriqueciendo %d ítems genéricos desde '%s'...", total, generic_path)

    stats: dict[str, int] = {
        "total": total,
        "enriched_crossref": 0,
        "enriched_doi_negotiation": 0,
        "enriched_openalex": 0,
        "enriched_openlibrary": 0,
        "skipped": 0,
        "errors": 0,
    }

    for idx, row in df.iterrows():
        enriched_fields = chain.enrich(row, stats, schema="generic")
        if enriched_fields:
            df = df_enricher.apply(df, idx, enriched_fields)

    df.to_csv(generic_path, index=False)

    logger.info(
        "[EnrichMetadata] Completado. Crossref: %d, DOI Negotiation: %d, "
        "OpenAlex: %d, OpenLibrary: %d, Omitidos: %d, Errores: %d",
        stats["enriched_crossref"], stats["enriched_doi_negotiation"],
        stats["enriched_openalex"], stats["enriched_openlibrary"],
        stats["skipped"], stats["errors"],
    )

    return {"enrichment_stats": stats}
