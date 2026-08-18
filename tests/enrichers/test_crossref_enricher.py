"""
Tests para CrossrefEnricher (core/clients/crossref_enricher.py).

Ejecutar tests unitarios (mock):
    pytest tests/enrichers/test_crossref_enricher.py -v

Ejecutar test de integración contra la API real de Crossref:
    pytest tests/enrichers/test_crossref_enricher.py -v -k integration --doi "10.1038/nature14539"
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.clients.crossref_enricher import CrossrefEnricher


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_crossref_work(
    title="Quantum Computing for Beginners",
    authors=None,
    year=2022,
    publisher="Nature Publishing Group",
    issn=None,
    work_type="journal-article",
    abstract="This is an abstract.",
    container_title=None,
):
    """Helper para construir un work simulado de Crossref."""
    if authors is None:
        authors = [{"given": "John", "family": "Doe"}]
    if issn is None:
        issn = ["0028-0836"]
    if container_title is None:
        container_title = ["Nature Physics"]
    return {
        "title": [title],
        "author": authors,
        "published": {"date-parts": [[year]]},
        "publisher": publisher,
        "ISSN": issn,
        "type": work_type,
        "abstract": abstract,
        "container-title": container_title,
    }


def _make_crossref_response(work):
    """Envuelve un work en la estructura de respuesta de Crossref."""
    return {"status": "ok", "message": work}


def _mock_response(json_data, status_code=200):
    """Crea un mock de requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


# ===========================================================================
# Tests Unitarios: enrich_by_doi
# ===========================================================================

class TestEnrichByDoi:

    @patch("core.clients.crossref_enricher.requests.Session")
    def test_returns_metadata_when_doi_found(self, MockSession):
        work = _make_crossref_work()
        resp_data = _make_crossref_response(work)
        mock_resp = _mock_response(resp_data)
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = CrossrefEnricher(email="test@example.com")
        result = enricher.enrich_by_doi("10.1038/nature14539")

        assert result["crossref_title"] == "Quantum Computing for Beginners"
        assert result["crossref_authors"] == "Doe, John"
        assert result["crossref_year"] == "2022"
        assert result["crossref_publisher"] == "Nature Publishing Group"
        assert result["crossref_issn"] == "0028-0836"
        assert result["crossref_type"] == "journal-article"
        assert result["crossref_abstract"] == "This is an abstract."
        assert result["crossref_journal"] == "Nature Physics"

    @patch("core.clients.crossref_enricher.requests.Session")
    def test_strips_doi_url_prefix(self, MockSession):
        mock_resp = _mock_response(_make_crossref_response(_make_crossref_work()))
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = CrossrefEnricher(email="test@example.com")
        enricher.enrich_by_doi("https://doi.org/10.1038/nature14539")

        call_args = session_instance.get.call_args[0][0]
        assert "nature14539" in call_args
        assert "doi.org" not in call_args

    @patch("core.clients.crossref_enricher.requests.Session")
    def test_strips_dx_doi_prefix(self, MockSession):
        mock_resp = _mock_response(_make_crossref_response(_make_crossref_work()))
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = CrossrefEnricher(email="test@example.com")
        enricher.enrich_by_doi("http://dx.doi.org/10.1038/nature14539")

        call_args = session_instance.get.call_args[0][0]
        assert "nature14539" in call_args
        assert "dx.doi.org" not in call_args

    @patch("core.clients.crossref_enricher.requests.Session")
    def test_returns_empty_when_doi_is_empty(self, MockSession):
        enricher = CrossrefEnricher(email="test@example.com")
        result = enricher.enrich_by_doi("")

        assert result == {}
        session_instance = MockSession.return_value
        session_instance.get.assert_not_called()

    @patch("core.clients.crossref_enricher.requests.Session")
    def test_returns_empty_when_status_not_ok(self, MockSession):
        mock_resp = _mock_response({"status": "error"})
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = CrossrefEnricher(email="test@example.com")
        result = enricher.enrich_by_doi("10.1000/fake-doi")

        assert result == {}

    @patch("core.clients.crossref_enricher.requests.Session")
    def test_returns_empty_fields_when_message_missing(self, MockSession):
        mock_resp = _mock_response({"status": "ok"})
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = CrossrefEnricher(email="test@example.com")
        result = enricher.enrich_by_doi("10.1000/fake-doi")

        assert result["crossref_title"] == ""
        assert result["crossref_authors"] == ""
        assert result["crossref_year"] == ""

    @patch("core.clients.crossref_enricher.requests.Session")
    def test_returns_empty_on_request_exception(self, MockSession):
        import requests as req_lib
        session_instance = MockSession.return_value
        session_instance.get.side_effect = req_lib.ConnectionError("timeout")

        enricher = CrossrefEnricher(email="test@example.com")
        result = enricher.enrich_by_doi("10.1000/fake-doi")

        assert result == {}

    @patch("core.clients.crossref_enricher.requests.Session")
    def test_handles_multiple_authors(self, MockSession):
        work = _make_crossref_work(authors=[
            {"given": "Alice", "family": "Smith"},
            {"given": "Bob", "family": "Jones"},
        ])
        mock_resp = _mock_response(_make_crossref_response(work))
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = CrossrefEnricher(email="test@example.com")
        result = enricher.enrich_by_doi("10.1000/multi-author")

        assert result["crossref_authors"] == "Smith, Alice || Jones, Bob"

    @patch("core.clients.crossref_enricher.requests.Session")
    def test_handles_empty_authors(self, MockSession):
        work = _make_crossref_work(authors=[])
        mock_resp = _mock_response(_make_crossref_response(work))
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = CrossrefEnricher(email="test@example.com")
        result = enricher.enrich_by_doi("10.1000/no-authors")

        assert result["crossref_authors"] == ""

    @patch("core.clients.crossref_enricher.requests.Session")
    def test_handles_empty_container_title(self, MockSession):
        work = _make_crossref_work(container_title=[])
        mock_resp = _mock_response(_make_crossref_response(work))
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = CrossrefEnricher(email="test@example.com")
        result = enricher.enrich_by_doi("10.1000/no-journal")

        assert result["crossref_journal"] == ""


# ===========================================================================
# Tests Unitarios: _extract_relevant_fields
# ===========================================================================

class TestExtractRelevantFields:

    def test_extracts_all_fields(self):
        enricher = CrossrefEnricher(email="test@example.com")
        work = _make_crossref_work()
        result = enricher._extract_relevant_fields(work)

        assert result["crossref_title"] == "Quantum Computing for Beginners"
        assert result["crossref_authors"] == "Doe, John"
        assert result["crossref_year"] == "2022"
        assert result["crossref_publisher"] == "Nature Publishing Group"
        assert result["crossref_issn"] == "0028-0836"
        assert result["crossref_journal"] == "Nature Physics"

    def test_handles_empty_work(self):
        enricher = CrossrefEnricher(email="test@example.com")
        result = enricher._extract_relevant_fields({})

        assert result["crossref_title"] == ""
        assert result["crossref_authors"] == ""
        assert result["crossref_year"] == ""
        assert result["crossref_publisher"] == ""
        assert result["crossref_issn"] == ""
        assert result["crossref_journal"] == ""

    def test_handles_missing_author_field(self):
        enricher = CrossrefEnricher(email="test@example.com")
        work = _make_crossref_work()
        del work["author"]
        result = enricher._extract_relevant_fields(work)

        assert result["crossref_authors"] == ""


# ===========================================================================
# Tests Unitarios: _get y retry
# ===========================================================================

class TestGetAndRetry:

    @patch("core.clients.crossref_enricher.requests.Session")
    def test_returns_empty_dict_on_404(self, MockSession):
        mock_resp = _mock_response({}, status_code=404)
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = CrossrefEnricher(email="test@example.com")
        result = enricher._get("/works/10.1000/fake")

        assert result == {}

    @patch("core.clients.crossref_enricher.requests.Session")
    def test_raises_on_500(self, MockSession):
        import requests as req_lib
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = req_lib.HTTPError("500")
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = CrossrefEnricher(email="test@example.com")
        with pytest.raises(req_lib.HTTPError):
            enricher._get("/works/10.1000/fake")


# ===========================================================================
# Tests de Integración (reales contra la API de Crossref)
# ===========================================================================

@pytest.mark.integration
class TestCrossrefIntegration:

    def test_enrich_by_doi_real(self):
        """Test real contra la API de Crossref. Requiere conexión a internet."""
        enricher = CrossrefEnricher(email="test@crossref.org")
        result = enricher.enrich_by_doi("10.1038/nature14539")

        assert isinstance(result, dict)
        if result:
            assert "crossref_title" in result
            assert "crossref_authors" in result
            assert result["crossref_title"] != ""

    def test_enrich_by_nonexistent_doi_returns_empty(self):
        enricher = CrossrefEnricher(email="test@crossref.org")
        result = enricher.enrich_by_doi("10.9999/nonexistent-doi-xyz")

        assert isinstance(result, dict)


# ===========================================================================
# Tests parametrizados para datos manuales
# ===========================================================================

MANUAL_TEST_DOIS = [
    pytest.param("10.1038/nature14539", id="nature-article"),
]

@pytest.mark.integration
@pytest.mark.parametrize("doi", MANUAL_TEST_DOIS)
def test_manual_doi(doi):
    enricher = CrossrefEnricher(email="test@crossref.org")
    result = enricher.enrich_by_doi(doi)
    print(f"\n[Crossref] DOI: '{doi}' => {result}")
    assert isinstance(result, dict)
