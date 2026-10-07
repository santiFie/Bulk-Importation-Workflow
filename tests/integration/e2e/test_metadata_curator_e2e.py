"""
Tests End-to-End (E2E) para el MetadataCuratorAgent con herramientas reales.

Prueba la integración completa del agente contra el bucket MinIO 'importacion-jaio'
(10 PDFs del 39 JAIIO - Simposio Argentino de Tecnología) y la tool real de OCR
(Tesseract + PyMuPDF).

Requisitos de ejecución:
  - MinIO disponible en localhost:9003 con el bucket 'importacion-jaio'.
  - Metadata Extractor disponible en el puerto configurado.
  - Al menos una API key configurada (GROQ_API_KEY, NVIDIA_API_KEY u OPEN_ROUTER_API_KEY).
"""

from __future__ import annotations

import asyncio
import csv
import os
import socket
import tempfile
from typing import Any
import pytest

from core.agent.metadata_curator_agent import (
    construir_mensaje_curacion,
    parsear_respuesta_agente,
)
from core.nodes.curation_nodes import _calcular_anomalias_restantes
from core.utils.config import config
from core.utils.text_fixers import aplicar_correctores_programaticos


_CACHE_CSV_JAIO = "/tmp/source_from_pdfs_jaio.csv"
_REGISTROS_JAIO: list[dict] | None = None


def _servicio_disponible(host: str, puerto: int, timeout: float = 2.0) -> bool:
    """Verifica si un servicio TCP está escuchando."""
    try:
        with socket.create_connection((host, puerto), timeout=timeout):
            return True
    except OSError:
        return False


def _tiene_api_key_llm() -> bool:
    """Verifica si hay al menos una API key de LLM configurada."""
    return bool(
        config.GROQ_API_KEY
        or config.NVIDIA_API_KEY
        or config.OPEN_ROUTER_API_KEY
    )


def _hay_metadata_extractor() -> bool:
    """Verifica disponibilidad del Metadata Extractor."""
    if not config.METADATA_EXTRACTOR_API_URL:
        return False
    from urllib.parse import urlparse
    parts = urlparse(config.METADATA_EXTRACTOR_API_URL)
    host = parts.hostname or "localhost"
    port = parts.port or 80
    return _servicio_disponible(host, port)


def _cargar_csv_jaio() -> list[dict]:
    """
    Carga o extrae el CSV de los 10 PDFs del bucket 'importacion-jaio'.
    """
    global _REGISTROS_JAIO

    if _REGISTROS_JAIO is not None:
        return _REGISTROS_JAIO

    if os.path.isfile(_CACHE_CSV_JAIO):
        with open(_CACHE_CSV_JAIO, newline="", encoding="utf-8") as fh:
            _REGISTROS_JAIO = list(csv.DictReader(fh))
        return _REGISTROS_JAIO

    if not _servicio_disponible("localhost", 9003):
        pytest.skip("MinIO no disponible en localhost:9003.")

    if not _hay_metadata_extractor():
        pytest.skip("Metadata Extractor no disponible en la URL configurada.")

    from core.nodes.ingest_nodes import pdf_ingest_node
    import shutil

    workspace_dir = tempfile.mkdtemp(prefix="test_curator_jaio_")
    state = {
        "workspace_dir": workspace_dir,
        "minio_bucket": "importacion-jaio",
        "minio_prefix": "",
        "node_errors": {},
    }

    result = asyncio.run(pdf_ingest_node(state))
    csv_path = result.get("source_csv_path", "")

    if not csv_path or not os.path.isfile(csv_path):
        pytest.fail(f"pdf_ingest_node no generó el CSV esperado. Resultado: {result}")

    shutil.copy(csv_path, _CACHE_CSV_JAIO)

    with open(csv_path, newline="", encoding="utf-8") as fh:
        _REGISTROS_JAIO = list(csv.DictReader(fh))

    return _REGISTROS_JAIO


@pytest.fixture(scope="module")
def agente_curador_fixture():
    """Construye el agente curador real para las pruebas E2E."""
    if not _tiene_api_key_llm():
        pytest.skip("No hay API key de LLM disponible para pruebas E2E.")
    from core.agent.metadata_curator_agent import build_metadata_curator_agent
    return asyncio.run(build_metadata_curator_agent())



@pytest.mark.integration
class TestMetadataCuratorE2E:
    """
    Tests End-to-End con Tools e Infraestructura Real.
    Valida la descarga desde MinIO y ejecución real de OCR sobre PDFs corruptos.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def registros_jaio(cls) -> list[dict]:
        return _cargar_csv_jaio()

    def test_agente_ejecuta_ocr_real_en_pdf_corrupto(self, registros_jaio, agente_curador_fixture):
        """
        Prueba la integración completa de la tool re_extract_with_ocr.
        El agente procesa ast-06, ejecuta OCR físico y produce la corrección.
        """
        fila = next((r for r in registros_jaio if r.get("id") == "39-jaiio-ast-06.pdf-PDFA.pdf"), None)
        if fila is None:
            pytest.fail("Registro ast-06 no encontrado en el CSV del bucket.")

        anomalias = fila.get("_curation", {}).get("anomalias", {})
        fila_post_fix = aplicar_correctores_programaticos(fila, anomalias)
        restantes = _calcular_anomalias_restantes(fila_post_fix, anomalias)

        if not restantes:
            restantes = {"title": ["CHARS_DISPERSOS"]}

        fila_input = {k: v for k, v in fila_post_fix.items() if v}
        fila_input["_curation"] = {
            "anomalias": restantes,
            "score": fila.get("_curation", {}).get("score", 1.0),
        }

        mensaje = construir_mensaje_curacion([fila_input])
        state = {"messages": [mensaje]}

        final_state = asyncio.run(agente_curador_fixture.ainvoke(state, config={"recursion_limit": 15}))
        ultima_respuesta = final_state["messages"][-1]
        correcciones = parsear_respuesta_agente(ultima_respuesta.content)

        correccion = next((c for c in correcciones if c.get("id") == fila.get("id")), None)

        assert correccion is not None, "El agente E2E no devolvió corrección."

        titulo_agente = correccion.get("title", "")
        marcado = correccion.get("title_curation_needed", False)

        assert titulo_agente != fila_post_fix.get("title") or marcado, (
            "El agente no corrigió ni marcó el título tras intentar usar tools reales."
        )
        assert "correction_notes" in correccion, (
            f"El agente debe incluir 'correction_notes' en cada corrección. "
            f"Corrección recibida: {correccion}"
        )
        assert correccion["correction_notes"].strip(), (
            "correction_notes no debe estar vacío"
        )
