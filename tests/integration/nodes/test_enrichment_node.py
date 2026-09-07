"""
Tests de integración/nodo para el nodo de enriquecimiento de metadatos.

Cubre:
  - Función de enrutamiento condicional: route_enrichment
  - Nodo de ejecución de enriquecimiento: enrich_metadata_node
"""

import os
import tempfile
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from core.nodes.enrichment_nodes import enrich_metadata_node, route_enrichment
from core.clients.enrichers.base_enricher import BaseEnricher


def test_route_enrichment():
    """Valida la lógica del conditional edge para enriquecimiento."""
    assert route_enrichment({"enrichment_enabled": True}) == "enrich"
    assert route_enrichment({"enrichment_enabled": False}) == "skip"
    assert route_enrichment({}) == "skip"


def test_enrich_metadata_node_updates_generic_csv():
    """Valida que enrich_metadata_node complete metadatos faltantes in-place en generic_source_csv_path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "generic_source.csv")

        # Registro con DOI pero sin author ni date
        df_init = pd.DataFrame([
            {
                "id": "item_test_01",
                "title": "Existing Title",
                "author": "",
                "date": "",
                "doi": "10.1000/xyz123",
                "issn": "",
                "citation": "",
            }
        ])
        df_init.to_csv(csv_path, index=False)

        state = {
            "generic_source_csv_path": csv_path,
            "enrichment_enabled": True,
        }

        mock_crossref = MagicMock(spec=BaseEnricher)
        mock_crossref.enrich_by_doi.return_value = {
            "title": "Crossref Enriched Title",
            "author": "Doe, Jane || Smith, John",
            "date": "2024",
            "citation": "Journal of Testing",
            "doi": "10.1000/xyz123",
        }

        with patch("core.nodes.enrichment_nodes.EnricherFactory.create") as mock_factory, \
             patch("core.nodes.enrichment_nodes._check_provider_health") as mock_health:

            mock_health.return_value = {
                "crossref": True,
                "openalex": True,
                "doi_negotiation": True,
                "openlibrary": True,
            }
            mock_factory.side_effect = lambda name: mock_crossref if name == "crossref" else MagicMock()

            result = enrich_metadata_node(state)

        assert "enrichment_stats" in result
        assert result["enrichment_stats"]["enriched_crossref"] == 1

        df_out = pd.read_csv(csv_path)
        assert len(df_out) == 1
        assert df_out.iloc[0]["title"] == "Existing Title"  # Preserva valor preexistente
        assert df_out.iloc[0]["author"] == "Doe, Jane || Smith, John"  # Enriquecido
        assert str(df_out.iloc[0]["date"]) == "2024"  # Enriquecido
        assert df_out.iloc[0]["citation"] == "Journal of Testing"  # Enriquecido
