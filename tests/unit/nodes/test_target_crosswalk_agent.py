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
    validate_target_config_compatibility,
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

    def test_map_generic_pure_format(self):
        """Verifica la resolución directa determinista para formato canónico genérico puro (MinIO/PDF)."""
        reconciled_cols = ["id", "title", "author", "date", "type", "doi", "issn", "citation"]
        level1_mappings, covered_cols = map_generic_to_sedici_level1(
            reconciled_columns=reconciled_cols,
            source_crosswalk_cfg=None,
        )
        mapped_targets = {m["left"]: m["replace"] for m in level1_mappings}
        assert mapped_targets["title"] == "dc.title[es]"
        assert mapped_targets["author"] == "sedici.creator.person[es]"
        assert mapped_targets["date"] == "dc.date.issued"
        assert mapped_targets["type"] == "dc.type"
        assert mapped_targets["doi"] == "sedici.identifier.doi"
        assert mapped_targets["issn"] == "sedici.identifier.issn"
        assert mapped_targets["citation"] == "sedici.relation.journalTitle[es]"
        # 'id' no está en el catálogo canónico genérico, por lo que queda remanente
        assert "id" not in covered_cols
        remnants = identify_remnant_columns(reconciled_cols, covered_cols)
        assert remnants == ["id"]

    def test_map_hybrid_enriched_format(self, sample_source_crosswalk_config):
        """Verifica la resolución híbrida: columnas de la fuente + columnas inyectadas por enriquecimiento."""
        reconciled_cols = [
            "Title", "Authors", "Journal/Book", "Publication Year", "DOI",
            "doi", "issn", "Volume", "Issue"
        ]
        level1_mappings, covered_cols = map_generic_to_sedici_level1(
            reconciled_columns=reconciled_cols,
            source_crosswalk_cfg=sample_source_crosswalk_config,
        )
        mapped_targets = {m["left"]: m["replace"] for m in level1_mappings}
        # Columnas de la fuente mapeadas vía config
        assert mapped_targets["Title"] == "dc.title[es]"
        assert mapped_targets["Authors"] == "sedici.creator.person[es]"
        assert mapped_targets["DOI"] == "sedici.identifier.doi"
        # Columnas genéricas inyectadas en minúsculas resueltas directamente
        assert mapped_targets["issn"] == "sedici.identifier.issn"
        # Remanentes esperados
        remnants = identify_remnant_columns(reconciled_cols, covered_cols)
        assert set(remnants) == {"Volume", "Issue"}

    def test_map_composite_plus_columns_both_present(self, tmp_path):
        """Verifica que si la config tiene columnas compuestas con '+' y ambas están en el CSV, se cubran."""
        cfg_path = tmp_path / "composite_cfg.json"
        content = [
            [
                {"left": "Family_Name + Given_Names", "replace": "author", "required": False},
                {"left": "Article_Title", "replace": "title", "required": True},
            ],
            {"replace_separator": "||"},
        ]
        cfg_path.write_text(json.dumps(content))

        reconciled_cols = ["Family_Name", "Given_Names", "Article_Title", "Extra"]
        level1_mappings, covered_cols = map_generic_to_sedici_level1(
            reconciled_columns=reconciled_cols,
            source_crosswalk_cfg=str(cfg_path),
        )
        mapped_targets = {m["left"]: m["replace"] for m in level1_mappings}
        assert mapped_targets["Family_Name + Given_Names"] == "sedici.creator.person[es]"
        assert mapped_targets["Article_Title"] == "dc.title[es]"
        assert "Family_Name" in covered_cols
        assert "Given_Names" in covered_cols
        assert identify_remnant_columns(reconciled_cols, covered_cols) == ["Extra"]

    def test_map_composite_plus_columns_partial_missing(self, tmp_path):
        """Verifica que si falta alguna columna de una expresión compuesta '+', no se genere el mapeo."""
        cfg_path = tmp_path / "composite_cfg.json"
        content = [
            [
                {"left": "Family_Name + Given_Names", "replace": "author", "required": False},
            ],
            {},
        ]
        cfg_path.write_text(json.dumps(content))

        reconciled_cols = ["Family_Name", "Article_Title"]  # Falta Given_Names
        level1_mappings, covered_cols = map_generic_to_sedici_level1(
            reconciled_columns=reconciled_cols,
            source_crosswalk_cfg=str(cfg_path),
        )
        assert len(level1_mappings) == 0
        assert "Family_Name" not in covered_cols
        assert "Family_Name" in identify_remnant_columns(reconciled_cols, covered_cols)


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

    def test_guardrail_strips_disallowed_lang_qualifier(self, sample_catalog):
        """Verifica que el guardrail remueva [es] si el campo no permite calificador lingüístico."""
        reconciled_cols = ["DOI_Col", "Date_Col"]
        raw_mappings = [
            {"left": "DOI_Col", "replace": "sedici.identifier.doi[es]"},
            {"left": "Date_Col", "replace": "dc.date.issued[es]"},
        ]
        validated = validate_and_filter_target_mappings(raw_mappings, sample_catalog, reconciled_cols)
        assert len(validated) == 2
        assert validated[0]["replace"] == "sedici.identifier.doi"
        assert validated[1]["replace"] == "dc.date.issued"

    def test_guardrail_preserves_allowed_lang_qualifier(self, sample_catalog):
        """Verifica que el guardrail conserve [es] cuando el metadato sí admite calificador lingüístico."""
        reconciled_cols = ["Title", "Authors"]
        raw_mappings = [
            {"left": "Title", "replace": "dc.title[es]"},
            {"left": "Authors", "replace": "sedici.creator.person[es]"},
        ]
        validated = validate_and_filter_target_mappings(raw_mappings, sample_catalog, reconciled_cols)
        assert len(validated) == 2
        assert validated[0]["replace"] == "dc.title[es]"
        assert validated[1]["replace"] == "sedici.creator.person[es]"


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


class TestUserConfigCompatibilityAndResilience:
    """Verifica la validación de compatibilidad y resiliencia con LLM ante configs provistas por el usuario."""

    def test_validate_compatibility_success(self, sample_catalog, tmp_path):
        cfg_file = tmp_path / "custom_valid.json"
        content = [
            [
                {"left": "Title", "replace": "dc.title[es]", "required": True},
                {"left": "Authors", "replace": "sedici.creator.person[es]", "required": False},
            ],
            {"replace_separator": "||"},
        ]
        cfg_file.write_text(json.dumps(content))

        reconciled_cols = ["Title", "Authors", "ExtraCol"]
        res = validate_target_config_compatibility(str(cfg_file), reconciled_cols, sample_catalog)

        assert res["is_valid"] is True
        assert len(res["valid_mappings"]) == 2
        assert len(res["incompatible_mappings"]) == 0
        assert len(res["invalid_target_fields"]) == 0

    def test_validate_compatibility_detects_missing_column(self, sample_catalog, tmp_path):
        cfg_file = tmp_path / "custom_missing.json"
        content = [
            [
                {"left": "NonExistentCol", "replace": "dc.title[es]"},
                {"left": "Authors", "replace": "sedici.creator.person[es]"},
            ],
            {},
        ]
        cfg_file.write_text(json.dumps(content))

        reconciled_cols = ["Title", "Authors"]
        res = validate_target_config_compatibility(str(cfg_file), reconciled_cols, sample_catalog)

        assert res["is_valid"] is False
        assert len(res["incompatible_mappings"]) == 1
        assert res["incompatible_mappings"][0]["mapping"]["left"] == "NonExistentCol"
        assert len(res["valid_mappings"]) == 1

    def test_validate_compatibility_detects_invalid_target_field(self, sample_catalog, tmp_path):
        cfg_file = tmp_path / "custom_invalid_field.json"
        content = [
            [
                {"left": "Title", "replace": "sedici.invented.field"},
            ],
            {},
        ]
        cfg_file.write_text(json.dumps(content))

        reconciled_cols = ["Title"]
        res = validate_target_config_compatibility(str(cfg_file), reconciled_cols, sample_catalog)

        assert res["is_valid"] is False
        assert len(res["invalid_target_fields"]) == 1
        assert res["invalid_target_fields"][0]["mapping"]["replace"] == "sedici.invented.field"

    def test_node_reuses_compatible_user_config(self, tmp_path):
        """Verifica que si la config provista por el usuario es 100% compatible, se reutilice directamente."""
        csv_file = tmp_path / "reconciled.csv"
        csv_file.write_text("Title,Authors\nTest Title,Author 1\n")

        cfg_file = tmp_path / "user_config.json"
        content = [
            [
                {"left": "Title", "replace": "dc.title[es]"},
                {"left": "Authors", "replace": "sedici.creator.person[es]"},
            ],
            {"replace_separator": "||", "file_delimiter": ","},
        ]
        cfg_file.write_text(json.dumps(content))

        state = {
            "source_name": "scopus_test",
            "workspace_dir": str(tmp_path),
            "reconciled_csv_path": str(csv_file),
            "sedici_target_crosswalk_config": str(cfg_file),
        }
        res = generate_sedici_target_crosswalk_config(state)
        assert res["sedici_target_crosswalk_config"] == str(cfg_file)

    def test_node_resilience_repairs_incompatible_user_config(self, tmp_path, monkeypatch):
        """
        Verifica que ante una incompatibilidad en la config provista por el usuario,
        el agente invoque al LLM para corregirla y guarde la configuración reparada.
        """
        csv_file = tmp_path / "reconciled.csv"
        csv_file.write_text("Title,Author_Names\nSample Title,Sample Author\n")

        # Config de usuario con una columna 'Authors' inexistente en el CSV (en el CSV se llama Author_Names)
        cfg_file = tmp_path / "broken_user_config.json"
        content = [
            [
                {"left": "Title", "replace": "dc.title[es]"},
                {"left": "Authors", "replace": "sedici.creator.person[es]"},
            ],
            {"replace_separator": "||", "file_delimiter": ","},
        ]
        cfg_file.write_text(json.dumps(content))

        # Simulamos la corrección del LLM que mapea Author_Names
        def mock_execute_llm(self, context, remaining_cols, feedback=None):
            return [{"left": "Author_Names", "replace": "sedici.creator.person[es]"}]

        from core.nodes.target_crosswalk_agent.node import TargetSediciCrosswalkGenerator
        monkeypatch.setattr(TargetSediciCrosswalkGenerator, "execute_llm_mapping", mock_execute_llm)

        state = {
            "source_name": "broken_source",
            "workspace_dir": str(tmp_path),
            "reconciled_csv_path": str(csv_file),
            "sedici_target_crosswalk_config": str(cfg_file),
        }
        res = generate_sedici_target_crosswalk_config(state)
        repaired_path = res["sedici_target_crosswalk_config"]

        assert os.path.isfile(repaired_path)
        assert repaired_path != str(cfg_file)

        with open(repaired_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        mappings = data[0]
        mapped_dict = {m["left"]: m["replace"] for m in mappings}

        # Debe conservar el mapping válido (Title) y añadir el reparado (Author_Names)
        assert mapped_dict["Title"] == "dc.title[es]"
        assert mapped_dict["Author_Names"] == "sedici.creator.person[es]"
        assert "Authors" not in mapped_dict

    def test_validate_compatibility_composite_columns(self, sample_catalog, tmp_path):
        """Verifica la validación de compatibilidad cuando el usuario incluye columnas compuestas '+'."""
        cfg_file = tmp_path / "custom_composite.json"
        content = [
            [
                {"left": "Family_Name + Given_Names", "replace": "sedici.creator.person[es]"},
                {"left": "Title", "replace": "dc.title[es]"},
            ],
            {"replace_separator": "||"},
        ]
        cfg_file.write_text(json.dumps(content))

        # Caso exitoso: ambas columnas presentes en el CSV
        res_ok = validate_target_config_compatibility(
            str(cfg_file), ["Family_Name", "Given_Names", "Title"], sample_catalog
        )
        assert res_ok["is_valid"] is True
        assert len(res_ok["valid_mappings"]) == 2

        # Caso fallido: falta Given_Names en el CSV
        res_fail = validate_target_config_compatibility(
            str(cfg_file), ["Family_Name", "Title"], sample_catalog
        )
        assert res_fail["is_valid"] is False
        assert len(res_fail["incompatible_mappings"]) == 1
        assert res_fail["incompatible_mappings"][0]["missing_parts"] == ["Given_Names"]

    def test_validate_compatibility_empty_or_missing_file(self, sample_catalog, tmp_path):
        """Verifica el manejo de error cuando el archivo de configuración no existe o está vacío."""
        # Archivo inexistente
        res_missing = validate_target_config_compatibility(
            str(tmp_path / "non_existent.json"), ["Title"], sample_catalog
        )
        assert res_missing["is_valid"] is False

        # Archivo con mappings vacíos
        empty_cfg = tmp_path / "empty_cfg.json"
        empty_cfg.write_text("[]")
        res_empty = validate_target_config_compatibility(str(empty_cfg), ["Title"], sample_catalog)
        assert res_empty["is_valid"] is False


