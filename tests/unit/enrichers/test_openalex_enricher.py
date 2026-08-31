"""
Tests para OpenAlexEnricher (core/clients/enrichers/openalex_enricher.py).

Ejecutar tests unitarios (mock):
    pytest tests/enrichers/test_openalex_enricher.py -v

Ejecutar test de integración contra la API real de OpenAlex:
    pytest tests/enrichers/test_openalex_enricher.py -v -k integration --title "Machine Learning" --issn "0028-0836"
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.clients.enrichers.openalex_enricher import OpenAlexEnricher


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_openalex_work(
    title="Deep Learning",
    authors=None,
    year=2020,
    doi="https://doi.org/10.1000/test123",
    work_type="journal-article",
    journal="Nature",
    issn_l="0028-0836",
    is_oa=True,
    citations=150,
):
    """Helper para construir un work simulado de OpenAlex."""
    if authors is None:
        authors = ["Ian Goodfellow"]
    return {
        "title": title,
        "publication_year": year,
        "doi": doi,
        "type": work_type,
        "authorships": [
            {"author": {"display_name": a}} for a in authors
        ],
        "primary_location": {
            "source": {
                "display_name": journal,
                "issn_l": issn_l,
            }
        },
        "open_access": {"is_oa": is_oa},
        "cited_by_count": citations,
    }


def _mock_response(json_data, status_code=200):
    """Crea un mock de requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


# ===========================================================================
# Tests Unitarios: enrich_by_title
# ===========================================================================

class TestEnrichByTitle:

    @patch("core.clients.enrichers.openalex_enricher.requests.Session")
    def test_returns_metadata_when_result_found(self, MockSession):
        work = _make_openalex_work()
        mock_resp = _mock_response({"results": [work]})
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = OpenAlexEnricher(email="test@example.com")
        result = enricher.enrich_by_title("Deep Learning")

        assert result["dc.title"] == "Deep Learning"
        assert result["sedici.creator.person"] == "Ian Goodfellow"
        assert result["dc.date.issued"] == "2020"
        assert result["sedici.identifier.other"] == "https://doi.org/10.1000/test123"
        assert result["dc.type"] == "journal-article"
        assert result["sedici.relation.journalTitle"] == "Nature"
        assert result["sedici.identifier.issn"] == "0028-0836"
        # open_access y citations se omiten intencionalmente del mapeo SEDICI
        assert "open_access" not in result
        assert "citations" not in result

    @patch("core.clients.enrichers.openalex_enricher.requests.Session")
    def test_returns_empty_when_no_results(self, MockSession):
        mock_resp = _mock_response({"results": []})
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = OpenAlexEnricher(email="test@example.com")
        result = enricher.enrich_by_title("titulo inexistente xyz")

        assert result == {}

    @patch("core.clients.enrichers.openalex_enricher.requests.Session")
    def test_returns_empty_when_title_is_empty(self, MockSession):
        enricher = OpenAlexEnricher(email="test@example.com")
        result = enricher.enrich_by_title("")

        assert result == {}
        session_instance = MockSession.return_value
        session_instance.get.assert_not_called()

    @patch("core.clients.enrichers.openalex_enricher.requests.Session")
    def test_returns_empty_when_title_is_whitespace(self, MockSession):
        enricher = OpenAlexEnricher(email="test@example.com")
        result = enricher.enrich_by_title("   ")

        assert result == {}

    @patch("core.clients.enrichers.openalex_enricher.requests.Session")
    def test_strips_title_whitespace(self, MockSession):
        mock_resp = _mock_response({"results": [_make_openalex_work()]})
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = OpenAlexEnricher(email="test@example.com")
        enricher.enrich_by_title("  Deep Learning  ")

        call_kwargs = session_instance.get.call_args
        assert call_kwargs[1]["params"]["search"] == "Deep Learning"

    @patch("core.clients.enrichers.openalex_enricher.requests.Session")
    def test_handles_multiple_authors(self, MockSession):
        work = _make_openalex_work(authors=["Alice Smith", "Bob Jones", "Carol White"])
        mock_resp = _mock_response({"results": [work]})
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = OpenAlexEnricher(email="test@example.com")
        result = enricher.enrich_by_title("Multi Author Paper")

        assert result["sedici.creator.person"] == "Alice Smith || Bob Jones || Carol White"

    @patch("core.clients.enrichers.openalex_enricher.requests.Session")
    def test_returns_empty_on_request_exception(self, MockSession):
        import requests as req_lib
        session_instance = MockSession.return_value
        session_instance.get.side_effect = req_lib.ConnectionError("network error")

        enricher = OpenAlexEnricher(email="test@example.com")
        result = enricher.enrich_by_title("Deep Learning")

        assert result == {}


# ===========================================================================
# Tests Unitarios: enrich_by_issn
# ===========================================================================

class TestEnrichByIssn:

    @patch("core.clients.enrichers.openalex_enricher.requests.Session")
    def test_returns_metadata_when_issn_found(self, MockSession):
        work = _make_openalex_work(title="Journal Article", year=2021)
        mock_resp = _mock_response({"results": [work]})
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = OpenAlexEnricher(email="test@example.com")
        result = enricher.enrich_by_issn("0028-0836")

        assert result["dc.title"] == "Journal Article"
        assert result["dc.date.issued"] == "2021"

    @patch("core.clients.enrichers.openalex_enricher.requests.Session")
    def test_strips_dashes_from_issn(self, MockSession):
        mock_resp = _mock_response({"results": []})
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = OpenAlexEnricher(email="test@example.com")
        enricher.enrich_by_issn("0028-0836")

        call_kwargs = session_instance.get.call_args
        issn_param = call_kwargs[1]["params"]["filter"]
        assert "00280836" in issn_param

    @patch("core.clients.enrichers.openalex_enricher.requests.Session")
    def test_returns_empty_when_issn_is_empty(self, MockSession):
        enricher = OpenAlexEnricher(email="test@example.com")
        result = enricher.enrich_by_issn("")

        assert result == {}

    @patch("core.clients.enrichers.openalex_enricher.requests.Session")
    def test_returns_empty_when_no_results(self, MockSession):
        mock_resp = _mock_response({"results": []})
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = OpenAlexEnricher(email="test@example.com")
        result = enricher.enrich_by_issn("00000000")

        assert result == {}


# ===========================================================================
# Tests Unitarios: _extract_relevant_fields
# ===========================================================================

class TestExtractRelevantFields:

    def test_extracts_all_fields(self):
        enricher = OpenAlexEnricher(email="test@example.com")
        work = _make_openalex_work()
        result = enricher._extract_relevant_fields(work)

        assert result["dc.title"] == "Deep Learning"
        assert result["sedici.creator.person"] == "Ian Goodfellow"
        assert result["dc.date.issued"] == "2020"
        assert result["sedici.relation.journalTitle"] == "Nature"
        assert result["sedici.identifier.issn"] == "0028-0836"

    def test_handles_missing_primary_location(self):
        enricher = OpenAlexEnricher(email="test@example.com")
        work = _make_openalex_work()
        work["primary_location"] = None
        result = enricher._extract_relevant_fields(work)

        assert "sedici.relation.journalTitle" not in result
        assert "sedici.identifier.issn" not in result

    def test_handles_missing_source(self):
        enricher = OpenAlexEnricher(email="test@example.com")
        work = _make_openalex_work()
        work["primary_location"] = {"source": None}
        result = enricher._extract_relevant_fields(work)

        assert "sedici.relation.journalTitle" not in result
        assert "sedici.identifier.issn" not in result

    def test_handles_empty_authorships(self):
        enricher = OpenAlexEnricher(email="test@example.com")
        work = _make_openalex_work(authors=[])
        work["authorships"] = []
        result = enricher._extract_relevant_fields(work)

        assert "sedici.creator.person" not in result

    def test_handles_missing_optional_fields(self):
        enricher = OpenAlexEnricher(email="test@example.com")
        work = {}
        result = enricher._extract_relevant_fields(work)

        # work vacío: title, year, doi, citations son cadenas/números vacíos,
        # se filtran; el resultado debe estar vacío
        assert "dc.title" not in result
        assert "dc.date.issued" not in result
        assert "sedici.identifier.other" not in result


# ===========================================================================
# Tests Unitarios: _get y retry
# ===========================================================================

class TestGetAndRetry:

    @patch("core.clients.enrichers.openalex_enricher.requests.Session")
    def test_returns_empty_dict_on_404(self, MockSession):
        mock_resp = _mock_response({}, status_code=404)
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = OpenAlexEnricher(email="test@example.com")
        result = enricher._get("/works", params={"search": "test"})

        assert result == {}

    @patch("core.clients.enrichers.openalex_enricher.requests.Session")
    def test_raises_on_500(self, MockSession):
        import requests as req_lib
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = req_lib.HTTPError("500")
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = OpenAlexEnricher(email="test@example.com")
        with pytest.raises(req_lib.HTTPError):
            enricher._get("/works", params={"search": "test"})


# ===========================================================================
# Tests de Integración (reales contra la API de OpenAlex)
# ===========================================================================

@pytest.mark.integration
class TestOpenAlexIntegration:

    def test_enrich_by_title_real(self):
        """Test real contra la API de OpenAlex. Requiere conexión a internet."""
        enricher = OpenAlexEnricher(email="test@openalex.org")
        result = enricher.enrich_by_title("Deep Learning")

        assert isinstance(result, dict)
        if result:
            assert "dc.title" in result
            assert "sedici.creator.person" in result
            assert result["dc.title"] != ""

    def test_enrich_by_issn_real(self):
        """Test real contra la API de OpenAlex. Requiere conexión a internet."""
        enricher = OpenAlexEnricher(email="test@openalex.org")
        result = enricher.enrich_by_issn("0028-0836")

        assert isinstance(result, dict)


# ===========================================================================
# Tests parametrizados para datos manuales
# ===========================================================================

MANUAL_TEST_TITLES = [
    pytest.param("Machine Learning", id="machine-learning"),
]

MANUAL_TEST_ISSNS = [
    pytest.param("0028-0836", id="nature-journal"),
]


@pytest.mark.integration
@pytest.mark.parametrize("issn", MANUAL_TEST_ISSNS)
def test_manual_issn(issn):
    enricher = OpenAlexEnricher(email="test@openalex.org")
    result = enricher.enrich_by_issn(issn)
    print(f"\n[OpenAlex] ISSN: '{issn}' => {result}")
    assert isinstance(result, dict)
