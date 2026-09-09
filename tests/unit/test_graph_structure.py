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


def test_resolve_source_id_column_scenarios():
    """Valida la resolución dinámica de la columna ID en diferentes escenarios."""
    from core.nodes.pipeline_nodes import _resolve_source_id_column

    # 1. Caso MinIO / PDFs directos
    df_minio = pd.DataFrame([{"id": "doc1", "title": "Doc"}])
    state_minio = {"input_source_type": "pdf_minio"}
    assert _resolve_source_id_column(state_minio, df_minio) == "id"

    # 2. Caso Crosswalk Config con mapeo explícito a 'id' (ej. PubMed con DOI)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        import json
        json.dump([
            [
                {"left": "DOI", "replace": "id", "required": True},
                {"left": "Journal/Book", "replace": "citation"}
            ],
            {"replace_separator": "|"}
        ], f)
        config_path = f.name

    try:
        df_pubmed = pd.DataFrame([{"DOI": "10.123/abc", "Title": "Title"}])
        state_crosswalk = {
            "input_source_type": "csv",
            "source_crosswalk_config": config_path,
        }
        assert _resolve_source_id_column(state_crosswalk, df_pubmed) == "DOI"
    finally:
        if os.path.exists(config_path):
            os.remove(config_path)

    # 3. Caso Fallback heurístico
    df_heuristic = pd.DataFrame([{"pmid": "12345", "title": "Title"}])
    state_heuristic = {"input_source_type": "csv"}
    assert _resolve_source_id_column(state_heuristic, df_heuristic) == "pmid"


def test_metadata_reconciliation_with_custom_id_and_citation_propagation():
    """
    Valida que metadata_reconciliation funcione con un CSV fuente sin columna 'id'
    (ej. con 'DOI') y propague 'citation' tanto a la columna genérica como a 'Journal/Book'.
    """
    import json

    with tempfile.TemporaryDirectory() as tmpdir:
        source_csv = os.path.join(tmpdir, "source.csv")
        generic_csv = os.path.join(tmpdir, "generic_source.csv")
        dedup_csv = os.path.join(tmpdir, "dedup.csv")
        reconciled_csv = os.path.join(tmpdir, "reconciled.csv")
        config_path = os.path.join(tmpdir, "crosswalk_config.json")

        # Configuración que mapea DOI -> id y Journal/Book -> citation
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump([
                [
                    {"left": "DOI", "replace": "id", "required": True},
                    {"left": "Journal/Book", "replace": "citation"}
                ],
                {"replace_separator": "|"}
            ], f)

        # CSV fuente original: columna DOI como identificador y Journal/Book vacía
        pd.DataFrame([
            {"DOI": "10.1000/182", "Title": "Articulo Test", "Journal/Book": ""}
        ]).to_csv(source_csv, index=False)

        # CSV genérico enriquecido con 'citation'
        pd.DataFrame([
            {"id": "10.1000/182", "title": "Articulo Test", "citation": "Nature Medicine", "doi": "10.1000/182"}
        ]).to_csv(generic_csv, index=False)

        # Deduplicador indica similitud 0 (no es duplicado)
        pd.DataFrame([
            {"id_document1": "sedici_1", "id_document2": "10.1000/182", "similarity": 0}
        ]).to_csv(dedup_csv, index=False)

        state = {
            "source_csv_path": source_csv,
            "generic_source_csv_path": generic_csv,
            "dedup_output_csv_path": dedup_csv,
            "reconciled_csv_path": reconciled_csv,
            "source_crosswalk_config": config_path,
            "umbral_seguro": 10,
            "enrichment_enabled": True,
        }

        metadata_reconciliation(state)

        df_rec = pd.read_csv(reconciled_csv)
        assert len(df_rec) == 1
        assert df_rec.at[0, "DOI"] == "10.1000/182"
        # Debe existir la columna genérica 'citation'
        assert df_rec.at[0, "citation"] == "Nature Medicine"
        # Y debe haberse actualizado la columna de origen original 'Journal/Book'
        assert df_rec.at[0, "Journal/Book"] == "Nature Medicine"

