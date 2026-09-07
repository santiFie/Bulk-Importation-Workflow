"""
Tests de integración para el subgrafo de Crosswalk y Deduplicación.

Módulo bajo prueba: core/subgraphs/crosswalk_dedup.py
Subgrafo: CrosswalkDedupSubgraph

Este test ejercita la topología completa del subgrafo LangGraph conectándose
a los servicios reales de backend (Crosswalk y Deduplicador) y consumiendo
tokens reales mediante el agente LLM en el nodo GenerateSourceCrosswalkConfig.
"""

import asyncio
import os
import shutil
import tempfile
import pandas as pd
import pytest

from core.subgraphs.crosswalk_dedup import (
    build_crosswalk_dedup_subgraph,
    route_source_crosswalk,
    bypass_source_crosswalk,
)
from core.state import State


# ---------------------------------------------------------------------------
# Paths base del proyecto y fixtures
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "tests", "data")
CROSSWALK_DEDUP_DIR = os.path.join(DATA_DIR, "crosswalk_dedup")
CONFIGS_DIR = os.path.join(PROJECT_ROOT, "core", "scripts", "crosswalk", "configs")

# Rutas de datos según el tipo de esquema de entrada al subgrafo
GENERIC_INPUTS_DIR = os.path.join(CROSSWALK_DEDUP_DIR, "generic_inputs")
SOURCE_INPUTS_DIR = os.path.join(CROSSWALK_DEDUP_DIR, "source_inputs")
REPOSITORY_DIR = os.path.join(CROSSWALK_DEDUP_DIR, "repository")

GENERIC_SAMPLE_CSV = os.path.join(GENERIC_INPUTS_DIR, "pdf_ingest_output.csv")
SPRINGER_SAMPLE_CSV = os.path.join(SOURCE_INPUTS_DIR, "springer_sample.csv")
SEDICI_SAMPLE_CSV = os.path.join(REPOSITORY_DIR, "sedici_sample.csv")
SEDICI_CROSSWALK_CONFIG = os.path.join(CONFIGS_DIR, "export_10915_crosswalkconfig.json")


class TestCrosswalkDedupSubgraphTopology:
    """Verifica la compilación, topología y funciones auxiliares del subgrafo."""

    @pytest.mark.asyncio
    async def test_compilacion_y_nodos_presentes(self):
        """El subgrafo compila correctamente y registra todos los nodos esperados."""
        subgraph = await build_crosswalk_dedup_subgraph()

        assert subgraph is not None
        node_names = set(subgraph.nodes.keys())

        expected_nodes = {
            "GenerateSourceCrosswalkConfig",
            "MapSourceToGeneric",
            "BypassSourceCrosswalk",
            "EnrichmentSubgraph",
            "MapSediciToGeneric",
            "Deduplicate",
            "MetadataReconciliation",
        }
        for node in expected_nodes:
            assert node in node_names, f"El nodo '{node}' debe estar registrado en el subgrafo."

    def test_route_source_crosswalk(self):
        """Verifica el enrutador condicional entre Bypass (PDFs) y Agente LLM (CSV)."""
        assert route_source_crosswalk({"input_source_type": "pdf_minio"}) == "BypassSourceCrosswalk"
        assert route_source_crosswalk({"input_source_type": "csv"}) == "GenerateSourceCrosswalkConfig"
        assert route_source_crosswalk({}) == "GenerateSourceCrosswalkConfig"

    @pytest.mark.asyncio
    async def test_bypass_source_crosswalk(self, tmp_path):
        """El nodo puente copia directamente el archivo hacia generic_source_csv_path."""
        src = tmp_path / "curated_input.csv"
        src.write_text("id,title,author\n1,Test Title,Author A\n", encoding="utf-8")
        dst = tmp_path / "generic_source.csv"

        state = {
            "source_csv_path": str(src),
            "generic_source_csv_path": str(dst),
        }

        res = await bypass_source_crosswalk(state)
        assert res == {}
        assert os.path.isfile(str(dst))
        assert dst.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")


class TestCrosswalkDedupSubgraphE2E:
    """
    Tests de integración End-to-End para el subgrafo usando servicios y LLM reales.
    """

    @pytest.mark.asyncio
    async def test_flujo_completo_rama_csv_con_llm_y_servicios_reales(self, tmp_path):
        """
        Ejecuta el subgrafo completo en la rama CSV:
          1. GenerateSourceCrosswalkConfig: Invocación real a LLM para inferir config.
          2. MapSourceToGeneric y MapSediciToGeneric: Conversión vía API REST de Crosswalk.
          3. EnrichmentSubgraph: Se omite (enrichment_enabled=False).
          4. Deduplicate: Detección real de duplicados vía API REST del Deduplicador.
          5. MetadataReconciliation: Cruce y generación del CSV reconciliado.
        """
        # Copiar la muestra acotada de la fuente para optimizar tiempo y consumo de tokens
        source_sample_path = str(tmp_path / "source_sample.csv")
        shutil.copy(SPRINGER_SAMPLE_CSV, source_sample_path)

        sedici_sample_path = str(tmp_path / "sedici_sample.csv")
        shutil.copy(SEDICI_SAMPLE_CSV, sedici_sample_path)

        source_name = "springer_e2e_test"
        generic_source_path = str(tmp_path / "generic_source.csv")
        generic_sedici_path = str(tmp_path / "generic_sedici.csv")
        dedup_output_path = str(tmp_path / "dedup_output.csv")
        reconciled_path = str(tmp_path / "reconciled.csv")

        state: State = {
            "workspace_dir": str(tmp_path),
            "source_name": source_name,
            "input_source_type": "csv",
            "source_csv_path": source_sample_path,
            "repository_csv_path": sedici_sample_path,
            "sedici_crosswalk_config": SEDICI_CROSSWALK_CONFIG,
            "generic_source_csv_path": generic_source_path,
            "generic_sedici_csv_path": generic_sedici_path,
            "dedup_output_csv_path": dedup_output_path,
            "reconciled_csv_path": reconciled_path,
            "enrichment_enabled": False,
            "umbral_seguro": 10,
            "umbral_revision": 30,
        }

        # Asegurar que no exista un config previo para que el LLM sea forzado a generarlo
        expected_config = os.path.join(str(tmp_path), f"crosswalk_config_{source_name}.json")
        if os.path.exists(expected_config):
            os.remove(expected_config)

        subgraph = await build_crosswalk_dedup_subgraph()
        final_state = await subgraph.ainvoke(state)

        # 1. Validar que el agente LLM generó el crosswalk config
        assert os.path.isfile(expected_config), "El LLM debió generar el archivo de configuración de crosswalk."
        assert "source_crosswalk_config" in final_state

        # 2. Validar que MapSourceToGeneric y MapSediciToGeneric produjeron los CSVs genéricos
        assert os.path.isfile(generic_source_path), "generic_source.csv debió generarse vía API de crosswalk."
        assert os.path.isfile(generic_sedici_path), "generic_sedici.csv debió generarse vía API de crosswalk."
        df_gen_src = pd.read_csv(generic_source_path)
        assert len(df_gen_src) == 3

        # 3. Validar que Deduplicate ejecutó en el backend y produjo el reporte de duplicados
        assert os.path.isfile(dedup_output_path), "dedup_output.csv debió generarse vía API del deduplicador."
        df_dedup = pd.read_csv(dedup_output_path)
        assert not df_dedup.empty
        assert "similarity" in df_dedup.columns or "total" in df_dedup.columns

        # 4. Validar que MetadataReconciliation generó el archivo final
        assert os.path.isfile(reconciled_path), "reconciled.csv debió ser generado al final del subgrafo."
        df_rec = pd.read_csv(reconciled_path)
        assert not df_rec.empty

    @pytest.mark.asyncio
    async def test_flujo_rama_pdf_minio_bypass_crosswalk(self, tmp_path):
        """
        Ejecuta el subgrafo con input_source_type='pdf_minio':
          - Debe tomar la ruta BypassSourceCrosswalk (sin llamar al LLM).
          - Ejecuta MapSediciToGeneric, Deduplicate y MetadataReconciliation.
        """
        # Usar entrada en esquema genérico (salida de ingest/PDFs) para verificar bypass
        generic_input_path = str(tmp_path / "generic_input.csv")
        shutil.copy(GENERIC_SAMPLE_CSV, generic_input_path)

        sedici_sample_path = str(tmp_path / "sedici_sample.csv")
        shutil.copy(SEDICI_SAMPLE_CSV, sedici_sample_path)

        generic_source_path = str(tmp_path / "generic_source.csv")
        generic_sedici_path = str(tmp_path / "generic_sedici.csv")
        dedup_output_path = str(tmp_path / "dedup_output.csv")
        reconciled_path = str(tmp_path / "reconciled.csv")

        state: State = {
            "workspace_dir": str(tmp_path),
            "source_name": "curated_pdf_test",
            "input_source_type": "pdf_minio",
            "curated_csv_path": generic_input_path,
            "source_csv_path": generic_input_path,
            "repository_csv_path": sedici_sample_path,
            "sedici_crosswalk_config": SEDICI_CROSSWALK_CONFIG,
            "generic_source_csv_path": generic_source_path,
            "generic_sedici_csv_path": generic_sedici_path,
            "dedup_output_csv_path": dedup_output_path,
            "reconciled_csv_path": reconciled_path,
            "enrichment_enabled": False,
            "umbral_seguro": 10,
            "umbral_revision": 30,
        }

        subgraph = await build_crosswalk_dedup_subgraph()
        final_state = await subgraph.ainvoke(state)

        # Verificar que el bypass copió el CSV directamente
        assert os.path.isfile(generic_source_path)
        assert os.path.isfile(reconciled_path)
        df_rec = pd.read_csv(reconciled_path)
        assert len(df_rec) == 3
        assert "39-jaiio-ast-04.pdf-PDFA.pdf" in df_rec["id"].values
