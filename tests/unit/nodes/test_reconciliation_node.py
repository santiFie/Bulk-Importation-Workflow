"""
Tests unitarios para el nodo MetadataReconciliation y MetadataReconciler (core/nodes/reconciliation_node.py).
"""

import json
import os
import pandas as pd
import pytest

from core.nodes.reconciliation_node import (
    MetadataReconciler,
    load_crosswalk_mappings,
    metadata_reconciliation,
)


class TestMetadataReconciler:
    """Verifica la lógica de filtrado de duplicados y propagación de enriquecimiento."""

    def test_reconcile_rest_format_filters_above_threshold(self):
        """Similitud > umbral_seguro (10) se descarta; <= 10 o ausente se retiene.
        En formato REST: id_document1 es repositorio (SEDICI) y id_document2 es la fuente.
        """
        df_source = pd.DataFrame([
            {"id": "doc_1", "title": "Duplicado Claro", "author": "Perez"},
            {"id": "doc_2", "title": "Baja Similitud", "author": "Gomez"},
            {"id": "doc_3", "title": "No Coincidente", "author": "Lopez"},
        ])

        df_dedup = pd.DataFrame([
            {"id_document1": "sedici_99", "id_document2": "doc_1", "similarity": 95.5},
            {"id_document1": "sedici_88", "id_document2": "doc_2", "similarity": 4.2},
        ])

        reconciler = MetadataReconciler(umbral_seguro=10)
        df_reconciled = reconciler.reconcile(df_dedup, df_source)

        assert len(df_reconciled) == 2
        retained_ids = set(df_reconciled["id"].tolist())
        assert retained_ids == {"doc_2", "doc_3"}

    def test_reconcile_legacy_format(self):
        """Formato legacy con columna 'total'."""
        df_source = pd.DataFrame([
            {"id": "101", "title": "Item A"},
            {"id": "102", "title": "Item B"},
        ])
        df_dedup = pd.DataFrame([
            {"id": "101", "total": 85},
            {"id": "102", "total": 5},
        ])

        reconciler = MetadataReconciler(umbral_seguro=10)
        df_reconciled = reconciler.reconcile(df_dedup, df_source)

        assert len(df_reconciled) == 1
        assert df_reconciled.iloc[0]["id"] == "102"

    def test_propagate_enrichment_fills_blanks(self):
        """Campos vacíos en el reconciliado se completan con los del CSV genérico enriquecido."""
        df_reconciled = pd.DataFrame([
            {"id": "item_1", "title": "Paper 1", "author": "", "doi": ""},
            {"id": "item_2", "title": "Paper 2", "author": "Original Author", "doi": ""},
        ])

        df_generic = pd.DataFrame([
            {"id": "item_1", "author": "Enriched Author 1", "doi": "10.1000/1", "date": "2024"},
            {"id": "item_2", "author": "New Author Should Not Overwrite", "doi": "10.1000/2", "date": "2023"},
        ])

        reconciler = MetadataReconciler()
        df_out = reconciler.propagate_enrichment(df_reconciled, df_generic)

        row1 = df_out[df_out["id"] == "item_1"].iloc[0]
        assert row1["author"] == "Enriched Author 1"
        assert row1["doi"] == "10.1000/1"

        row2 = df_out[df_out["id"] == "item_2"].iloc[0]
        assert row2["author"] == "Original Author"  # No pisa valor preexistente
        assert row2["doi"] == "10.1000/2"


class TestMetadataReconciliationNode:
    """Verifica la ejecución del nodo de LangGraph en disco."""

    def test_metadata_reconciliation_node_execution(self, tmp_path):
        source_csv = tmp_path / "source.csv"
        source_csv.write_text("id,title,author\nitem_A,Paper A,Author A\nitem_B,Paper B,Author B\n")

        dedup_csv = tmp_path / "dedup.csv"
        # id_document2 es el ID de la fuente a filtrar
        dedup_csv.write_text("id_document1,id_document2,similarity\nsedici_1,item_A,99.0\n")

        reconciled_csv = tmp_path / "reconciled.csv"

        state = {
            "source_csv_path": str(source_csv),
            "dedup_output_csv_path": str(dedup_csv),
            "reconciled_csv_path": str(reconciled_csv),
            "umbral_seguro": 10,
        }

        res = metadata_reconciliation(state)
        assert res == {}
        assert os.path.isfile(reconciled_csv)

        df_res = pd.read_csv(reconciled_csv)
        assert len(df_res) == 1
        assert df_res.iloc[0]["id"] == "item_B"

    def test_load_crosswalk_mappings_helper(self, tmp_path):
        cfg = tmp_path / "test_cfg.json"
        content = [
            [
                {"left": "Item Title", "replace": "title"},
                {"left": "Author List", "replace": "author"},
            ],
            {}
        ]
        cfg.write_text(json.dumps(content))

        s2g, g2s = load_crosswalk_mappings(str(cfg))
        assert s2g["Item Title"] == "title"
        assert g2s["title"] == "Item Title"
        assert g2s["author"] == "Author List"
