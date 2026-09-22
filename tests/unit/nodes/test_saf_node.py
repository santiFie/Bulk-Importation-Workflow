"""
Tests unitarios para el nodo GenerateSafToImport (core/nodes/saf_node.py).
"""

import os
import xml.etree.ElementTree as ET
import pandas as pd
import pytest

from core.nodes.saf_node import generate_saf_to_import, _prepare_dataframe


class TestSafNode:
    """Verifica la generación del Simple Archive Format (SAF) para DSpace."""

    def test_prepare_dataframe_cleans_unnamed_and_ensures_files_col(self, tmp_path):
        csv_file = tmp_path / "dirty.csv"
        df = pd.DataFrame({
            "Unnamed: 0": [0, 1],
            "dc.title[es]": ["Articulo A", "Articulo B"],
            "dc.date.issued": ["2023", "2024"],
        })
        df.to_csv(csv_file, index=False)

        clean_df = _prepare_dataframe(str(csv_file))

        assert "Unnamed: 0" not in clean_df.columns
        assert "files" in clean_df.columns
        assert list(clean_df.columns)[0] == "files"

    def test_generate_saf_to_import_success(self, tmp_path):
        sedici_ready_csv = tmp_path / "sedici_ready.csv"
        df = pd.DataFrame([
            {
                "files": "",
                "dc.title[es]": "Impacto de la Inteligencia Artificial",
                "sedici.creator.person[es]": "Perez, Juan",
                "dc.date.issued": "2024",
                "dc.type[es]": "Articulo",
            },
            {
                "files": "",
                "dc.title[es]": "Metodologia de la Investigacion",
                "sedici.creator.person[es]": "Gomez, Maria",
                "dc.date.issued": "2023",
                "dc.type[es]": "Libro",
            },
        ])
        df.to_csv(sedici_ready_csv, index=False)

        saf_out = tmp_path / "saf_output"
        state = {
            "sedici_ready_csv_path": str(sedici_ready_csv),
            "saf_output_path": str(saf_out),
        }

        res = generate_saf_to_import(state)
        assert res == {}

        # DspaceArchive numera los directorios comenzando en 1: item_001, item_002
        assert os.path.isdir(saf_out)
        item1 = saf_out / "item_001"
        item2 = saf_out / "item_002"
        assert os.path.isdir(item1)
        assert os.path.isdir(item2)

        # Verificar existencia de dublin_core.xml y contents
        dc_xml_file = item1 / "dublin_core.xml"
        contents_file = item1 / "contents"
        assert os.path.isfile(dc_xml_file)
        assert os.path.isfile(contents_file)

        # Parsear XML y validar contenido
        tree = ET.parse(dc_xml_file)
        root = tree.getroot()
        assert root.tag == "dublin_core"

        dcvalues = root.findall("dcvalue")
        elements = {
            (elem.attrib.get("element"), elem.attrib.get("language")): elem.text
            for elem in dcvalues
        }

        assert elements.get(("title", "es")) == "Impacto de la Inteligencia Artificial"
        assert any(elem.text == "2024" for elem in dcvalues)

    def test_generate_saf_missing_csv_handled_cleanly(self, tmp_path):
        state = {
            "sedici_ready_csv_path": str(tmp_path / "no_existe.csv"),
            "saf_output_path": str(tmp_path / "saf_output"),
        }
        res = generate_saf_to_import(state)
        assert res == {}
