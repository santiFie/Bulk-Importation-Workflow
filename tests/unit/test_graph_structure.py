"""
Tests unitarios para la estructura y topología del pipeline y subgrafos.
"""

import os
import tempfile
import pandas as pd
import pytest

from core.subgraphs.crosswalk_dedup import build_crosswalk_dedup_subgraph
from core.graph import create_graph
from core.nodes.pipeline_nodes import metadata_reconciliation


@pytest.mark.asyncio
async def test_crosswalk_dedup_subgraph_contains_enrichment():
    """Valida que CrosswalkDedupSubgraph contenga el nodo EnrichmentSubgraph."""
    subgraph = await build_crosswalk_dedup_subgraph()
    assert "EnrichmentSubgraph" in subgraph.nodes
    assert "Deduplicate" in subgraph.nodes
    assert "MetadataReconciliation" in subgraph.nodes


@pytest.mark.asyncio
async def test_main_graph_topology():
    """Valida los nodos principales del grafo ensamblado."""
    main_graph = await create_graph(persistence_saver=None)
    nodes = main_graph.nodes
    assert "SetupWorkspace" in nodes
    assert "IngestSubgraph" in nodes
    assert "CrosswalkDedupSubgraph" in nodes
    assert "ExportSubgraph" in nodes


def test_metadata_reconciliation_propagates_enriched_fields():
    """Valida que metadata_reconciliation propague los campos enriquecidos de generic_source."""
    with tempfile.TemporaryDirectory() as tmpdir:
        source_csv = os.path.join(tmpdir, "source.csv")
        generic_csv = os.path.join(tmpdir, "generic_source.csv")
        dedup_csv = os.path.join(tmpdir, "dedup.csv")
        reconciled_csv = os.path.join(tmpdir, "reconciled.csv")

        # CSV de origen original (sin año ni autor)
        pd.DataFrame([
            {"id": "doc1", "title": "Doc Uno", "source_col": "val1"}
        ]).to_csv(source_csv, index=False)

        # CSV genérico enriquecido
        pd.DataFrame([
            {"id": "doc1", "title": "Doc Uno", "author": "Autor Enriquecido", "date": "2024", "doi": "10.123/456"}
        ]).to_csv(generic_csv, index=False)

        # CSV de deduplicador (doc1 no es duplicado)
        pd.DataFrame([
            {"id": "doc1", "total": 0}
        ]).to_csv(dedup_csv, index=False)

        state = {
            "source_csv_path": source_csv,
            "generic_source_csv_path": generic_csv,
            "dedup_output_csv_path": dedup_csv,
            "reconciled_csv_path": reconciled_csv,
            "umbral_seguro": 10,
            "enrichment_enabled": True,
        }

        metadata_reconciliation(state)

        df_rec = pd.read_csv(reconciled_csv)
        assert len(df_rec) == 1
        assert df_rec.at[0, "id"] == "doc1"
        assert df_rec.at[0, "source_col"] == "val1"
        assert df_rec.at[0, "author"] == "Autor Enriquecido"
        assert str(df_rec.at[0, "date"]) == "2024"
        assert df_rec.at[0, "doi"] == "10.123/456"
