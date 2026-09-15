"""
Tests unitarios para el generador de crosswalk source_to_generic y crosswalk_base.
"""

import json
import os
import tempfile
import pytest

from core.nodes.crosswalk_base.models import CrosswalkColumnMapping
from core.nodes.crosswalk_base.helpers import (
    save_crosswalk_config_json,
    validate_column_mappings_base,
    read_crosswalk_config,
)
from core.nodes.source_to_generic.node import (
    SourceToGenericCrosswalkGenerator,
    generate_source_crosswalk_config,
    ColumnMapping,
)


class TestCrosswalkBaseModelsAndHelpers:
    """Verifica el modelo unificado y las utilidades comunes de crosswalk_base."""

    def test_column_mapping_defaults(self):
        m = CrosswalkColumnMapping(left="Authors", replace="author")
        assert m.left == "Authors"
        assert m.replace == "author"
        assert m.required is False
        assert m.default == ""
        assert m.filter == "trim"

    def test_column_mapping_backward_compatibility_alias(self):
        assert ColumnMapping is CrosswalkColumnMapping

    def test_save_and_read_crosswalk_config(self, tmp_path):
        mappings = [{"left": "col_a", "replace": "title", "required": True, "filter": "trim"}]
        settings = {"original_separator": "||", "replace_separator": "|", "file_delimiter": ","}
        out_file = str(tmp_path / "test_config.json")

        saved = save_crosswalk_config_json(mappings, settings, out_file)
        assert os.path.isfile(saved)

        read_mappings, read_settings = read_crosswalk_config(saved)
        assert len(read_mappings) == 1
        assert read_mappings[0]["left"] == "col_a"
        assert read_settings["original_separator"] == "||"

    def test_validate_column_mappings_base(self):
        raw = [
            {"left": "col1", "replace": "title"},
            {"left": "col2+col3", "replace": "author"},
            {"left": "inexistente", "replace": "date"},
        ]
        available = ["col1", "col2", "col3"]
        validated = validate_column_mappings_base(raw, available)

        assert len(validated) == 2
        lefts = {m["left"] for m in validated}
        assert "col1" in lefts
        assert "col2+col3" in lefts
        assert "inexistente" not in lefts


class TestSourceToGenericGenerator:
    """Verifica la especialización de SourceToGenericCrosswalkGenerator."""

    def test_get_existing_config(self, tmp_path):
        cfg_file = tmp_path / "crosswalk_config_springer.json"
        cfg_file.write_text("[]")

        csv_file = tmp_path / "data.csv"
        csv_file.write_text("colA,colB\n1,2\n")

        state = {
            "source_csv_path": str(csv_file),
            "source_name": "springer",
        }
        gen = SourceToGenericCrosswalkGenerator()
        res = gen.get_existing_config(state)
        assert res is not None
        assert res["source_crosswalk_config"] == str(cfg_file)

    def test_validate_mappings_against_generic_schema(self, tmp_path):
        gen = SourceToGenericCrosswalkGenerator()
        context = {
            "columns": ["Title", "Autores", "Desconocida"],
            "head_rows": [{"Title": "T", "Autores": "A", "Desconocida": "X"}],
        }
        raw = [
            {"left": "Title", "replace": "title"},
            {"left": "Autores", "replace": "author"},
            {"left": "Desconocida", "replace": "campo_inventado_no_generico"},
        ]
        validated = gen.validate_mappings(raw, context)
        assert len(validated) == 2
        replaces = {m["replace"] for m in validated}
        assert replaces == {"title", "author"}
