"""
Test de integración REAL para el nodo pdf_ingest_node (core/nodes/ingest_nodes.py).

A diferencia del resto de la suite, este test NO usa mocks: ejercita los
servicios externos reales de los que depende la ingesta desde MinIO:

  - Metadata Extractor MCP corriendo en config.METADATA_EXTRACTOR_MCP_URL
    (definido en .env, ej. http://localhost:9604/mcp).
  - MinIO accesible en localhost:9003, con el bucket 'importacion'.
  - Docker disponible para levantar el MCP server aistor (transporte stdio).
  - GROQ_API_KEY configurada, requerida por el agente extractor de metadatos.

Configuración del lote bajo prueba:
  - minio_bucket:   'importacion'
  - workspace_dir:  '/tmp' (directorio persistente; el CSV generado en
    /tmp/source_from_pdfs.csv NO se elimina al finalizar el test).

Si los servicios externos no están disponibles, el test se marca como
skip en lugar de fallar, para no ensuciar la suite en entornos sin infra.
"""

import asyncio
import csv
import os
import socket
from urllib.parse import urlparse

import pytest

from core.nodes.ingest_nodes import pdf_ingest_node
from core.utils.config import config

# ---------------------------------------------------------------------------
# Configuración del test
# ---------------------------------------------------------------------------

BUCKET = "importacion"
WORKSPACE_DIR = "/tmp"
EXPECTED_CSV_PATH = os.path.join(WORKSPACE_DIR, "source_from_pdfs.csv")

# Endpoint MinIO expuesto por el contenedor del MCP aistor (hardcodeado en
# los argumentos docker del nodo); se verifica solo a nivel de conectividad.
MINIO_ENDPOINT_HOST = "localhost"
MINIO_ENDPOINT_PORT = 9003


# ---------------------------------------------------------------------------
# Helpers de verificación de conectividad
# ---------------------------------------------------------------------------

def _servicio_disponible(url_o_host: str, puerto_default: int = 80, timeout: float = 3.0) -> bool:
    """
    Verifica conectividad TCP contra una URL (http://host:puerto) o un
    par host/puerto explícito. Devuelve True si el handshake TCP funciona.
    """
    if "://" in url_o_host:
        partes = urlparse(url_o_host)
        host = partes.hostname or "localhost"
        puerto = partes.port or (443 if partes.scheme == "https" else puerto_default)
    else:
        host, puerto = url_o_host, puerto_default

    try:
        with socket.create_connection((host, puerto), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def servicios_externos_activos():
    """
    Garantiza que los servicios reales estén levantados antes de ejecutar
    el flujo completo; si no lo están, salta el test (no es un fallo).
    """
    if not _servicio_disponible(config.METADATA_EXTRACTOR_API_URL):
        pytest.skip(
            f"Metadata Extractor API no disponible en '{config.METADATA_EXTRACTOR_API_URL}'; "
            "levantá el servicio para correr este test de integración."
        )
    if not _servicio_disponible(MINIO_ENDPOINT_HOST, MINIO_ENDPOINT_PORT):
        pytest.skip(
            f"MinIO no disponible en '{MINIO_ENDPOINT_HOST}:{MINIO_ENDPOINT_PORT}'; "
            "levantá el servicio para correr este test de integración."
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPdfIngestNodeIntegracionReal:
    """Flujo completo contra Orchestrator MCP + MinIO + LLM reales."""

    def test_flujo_completo_bucket_importacion(self, servicios_externos_activos):
        """
        Ejecuta pdf_ingest_node contra los servicios reales y valida:

          1. El nodo retorna 'source_csv_path' apuntando al CSV esperado.
          2. El archivo existe en /tmp y persiste tras el test (no hay cleanup).
          3. El CSV es legible y, si el bucket tenía PDFs, cada fila registra
             un 'source_file' terminando en '.pdf'.

        Nota: la cantidad de filas depende del contenido real del bucket
        'importacion' en el momento de la ejecución; por eso sólo se
        assertiona estructura, no contenido exacto.
        """
        state = {
            "workspace_dir": WORKSPACE_DIR,
            "minio_bucket": BUCKET,
            "minio_prefix": "",
            "node_errors": {},
        }

        resultado = asyncio.run(pdf_ingest_node(state))

        # 1. Retorno del nodo
        assert isinstance(resultado, dict), "El nodo debe retornar un dict de actualización de estado."
        assert "source_csv_path" in resultado, "El nodo debe retornar 'source_csv_path'."
        assert resultado["source_csv_path"] == EXPECTED_CSV_PATH

        print(resultado["source_csv_path"])

        # 2. Artefacto persistente en /tmp (NO se elimina)
        assert os.path.isfile(EXPECTED_CSV_PATH), (
            f"El CSV '{EXPECTED_CSV_PATH}' debe existir y permanecer en /tmp."
        )

        # 3. Estructura del CSV generado
        with open(EXPECTED_CSV_PATH, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            assert reader.fieldnames, "El CSV debe tener al menos una fila de header."
            filas = list(reader)

        for fila in filas:
            source_file = (fila.get("source_file") or "").strip()
            if source_file:
                assert source_file.lower().endswith(".pdf"), (
                    f"'source_file' debería referenciar un PDF, got: '{source_file}'"
                )

    def test_sin_minio_bucket_lanza_value_error(self):
        """
        Validación pura de Python (no requiere servicios): si falta
        'minio_bucket' en el estado, el nodo debe fallar rápido con
        ValueError antes de intentar conectar a cualquier servicio.
        """
        state = {
            "workspace_dir": WORKSPACE_DIR,
            "minio_bucket": "",
            "minio_prefix": "",
        }

        with pytest.raises(ValueError, match="minio_bucket"):
            asyncio.run(pdf_ingest_node(state))
