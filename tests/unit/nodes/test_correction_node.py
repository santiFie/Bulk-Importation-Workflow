"""
Tests unitarios para el nodo MetadataCorrections (core/nodes/correction_node.py).
"""

import os
import pandas as pd
import pytest

from core.nodes.correction_node import metadata_corrections


class TestMetadataCorrectionsNode:
    """Verifica la aplicación de correcciones programáticas por repositorio."""

    def test_missing_csv_raises_file_not_found(self, tmp_path):
        state = {
            "sedici_ready_csv_path": str(tmp_path / "inexistente.csv"),
            "source_name": "scopus",
        }
        with pytest.raises(FileNotFoundError, match="CSV no encontrado"):
            metadata_corrections(state)

    def test_generic_corrections_normalizes_language_and_separators(self, tmp_path):
        csv_file = tmp_path / "sedici_ready.csv"
        # Usamos '|' que debe ser normalizado a '||'
        df = pd.DataFrame([
            {
                "dc.title[es]": "Mi Articulo",
                "sedici.creator.person[es]": "Perez, Juan|Gomez, Maria",
                "dc.language": "Spanish",
            }
        ])
        df.to_csv(csv_file, index=False)

        state = {
            "sedici_ready_csv_path": str(csv_file),
            "source_name": "generic_repo",
        }

        res = metadata_corrections(state)
        assert res == {}

        df_corrected = pd.read_csv(csv_file)
        assert df_corrected.iloc[0]["dc.language"] == "es"
        # Separador normalizado a ||
        assert df_corrected.iloc[0]["sedici.creator.person[es]"] == "Perez, Juan||Gomez, Maria"

    def test_scopus_corrections_applied(self, tmp_path):
        """Verifica que las reglas de SCOPUS procesen autores y lugar."""
        csv_file = tmp_path / "scopus_ready.csv"
        # Lista con más de 30 autores ficticios separados por ||| (legacy de scopus)
        autores = "|||".join([f"Autor {i}" for i in range(35)])
        df = pd.DataFrame([
            {
                "dc.title[es]": "Scopus Paper",
                "sedici.creator.person[es]": autores,
                "autores_unlp_nombre": "Autor 1||Autor 2",
                "dc.language": "English",
            }
        ])
        df.to_csv(csv_file, index=False)

        state = {
            "sedici_ready_csv_path": str(csv_file),
            "source_name": "scopus",
        }

        metadata_corrections(state)

        df_corrected = pd.read_csv(csv_file)
        assert df_corrected.iloc[0]["dc.language"] == "en"
        # Debe haber conservado solo los autores UNLP porque superó los 30
        autores_finales = df_corrected.iloc[0]["sedici.creator.person[es]"]
        assert "Autor 1" in autores_finales
        assert "Autor 34" not in autores_finales
