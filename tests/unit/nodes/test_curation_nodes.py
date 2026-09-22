"""
Tests unitarios para el nodo de Curación de Metadatos (core/nodes/curation_nodes.py).
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch
import pandas as pd
import pytest

from core.nodes.curation_nodes import (
    CsvHandler,
    CurationStats,
    curate_metadata_node,
)


class TestCsvHandler:
    """Verifica el lector y escritor de CSVs canónicos de curación."""

    def test_read_and_write(self, tmp_path):
        csv_file = str(tmp_path / "test.csv")
        records = [
            {"id": "doc1", "title": "Titulo 1", "author": "Autor 1", "extra_col": "val1"},
            {"id": "doc2", "title": "Titulo 2", "author": "Autor 2", "_internal_key": "ignore"},
        ]

        CsvHandler.write(records, csv_file)
        assert os.path.isfile(csv_file)

        read_records = CsvHandler.read(csv_file)
        assert len(read_records) == 2
        assert read_records[0]["id"] == "doc1"
        assert read_records[0]["extra_col"] == "val1"
        assert "_internal_key" not in read_records[1]


class TestCurateMetadataNode:
    """Verifica el nodo curate_metadata_node de forma aislada."""

    @pytest.mark.asyncio
    async def test_missing_source_csv_raises_value_error(self, tmp_path):
        state = {"workspace_dir": str(tmp_path)}
        with pytest.raises(ValueError, match="'source_csv_path' no está definido"):
            await curate_metadata_node(state)

    @pytest.mark.asyncio
    async def test_non_existent_csv_returns_error_dict(self, tmp_path):
        state = {
            "source_csv_path": str(tmp_path / "inexistente.csv"),
            "workspace_dir": str(tmp_path),
        }
        res = await curate_metadata_node(state)
        assert "error" in res["curation_stats"]
        assert res["pending_to_review_csv_path"] is None

    @pytest.mark.asyncio
    async def test_empty_csv_writes_empty_output(self, tmp_path):
        empty_csv = tmp_path / "empty.csv"
        empty_csv.write_text("id,title,author\n")

        state = {
            "source_csv_path": str(empty_csv),
            "workspace_dir": str(tmp_path),
        }
        res = await curate_metadata_node(state)
        assert os.path.isfile(res["curated_csv_path"])
        assert res["pending_to_review_csv_path"] is None
        assert res["curation_stats"]["total"] == 0

    @pytest.mark.asyncio
    async def test_clean_batch_zero_tokens_never_calls_agent(self, tmp_path):
        """Si los registros son limpios (Capa 1), el agente LLM nunca se invoca."""
        source_csv = tmp_path / "clean_source.csv"
        records = [
            {
                "id": "item_01",
                "title": "A Systematic Review of Clean Architecture",
                "author": "Martin, Robert C.",
                "description": "An exhaustive analysis.",
                "date": "2024",
                "type": "Article",
                "subject": "Software Engineering",
                "issn": "1234-5678",
                "isbn": "",
                "doi": "10.1000/182",
                "citation": "Journal of Software",
                "rights": "Open Access",
                "rightsurl": "http://example.com/license",
            }
        ]
        CsvHandler.write(records, str(source_csv))

        state = {
            "source_csv_path": str(source_csv),
            "workspace_dir": str(tmp_path),
        }

        with patch("core.nodes.curation_nodes.build_metadata_curator_agent") as mock_build_agent:
            res = await curate_metadata_node(state)

            mock_build_agent.assert_not_called()
            assert res["curation_stats"]["limpias"] == 1
            assert res["curation_stats"]["sospechosas_detectadas"] == 0
            assert res["pending_to_review_csv_path"] is None
            assert os.path.isfile(res["curated_csv_path"])

    @pytest.mark.asyncio
    async def test_unresolvable_items_sent_to_pending_quarantine(self, tmp_path):
        """Registros sin datos o con anomalías graves no subsanadas van a cuarentena."""
        source_csv = tmp_path / "quarantine_source.csv"
        # Fila sin ningún dato bibliográfico útil -> clasificada como sin_datos
        records = [
            {
                "id": "empty_item",
                "title": "",
                "author": "",
                "description": "",
                "date": "",
                "type": "",
                "subject": "",
                "issn": "",
                "isbn": "",
                "doi": "",
                "citation": "",
                "rights": "",
                "rightsurl": "",
            }
        ]
        CsvHandler.write(records, str(source_csv))

        state = {
            "source_csv_path": str(source_csv),
            "workspace_dir": str(tmp_path),
        }

        res = await curate_metadata_node(state)

        assert res["curation_stats"]["sin_datos"] == 1
        assert res["pending_to_review_csv_path"] is not None
        assert os.path.isfile(res["pending_to_review_csv_path"])

        df_pending = pd.read_csv(res["pending_to_review_csv_path"])
        assert len(df_pending) == 1
        assert df_pending.iloc[0]["id"] == "empty_item"
