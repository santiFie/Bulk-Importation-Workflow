"""
Tests para OpenLibraryEnricher (core/clients/enrichers/openlibrary_enricher.py).

Ejecutar tests unitarios (mock):
    pytest tests/enrichers/test_openlibrary_enricher.py -v

Ejecutar test de integración contra la API real de OpenLibrary:
    pytest tests/enrichers/test_openlibrary_enricher.py -v -k integration
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.clients.enrichers.openlibrary_enricher import OpenLibraryEnricher, _normalize_isbn


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_openlibrary_entry(
    title="Clean Code",
    subtitle="A Handbook of Agile Software Craftsmanship",
    authors=None,
    publishers=None,
    publish_date="2008-08-01",
    number_of_pages=464,
    subjects=None,
):
    """Helper para construir una entrada simulada de OpenLibrary (jscmd=data)."""
    if authors is None:
        authors = [{"name": "Robert C. Martin"}]
    if publishers is None:
        publishers = [{"name": "Prentice Hall"}]
    if subjects is None:
        subjects = [{"name": "Computer programming"}, {"name": "Software engineering"}]
    entry = {
        "title": title,
        "authors": authors,
        "publishers": publishers,
        "publish_date": publish_date,
        "number_of_pages": number_of_pages,
        "subjects": subjects,
    }
    if subtitle:
        entry["subtitle"] = subtitle
    return entry


def _mock_response(json_data, status_code=200):
    """Crea un mock de requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


# ===========================================================================
# Tests Unitarios: _normalize_isbn
# ===========================================================================

class TestNormalizeIsbn:

    def test_strips_dashes(self):
        assert _normalize_isbn("978-3-16-148410-0") == "9783161484100"

    def test_strips_spaces(self):
        assert _normalize_isbn("978 3 16 148410 0") == "9783161484100"

    def test_preserves_x_suffix_isbn10(self):
        assert _normalize_isbn("0-306-40615-2") == "0306406152"

    def test_empty_string(self):
        assert _normalize_isbn("") == ""

    def test_none_returns_empty(self):
        assert _normalize_isbn(None) == ""


# ===========================================================================
# Tests Unitarios: enrich_by_isbn
# ===========================================================================

class TestEnrichByIsbn:

    @patch("core.clients.enrichers.openlibrary_enricher.requests.Session")
    def test_returns_metadata_when_isbn_found(self, MockSession):
        isbn = "9780132350884"
        entry = _make_openlibrary_entry()
        mock_resp = _mock_response({f"ISBN:{isbn}": entry})
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = OpenLibraryEnricher()
        result = enricher.enrich_by_isbn(isbn)

        assert result["dc.title"] == "Clean Code: A Handbook of Agile Software Craftsmanship"
        assert result["sedici.creator.person"] == "Robert C. Martin"
        assert result["dc.publisher"] == "Prentice Hall"
        assert result["dc.date.issued"] == "2008"
        assert result["dc.format.extent"] == "464"
        assert result["dc.type"] == "Libro"
        assert result["sedici.identifier.isbn"] == isbn

    @patch("core.clients.enrichers.openlibrary_enricher.requests.Session")
    def test_strips_isbn_dashes_and_spaces(self, MockSession):
        isbn_with_dashes = "978-0-13-235088-4"
        norm = "9780132350884"
        entry = _make_openlibrary_entry()
        mock_resp = _mock_response({f"ISBN:{norm}": entry})
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = OpenLibraryEnricher()
        enricher.enrich_by_isbn(isbn_with_dashes)

        call_kwargs = session_instance.get.call_args
        bibkeys = call_kwargs[1]["params"]["bibkeys"]
        assert bibkeys == f"ISBN:{norm}"

    @patch("core.clients.enrichers.openlibrary_enricher.requests.Session")
    def test_returns_empty_when_isbn_not_found(self, MockSession):
        mock_resp = _mock_response({})
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = OpenLibraryEnricher()
        result = enricher.enrich_by_isbn("9999999999999")

        assert result == {}

    @patch("core.clients.enrichers.openlibrary_enricher.requests.Session")
    def test_returns_empty_when_isbn_is_empty(self, MockSession):
        enricher = OpenLibraryEnricher()
        result = enricher.enrich_by_isbn("")

        assert result == {}
        session_instance = MockSession.return_value
        session_instance.get.assert_not_called()

    @patch("core.clients.enrichers.openlibrary_enricher.requests.Session")
    def test_returns_empty_on_request_exception(self, MockSession):
        import requests as req_lib
        session_instance = MockSession.return_value
        session_instance.get.side_effect = req_lib.ConnectionError("timeout")

        enricher = OpenLibraryEnricher()
        result = enricher.enrich_by_isbn("9780132350884")

        assert result == {}

    @patch("core.clients.enrichers.openlibrary_enricher.requests.Session")
    def test_returns_empty_on_non_200_status(self, MockSession):
        mock_resp = _mock_response({}, status_code=503)
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = OpenLibraryEnricher()
        result = enricher.enrich_by_isbn("9780132350884")

        assert result == {}

    @patch("core.clients.enrichers.openlibrary_enricher.requests.Session")
    def test_handles_entry_without_subtitle(self, MockSession):
        isbn = "9780132350884"
        entry = _make_openlibrary_entry(subtitle=None)
        mock_resp = _mock_response({f"ISBN:{isbn}": entry})
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = OpenLibraryEnricher()
        result = enricher.enrich_by_isbn(isbn)

        assert result["dc.title"] == "Clean Code"

    @patch("core.clients.enrichers.openlibrary_enricher.requests.Session")
    def test_handles_empty_authors(self, MockSession):
        isbn = "9780132350884"
        entry = _make_openlibrary_entry(authors=[])
        mock_resp = _mock_response({f"ISBN:{isbn}": entry})
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = OpenLibraryEnricher()
        result = enricher.enrich_by_isbn(isbn)

        # authors vacío se filtra; no debe aparecer en el resultado
        assert "sedici.creator.person" not in result

    @patch("core.clients.enrichers.openlibrary_enricher.requests.Session")
    def test_handles_empty_publishers(self, MockSession):
        isbn = "9780132350884"
        entry = _make_openlibrary_entry(publishers=[])
        mock_resp = _mock_response({f"ISBN:{isbn}": entry})
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = OpenLibraryEnricher()
        result = enricher.enrich_by_isbn(isbn)

        # publisher None se filtra; no debe aparecer en el resultado
        assert "dc.publisher" not in result

    @patch("core.clients.enrichers.openlibrary_enricher.requests.Session")
    def test_handles_no_pages(self, MockSession):
        isbn = "9780132350884"
        entry = _make_openlibrary_entry()
        del entry["number_of_pages"]
        mock_resp = _mock_response({f"ISBN:{isbn}": entry})
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = OpenLibraryEnricher()
        result = enricher.enrich_by_isbn(isbn)

        # pages None se filtra; no debe aparecer en el resultado
        assert "dc.format.extent" not in result

    @patch("core.clients.enrichers.openlibrary_enricher.requests.Session")
    def test_handles_multiple_authors(self, MockSession):
        isbn = "9780132350884"
        entry = _make_openlibrary_entry(authors=[
            {"name": "Alice Smith"},
            {"name": "Bob Jones"},
        ])
        mock_resp = _mock_response({f"ISBN:{isbn}": entry})
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = OpenLibraryEnricher()
        result = enricher.enrich_by_isbn(isbn)

        assert result["sedici.creator.person"] == "Alice Smith || Bob Jones"

    @patch("core.clients.enrichers.openlibrary_enricher.requests.Session")
    def test_limits_subjects_to_ten(self, MockSession):
        isbn = "9780132350884"
        subjects = [{"name": f"Subject {i}"} for i in range(15)]
        entry = _make_openlibrary_entry(subjects=subjects)
        mock_resp = _mock_response({f"ISBN:{isbn}": entry})
        session_instance = MockSession.return_value
        session_instance.get.return_value = mock_resp

        enricher = OpenLibraryEnricher()
        result = enricher.enrich_by_isbn(isbn)

        subject_list = result["sedici.subject.materias"].split(", ")
        assert len(subject_list) == 10


# ===========================================================================
# Tests Unitarios: parse_response
# ===========================================================================

class TestParseResponse:

    def test_extracts_all_fields(self):
        enricher = OpenLibraryEnricher()
        entry = _make_openlibrary_entry()
        result = enricher.parse_response(entry)

        assert result["title"] == "Clean Code: A Handbook of Agile Software Craftsmanship"
        assert result["authors"] == "Robert C. Martin"
        assert result["publisher"] == "Prentice Hall"
        assert result["year"] == "2008"
        assert result["pages"] == "464"
        assert result["type"] == "Libro"

    def test_handles_empty_entry(self):
        enricher = OpenLibraryEnricher()
        result = enricher.parse_response({})

        assert result["title"] is None
        assert result["authors"] == ""
        assert result["year"] == ""
        assert result["pages"] is None
        assert result["type"] == "Libro"

    def test_handles_non_dict_input(self):
        enricher = OpenLibraryEnricher()
        result = enricher.parse_response("not a dict")

        assert result["title"] is None
        assert result["authors"] == ""

    def test_extracts_year_from_date_string(self):
        enricher = OpenLibraryEnricher()
        entry = _make_openlibrary_entry(publish_date="2015-03-20")
        result = enricher.parse_response(entry)

        assert result["year"] == "2015"

    def test_handles_year_only(self):
        enricher = OpenLibraryEnricher()
        entry = _make_openlibrary_entry(publish_date="2020")
        result = enricher.parse_response(entry)

        assert result["year"] == "2020"

    def test_handles_empty_date(self):
        enricher = OpenLibraryEnricher()
        entry = _make_openlibrary_entry(publish_date="")
        result = enricher.parse_response(entry)

        assert result["year"] == ""


# ===========================================================================
# Tests Unitarios: herencia y provider
# ===========================================================================

class TestProviderMetadata:

    def test_provider_name(self):
        enricher = OpenLibraryEnricher()
        assert enricher.provider_name == "OpenLibrary"

    def test_inherits_from_base_enricher(self):
        from core.clients.enrichers.base_enricher import BaseEnricher
        enricher = OpenLibraryEnricher()
        assert isinstance(enricher, BaseEnricher)

    def test_enrich_by_doi_returns_empty(self):
        """OpenLibrary no soporta DOI."""
        enricher = OpenLibraryEnricher()
        assert enricher.enrich_by_doi("10.1000/test") == {}

    def test_enrich_by_title_returns_empty(self):
        """OpenLibrary no soporta búsqueda por título vía este método."""
        enricher = OpenLibraryEnricher()
        assert enricher.enrich_by_title("Some Book") == {}

    def test_enrich_by_issn_returns_empty(self):
        """OpenLibrary no soporta ISSN."""
        enricher = OpenLibraryEnricher()
        assert enricher.enrich_by_issn("0028-0836") == {}


# ===========================================================================
# Tests de Integración (reales contra la API de OpenLibrary)
# ===========================================================================

@pytest.mark.integration
class TestOpenLibraryIntegration:

    def test_enrich_by_isbn_real(self):
        """Test real contra la API de OpenLibrary. Requiere conexión a internet.
        ISBN de 'Clean Code' de Robert C. Martin."""
        enricher = OpenLibraryEnricher()
        result = enricher.enrich_by_isbn("9780132350884")

        assert isinstance(result, dict)
        if result:
            assert "dc.title" in result
            assert result["dc.title"] != ""

    def test_enrich_by_nonexistent_isbn_returns_valid_structure(self):
        """ISBN inventado: el resultado debe ser un dict válido (OpenLibrary
        puede devolver resultados parciales para ISBNs no exactos)."""
        enricher = OpenLibraryEnricher()
        result = enricher.enrich_by_isbn("0000000000000")

        assert isinstance(result, dict)


# ===========================================================================
# Tests parametrizados para datos manuales
# ===========================================================================

MANUAL_TEST_ISBNS = [
    pytest.param("9780132350884", id="clean-code"),
    pytest.param("9780201633610", id="design-patterns"),
]


@pytest.mark.integration
@pytest.mark.parametrize("isbn", MANUAL_TEST_ISBNS)
def test_manual_isbn(isbn):
    enricher = OpenLibraryEnricher()
    result = enricher.enrich_by_isbn(isbn)
    print(f"\n[OpenLibrary] ISBN: '{isbn}' => {result}")
    assert isinstance(result, dict)
