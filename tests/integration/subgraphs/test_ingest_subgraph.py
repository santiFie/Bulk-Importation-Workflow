"""
Tests de integración para el subgrafo de Ingesta.

Módulo bajo prueba: core/subgraphs/ingest.py
Subgrafo: IngestSubgraph

Topología del subgrafo:
  START
    ├─ (csv)       → END (pasa directo sin procesar)
    └─ (pdf_minio) → PDFIngest → CurateMetadata → END

Este test cubre:
  1. Topología y lógica de bifurcación (enrutamiento de route_input_source).
  2. Evaluación de la rama CSV (bypass directo a END sin invocar nodos ni alterar el estado).
  3. Ejecución consecutiva sobre una lista de buckets de MinIO ("JAIO-24", "PUBMED-26", etc.),
     verificando la extracción y curación de metadatos o el manejo controlado de errores.
"""

import os
import socket
from pathlib import Path
from urllib.parse import urlparse
import pandas as pd
import pytest

from core.subgraphs.ingest import build_ingest_subgraph
from core.nodes.ingest_nodes import route_input_source
from core.state import State
from core.utils.config import config


# ---------------------------------------------------------------------------
# Configuración y constantes de prueba
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RAW_DATA_DIR = os.path.join(PROJECT_ROOT, "tests", "data", "ingest", "raw")
SAMPLE_CSV_PATH = os.path.join(RAW_DATA_DIR, "SearchResults.csv")

# Lista de buckets a evaluar de forma consecutiva
# Puede sobreescribirse mediante la variable de entorno TEST_MINIO_BUCKETS (ej: "BUCKET_A,BUCKET_B")
DEFAULT_BUCKETS = ["JAIO", "importacion"]
ENV_BUCKETS = os.getenv("TEST_MINIO_BUCKETS")
BUCKETS_TO_TEST = [b.strip() for b in ENV_BUCKETS.split(",") if b.strip()] if ENV_BUCKETS else DEFAULT_BUCKETS

# Endpoint MinIO para verificación de conectividad
MINIO_HOST = "localhost"
MINIO_PORT = 9003


# ---------------------------------------------------------------------------
# Helpers de verificación de conectividad
# ---------------------------------------------------------------------------
def _servicio_disponible(host: str, puerto: int, timeout: float = 2.0) -> bool:
    """Verifica si un socket TCP responde en host:puerto."""
    try:
        with socket.create_connection((host, puerto), timeout=timeout):
            return True
    except (socket.timeout, OSError):
        return False


# ===========================================================================
# 1. Topología y Ruteo de Bifurcación
# ===========================================================================
class TestIngestSubgraphTopology:
    """Verifica la compilación del subgrafo y la función de ruteo de bifurcación."""

    @pytest.mark.asyncio
    async def test_compilacion_y_nodos_presentes(self):
        """El subgrafo compila correctamente y registra los nodos esperados."""
        subgraph = await build_ingest_subgraph()

        assert subgraph is not None
        node_names = set(subgraph.nodes.keys())

        expected_nodes = {"PDFIngest", "CurateMetadata"}
        for node in expected_nodes:
            assert node in node_names, f"El nodo '{node}' debe estar registrado en IngestSubgraph."

    def test_route_input_source_bifurcacion(self):
        """Verifica que el enrutador bifurque adecuadamente entre 'csv' y 'pdf_minio'."""
        # 1. Rama CSV explícita
        assert route_input_source({"input_source_type": "csv"}) == "csv"

        # 2. Rama PDF MinIO
        assert route_input_source({"input_source_type": "pdf_minio"}) == "pdf_minio"

        # 3. Default cuando no se especifica input_source_type (asume csv)
        assert route_input_source({}) == "csv"


# ===========================================================================
# 2. Evaluación de la Bifurcación CSV (Paso directo a END)
# ===========================================================================
class TestIngestSubgraphCsvBifurcation:
    """
    Evalúa la ejecución del subgrafo en la rama CSV.
    Dado que cuando el contenido proviene de un CSV no se realiza procesamiento,
    se valida que el flujo vaya directo a END sin invocar PDFIngest ni CurateMetadata.
    """

    @pytest.mark.asyncio
    async def test_flujo_bifurcacion_csv_pasa_directo_a_end(self, tmp_path):
        """
        Al ingresar input_source_type='csv':
          - El subgrafo debe terminar inmediatamente en END.
          - No debe generar archivos temporales de PDFs ni invocar curación.
          - El estado de salida conserva inalterado el source_csv_path de entrada.
        """
        subgraph = await build_ingest_subgraph()

        state: State = {
            "workspace_dir": str(tmp_path),
            "input_source_type": "csv",
            "source_csv_path": SAMPLE_CSV_PATH,
            "source_name": "springer_raw_test",
        }

        final_state = await subgraph.ainvoke(state)

        # 1. El estado preserva la ruta de entrada
        assert final_state.get("source_csv_path") == SAMPLE_CSV_PATH

        # 2. No se debió ejecutar CurateMetadata (no debe existir curated_csv_path)
        assert "curated_csv_path" not in final_state

        # 3. No se debió crear ningún archivo en el workspace temporal
        workspace_files = list(tmp_path.iterdir())
        assert len(workspace_files) == 0, "La rama CSV no debe generar archivos en el workspace."


# ===========================================================================
# 3. Evaluación Consecutiva de Buckets de MinIO (Rama pdf_minio)
# ===========================================================================
class TestIngestSubgraphMinioBuckets:
    """
    Evalúa la rama 'pdf_minio' sobre un listado de buckets ejecutados consecutivamente.
    Ejercita el flujo completo: START -> PDFIngest -> CurateMetadata -> END.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bucket_name", BUCKETS_TO_TEST)
    async def test_ejecucion_consecutiva_bucket_minio(self, bucket_name: str, tmp_path):
        """
        Ejecuta el subgrafo de ingesta de forma consecutiva para cada bucket de la lista:
          1. PDFIngest: Lista y descarga PDFs desde MinIO, extrayendo metadatos a source_from_pdfs.csv.
          2. CurateMetadata: Aplica heurísticas y curación a los metadatos generados.
          3. Verifica que se generen los CSVs correspondientes o se capture el error ordenadamente.
        """
        # Verificar conectividad con MinIO
        if not _servicio_disponible(MINIO_HOST, MINIO_PORT):
            pytest.skip(f"MinIO no está accesible en {MINIO_HOST}:{MINIO_PORT}. Omitiendo test para bucket '{bucket_name}'.")

        # Crear workspace aislado para este bucket
        bucket_workspace = tmp_path / bucket_name
        bucket_workspace.mkdir(parents=True, exist_ok=True)

        state: State = {
            "workspace_dir": str(bucket_workspace),
            "input_source_type": "pdf_minio",
            "minio_bucket": bucket_name,
            "minio_prefix": "",
            "source_name": f"minio_{bucket_name.lower().replace('-', '_')}",
        }

        subgraph = await build_ingest_subgraph()
        final_state = await subgraph.ainvoke(state)

        # Rutas esperadas en el workspace del bucket
        expected_source_csv = bucket_workspace / "source_from_pdfs.csv"
        expected_curated_csv = bucket_workspace / "curated_from_pdfs.csv"

        # 1. Si el bucket existe y MinIO procesó objetos
        node_errors = final_state.get("node_errors", {})
        if "PDFIngest_errors" not in node_errors:
            # Debe haberse generado el CSV fuente desde PDFs
            assert expected_source_csv.is_file(), f"Se esperaba {expected_source_csv} tras PDFIngest."
            assert final_state.get("source_csv_path") == str(expected_source_csv)

            # Debe haberse generado el CSV curado tras CurateMetadata
            assert expected_curated_csv.is_file(), f"Se esperaba {expected_curated_csv} tras CurateMetadata."
            assert final_state.get("curated_csv_path") == str(expected_curated_csv)

            # Validar que el CSV curado tenga las columnas canónicas
            df_curated = pd.read_csv(str(expected_curated_csv))
            columnas_esperadas = {"id", "title", "author", "date", "type"}
            assert columnas_esperadas.issubset(set(df_curated.columns)), (
                f"El CSV curado debe contener las columnas {columnas_esperadas}. "
                f"Columnas encontradas: {list(df_curated.columns)}"
            )
        else:
            # 2. Si el bucket no existe en el servidor MinIO o falló la conexión
            # Verificamos que el subgrafo manejó la anomalía de forma controlada sin crashear
            assert "PDFIngest_errors" in node_errors
            assert expected_source_csv.is_file(), "Incluso ante error de listado se debe generar un CSV vacío de resguardo."
