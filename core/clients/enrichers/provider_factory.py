"""
Factory para crear y gestionar instancias de enriquecedores de metadatos.

Registra todos los providers disponibles y permite crearlos por nombre
o instanciar todos a la vez. Incluye un health check pre-lote que valida
disponibilidad de las APIs antes de procesar el CSV.

Uso típico:
  from core.clients.provider_factory import EnricherFactory

  # Crear uno por nombre
  crossref = EnricherFactory.create("crossref")

  # Crear todos los registrados
  all_enrichers = EnricherFactory.create_all()

  # Verificar disponibilidad antes del lote
  status = EnricherFactory.health_check_all()
"""

import logging
from typing import Type

from core.clients.enrichers.base_enricher import BaseEnricher
from core.clients.enrichers.crossref_enricher import CrossrefEnricher
from core.clients.enrichers.openalex_enricher import OpenAlexEnricher
from core.clients.enrichers.doi_negotiation_enricher import DOINegotiationEnricher
from core.clients.enrichers.openlibrary_enricher import OpenLibraryEnricher

logger = logging.getLogger(__name__)


class EnricherFactory:
    """Factory centralizado para enriquecedores de metadatos."""

    _providers: dict[str, Type[BaseEnricher]] = {
        "crossref": CrossrefEnricher,
        "openalex": OpenAlexEnricher,
        "doi_negotiation": DOINegotiationEnricher,
        "openlibrary": OpenLibraryEnricher,
    }

    @classmethod
    def create(cls, name: str, **kwargs) -> BaseEnricher:
        """
        Crear una instancia de enriquecedor por nombre.

        Args:
            name: Nombre del enriquecedor (ver get_available).
            **kwargs: Argumentos pasados al constructor.

        Raises:
            ValueError: Si el nombre no está registrado.
        """
        name = name.lower()
        if name not in cls._providers:
            available = ", ".join(cls._providers.keys())
            raise ValueError(
                f"Enricher '{name}' no encontrado. "
                f"Disponibles: {available}"
            )
        return cls._providers[name](**kwargs)

    @classmethod
    def create_all(cls, **kwargs) -> dict[str, BaseEnricher]:
        """Crear instancias de todos los enriquecedores registrados."""
        enrichers = {}
        for name in cls._providers:
            try:
                enrichers[name] = cls.create(name, **kwargs)
            except Exception as exc:
                logger.error("Error creando enricher '%s': %s", name, exc)
        return enrichers

    @classmethod
    def get_available(cls) -> list[str]:
        """Nombres de todos los enriquecedores registrados."""
        return list(cls._providers.keys())

    @classmethod
    def register(cls, name: str, enricher_class: Type[BaseEnricher]) -> None:
        """
        Registrar un nuevo enriquecedor en tiempo de ejecución.

        Útil para plugins o extensibilidad sin modificar el factory.
        """
        if not issubclass(enricher_class, BaseEnricher):
            raise ValueError(
                "El enriquecedor debe ser subclase de BaseEnricher"
            )
        cls._providers[name.lower()] = enricher_class
        logger.info("Enricher '%s' registrado exitosamente", name)

    @classmethod
    def health_check_all(cls) -> dict[str, dict]:
        """
        Verificar disponibilidad de todas las APIs registradas.

        Returns:
            Dict con el estado de cada enriquecedor.
            Útil como pre-check antes de procesar un lote grande.
        """
        results = {}
        for name, klass in cls._providers.items():
            try:
                instance = klass()
                results[name] = instance.get_status()
            except Exception as exc:
                results[name] = {
                    "available": False,
                    "provider": name,
                    "error": str(exc),
                }
        return results
