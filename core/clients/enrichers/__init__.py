"""
Enriquecedores de metadatos académicos.

Paquete que expone todos los providers de enriquecimiento y el factory
para crearlos de forma centralizada.
"""

from core.clients.enrichers.base_enricher import BaseEnricher, BaseEnricherError
from core.clients.enrichers.crossref_enricher import CrossrefEnricher, CrossrefEnricherError
from core.clients.enrichers.openalex_enricher import OpenAlexEnricher, OpenAlexEnricherError
from core.clients.enrichers.doi_negotiation_enricher import DOINegotiationEnricher, DOINegotiationEnricherError
from core.clients.enrichers.openlibrary_enricher import OpenLibraryEnricher, OpenLibraryEnricherError
from core.clients.enrichers.provider_factory import EnricherFactory

__all__ = [
    "BaseEnricher",
    "BaseEnricherError",
    "CrossrefEnricher",
    "CrossrefEnricherError",
    "OpenAlexEnricher",
    "OpenAlexEnricherError",
    "DOINegotiationEnricher",
    "DOINegotiationEnricherError",
    "OpenLibraryEnricher",
    "OpenLibraryEnricherError",
    "EnricherFactory",
]
