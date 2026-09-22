"""
Tests unitarios para los nodos de Enriquecimiento (core/nodes/enrichment_nodes.py).
"""

import os
import pandas as pd
from unittest.mock import MagicMock, patch
import pytest

from core.nodes.enrichment_nodes import enrich_metadata_node, route_enrichment
from core.clients.enrichers.crossref_enricher import CrossrefEnricherError
from core.clients.enrichers.base_enricher import BaseEnricher


class TestRouteEnrichment:
    """Verifica el enrutador condicional del subgrafo de enriquecimiento."""

    def test_route_enrichment_enabled(self):
        assert route_enrichment({"enrichment_enabled": True}) == "enrich"

    def test_route_enrichment_disabled(self):
        assert route_enrichment({"enrichment_enabled": False}) == "skip"
        assert route_enrichment({}) == "skip"


class TestEnrichMetadataNode:
    """Verifica la ejecución de enrich_metadata_node con mocks de APIs externas."""

    def test_enrich_by_doi_crossref(self, tmp_path):
        generic_csv = tmp_path / "generic_source.csv"
        df_init = pd.DataFrame([
            {
                "id": "item_1",
                "title": "Existing Title",
                "author": "",
                "date": "",
                "doi": "10.1000/123",
                "issn": "",
                "isbn": "",
                "citation": "",
            }
        ])
        df_init.to_csv(generic_csv, index=False)

        state = {
            "generic_source_csv_path": str(generic_csv),
            "enrichment_enabled": True,
        }

        mock_crossref = MagicMock(spec=BaseEnricher)
        mock_crossref.enrich_by_doi.return_value = {
            "title": "Crossref Title",
            "author": "Perez, Juan",
            "date": "2024",
            "citation": "Revista Cientifica",
            "doi": "10.1000/123",
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

            res = enrich_metadata_node(state)

            assert res["enrichment_stats"]["enriched_crossref"] == 1
            df_out = pd.read_csv(generic_csv)
            assert df_out.iloc[0]["title"] == "Existing Title"  # Preservado
            assert df_out.iloc[0]["author"] == "Perez, Juan"    # Enriquecido
            assert str(df_out.iloc[0]["date"]) == "2024"        # Enriquecido

    def test_enrichment_fallback_to_openalex_by_issn(self, tmp_path):
        generic_csv = tmp_path / "generic_source.csv"
        df_init = pd.DataFrame([
            {
                "id": "item_2",
                "title": "",
                "author": "",
                "date": "",
                "doi": "",
                "issn": "0028-0836",
                "isbn": "",
                "citation": "",
            }
        ])
        df_init.to_csv(generic_csv, index=False)

        state = {"generic_source_csv_path": str(generic_csv)}

        mock_openalex = MagicMock(spec=BaseEnricher)
        mock_openalex.enrich_by_issn.return_value = {
            "title": "Nature Article",
            "author": "Smith, J.",
            "issn": "0028-0836",
        }

        with patch("core.nodes.enrichment_nodes.EnricherFactory.create") as mock_factory, \
             patch("core.nodes.enrichment_nodes._check_provider_health") as mock_health:

            mock_health.return_value = {"crossref": True, "openalex": True, "doi_negotiation": True, "openlibrary": True}
            mock_factory.side_effect = lambda name: mock_openalex if name == "openalex" else MagicMock()

            res = enrich_metadata_node(state)

            assert res["enrichment_stats"]["enriched_openalex"] == 1
            df_out = pd.read_csv(generic_csv)
            assert df_out.iloc[0]["title"] == "Nature Article"

    def test_enrichment_handles_provider_errors_gracefully(self, tmp_path):
        generic_csv = tmp_path / "generic_source.csv"
        df_init = pd.DataFrame([
            {
                "id": "item_err",
                "title": "Title",
                "author": "",
                "doi": "10.1000/broken_doi",
                "issn": "",
                "isbn": "",
                "citation": "",
                "date": "",
            }
        ])
        df_init.to_csv(generic_csv, index=False)

        state = {"generic_source_csv_path": str(generic_csv)}

        mock_crossref = MagicMock(spec=BaseEnricher)
        mock_crossref.enrich_by_doi.side_effect = CrossrefEnricherError("429 Rate Limit Exceeded")

        with patch("core.nodes.enrichment_nodes.EnricherFactory.create") as mock_factory, \
             patch("core.nodes.enrichment_nodes._check_provider_health") as mock_health:

            mock_health.return_value = {"crossref": True, "openalex": True, "doi_negotiation": True, "openlibrary": True}
            mock_factory.side_effect = lambda name: mock_crossref if name == "crossref" else MagicMock()

            res = enrich_metadata_node(state)

            assert res["enrichment_stats"]["errors"] == 1
            assert res["enrichment_stats"]["total"] == 1
            # El archivo no se corrompe
            assert os.path.isfile(generic_csv)

    def test_missing_generic_file_returns_error_stats(self, tmp_path):
        state = {"generic_source_csv_path": str(tmp_path / "no_existe.csv")}
        res = enrich_metadata_node(state)
        assert "error" in res["enrichment_stats"]
