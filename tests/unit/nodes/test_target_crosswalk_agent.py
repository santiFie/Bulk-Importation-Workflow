"""
Tests unitarios para el agente de generación de crosswalk hacia SEDICI/DSpace (core/nodes/target_crosswalk_agent/node.py).
"""

import json
import os
import pandas as pd
import pytest

from core.nodes.target_crosswalk_agent.helpers import (
    CANONICAL_GENERIC_TO_SEDICI,
    build_and_save_target_config,
    get_valid_sedici_fields,
    identify_remnant_columns,
    load_sedici_catalog,
    map_generic_to_sedici_level1,
    validate_and_filter_target_mappings,
)
from core.nodes.target_crosswalk_agent.node import (
    generate_sedici_target_crosswalk_config,
)


@pytest.fixture
def sample_catalog():
    return load_sedici_catalog()


@pytest.fixture
def sample_pubmed_csv(tmp_path):
    """Genera un CSV de prueba con la estructura de PubMed."""
    csv_path = tmp_path / "reconciled_pubmed.csv"
    data = {
        "PMID": ["41118147", "40966355"],
        "Title": ["Kinases go where the function calls", "1206 genomes reveal origin"],
        "Authors": ["Said M, Mattiazzi A", "Crawford JE, Balcazar D"],
        "First Author": ["Said M", "Crawford JE"],
        "Journal/Book": ["Cardiovasc Res", "Science"],
        "Publication Year": [2025, 2025],
        "Create Date": ["2025/10/21", "2025/09/18"],
        "PMCID": ["", "PMC12952217"],
        "NIHMS ID": ["", "NIHMS2144453"],
        "DOI": ["10.1093/cvr/cvaf197", "10.1126/science.ads3732"],
        "inferred_type": ["journal-article", "journal-article"],
    }
    df = pd.DataFrame(data)
    df.to_csv(csv_path, index=False)
    return str(csv_path)


@pytest.fixture
def sample_source_crosswalk_config(tmp_path):
    """Configuración previa del Paso 1 (PubMed -> Genérico)."""
    cfg_path = tmp_path / "config_source_to_generic.json"
    content = [
        [
            {"left": "Title", "replace": "title", "required": True},
            {"left": "Authors", "replace": "author", "required": False},
            {"left": "Publication Year", "replace": "date", "required": False},
            {"left": "Journal/Book", "replace": "citation", "required": False},
            {"left": "DOI", "replace": "doi", "required": False},
        ],
        {
            "original_separator": "||",
            "replace_separator": "||",
            "file_delimiter": ",",
        },
    ]
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(content, f)
    return str(cfg_path)


class TestSediciCatalog:
    """Verifica la integridad del catálogo de metadatos de SEDICI."""

    def test_catalogo_no_vacio(self, sample_catalog):
        assert len(sample_catalog) > 30
        fields = [item["field"] for item in sample_catalog]
        assert "dc.title" in fields
        assert "sedici.creator.person" in fields
        assert "sedici.identifier.other" in fields
        assert "sedici.identifier.doi" in fields
        assert "sedici.relation.journalTitle" in fields
        assert "dc.type" in fields
        assert "sedici.subtype" in fields

    def test_get_valid_fields_helper(self, sample_catalog):
        schema_by_field, valid_base, allow_lang = get_valid_sedici_fields(sample_catalog)
        assert "dc.title" in valid_base
        assert "dc.title" in allow_lang
        assert "sedici.identifier.other" in valid_base
        assert "sedici.identifier.other" not in allow_lang


class TestLevel1DeterministicMapping:
    """Verifica la composición determinista del Nivel 1."""

    def test_map_generic_to_sedici_level1(self, sample_source_crosswalk_config):
        reconciled_cols = [
            "PMID", "Title", "Authors", "First Author", "Journal/Book",
            "Publication Year", "Create Date", "PMCID", "NIHMS ID", "DOI", "inferred_type"
        ]
        level1_mappings, covered_cols = map_generic_to_sedici_level1(
            reconciled_columns=reconciled_cols,
            source_crosswalk_cfg=sample_source_crosswalk_config,
        )

        mapped_targets = {m["left"]: m["replace"] for m in level1_mappings}
        assert mapped_targets["Title"] == "dc.title[es]"
        assert mapped_targets["Authors"] == "sedici.creator.person[es]"
        assert mapped_targets["Publication Year"] == "dc.date.issued"
        assert mapped_targets["Journal/Book"] == "sedici.relation.journalTitle[es]"
        assert mapped_targets["DOI"] == "sedici.identifier.doi"

        assert "Title" in covered_cols
        assert "Authors" in covered_cols
        assert "DOI" in covered_cols

    def test_identify_remnant_columns(self):
        reconciled_cols = ["PMID", "Title", "Authors", "PMCID", "DOI"]
        covered = {"Title", "Authors", "DOI"}
        remnants = identify_remnant_columns(reconciled_cols, covered)
        assert remnants == ["PMID", "PMCID"]


class TestLevel3GuardrailValidation:
    """Verifica el filtrado y blindaje contra alucinaciones del Nivel 3."""

    def test_acepta_metadatos_validos(self, sample_catalog):
        reconciled_cols = ["PMID", "PMCID", "inferred_type"]
        raw_mappings = [
            {"left": "PMID", "replace": "sedici.identifier.other"},
            {"left": "PMCID", "replace": "sedici.identifier.other"},
            {"left": "inferred_type", "replace": "sedici.subtype[es]"},
        ]
        validated = validate_and_filter_target_mappings(raw_mappings, sample_catalog, reconciled_cols)
        assert len(validated) == 3
        assert validated[0]["replace"] == "sedici.identifier.other"
        assert validated[2]["replace"] == "sedici.subtype[es]"

    def test_rechaza_campos_alucinados(self, sample_catalog):
        reconciled_cols = ["inferred_type"]
        raw_mappings = [
            {"left": "inferred_type", "replace": "sedici.custom.invented_field"},
        ]
        validated = validate_and_filter_target_mappings(raw_mappings, sample_catalog, reconciled_cols)
        assert len(validated) == 0

    def test_rescata_identificadores_desconocidos_a_identifier_other(self, sample_catalog):
        reconciled_cols = ["PMID"]
        raw_mappings = [
            {"left": "PMID", "replace": "sedici.identifier.pmid_unknown"},
        ]
        validated = validate_and_filter_target_mappings(raw_mappings, sample_catalog, reconciled_cols)
        assert len(validated) == 1
        assert validated[0]["replace"] == "sedici.identifier.other"

    def test_rechaza_columna_inexistente_en_csv(self, sample_catalog):
        reconciled_cols = ["PMID"]
        raw_mappings = [
            {"left": "NonExistentColumn", "replace": "dc.title"},
        ]
        validated = validate_and_filter_target_mappings(raw_mappings, sample_catalog, reconciled_cols)
        assert len(validated) == 0


class TestConfigSerializationAndNode:
    """Verifica la generación del config final y el nodo LangGraph."""

    def test_build_and_save_target_config(self, tmp_path):
        level1 = [{"left": "Title", "replace": "dc.title[es]", "required": True, "default": "", "filter": "trim"}]
        level2 = [{"left": "PMID", "replace": "sedici.identifier.other", "required": False, "default": "", "filter": "trim"}]
        out_file = tmp_path / "out_config.json"

        saved_path = build_and_save_target_config(level1, level2, None, str(out_file))
        assert os.path.isfile(saved_path)

        with open(saved_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert len(data) == 2
        mappings = data[0]
        options = data[1]
        assert len(mappings) == 2
        assert mappings[0]["left"] == "Title"
        assert mappings[1]["left"] == "PMID"
        assert options["replace_separator"] == "||"

    def test_node_reuses_existing_romero_config(self, tmp_path):
        """Verifica que si la fuente es Romero y ya tiene config, no la sobreescribe."""
        cfg_romero = tmp_path / "config_romero_to_sedici.json"
        cfg_romero.write_text("[]")

        state = {
            "source_name": "romero",
            "sedici_target_crosswalk_config": str(cfg_romero),
            "reconciled_csv_path": str(tmp_path / "dummy.csv"),
            "workspace_dir": str(tmp_path),
        }
        res = generate_sedici_target_crosswalk_config(state)
        assert res["sedici_target_crosswalk_config"] == str(cfg_romero)

    def test_node_full_execution_pubmed(self, sample_pubmed_csv, sample_source_crosswalk_config, tmp_path):
        """Prueba la ejecución integral del nodo con el CSV y config de muestra de PubMed."""
        state = {
            "source_name": "pubmed_test",
            "workspace_dir": str(tmp_path),
            "reconciled_csv_path": sample_pubmed_csv,
            "source_crosswalk_config": sample_source_crosswalk_config,
        }
        res = generate_sedici_target_crosswalk_config(state)
        target_config = res["sedici_target_crosswalk_config"]

        assert os.path.isfile(target_config)
        with open(target_config, "r", encoding="utf-8") as f:
            data = json.load(f)

        mappings = data[0]
        targets = {m["left"]: m["replace"] for m in mappings}

        assert targets["Title"] == "dc.title[es]"
        assert targets["Authors"] == "sedici.creator.person[es]"
        assert targets["DOI"] == "sedici.identifier.doi"
        assert targets["Publication Year"] == "dc.date.issued"
        assert targets["Journal/Book"] == "sedici.relation.journalTitle[es]"
