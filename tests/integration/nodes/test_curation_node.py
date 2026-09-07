"""
Tests del nodo de curación de metadatos PDF (curate_metadata_node).

Soporta dos rutas de activación según lo especificado en el plan:

  1. Ruta CSV existente (TestCurationDesdeCsvExistente):
     Activa el nodo directamente con un CSV pre-existente, sin necesidad
     de MinIO ni Metadata Extractor. Usa el archivo /tmp/source_from_pdfs.csv
     generado por tests/integration/test_pdf_ingest_node.py.
     → No requiere servicios externos.

  2. Ruta desde extracción completa (TestCuracionDesdeExtraccion):
     Ejecuta el flujo completo PDFIngest → CurateMetadata.
     Requiere MinIO y Metadata Extractor levantados.
     → Se salta automáticamente si los servicios no están disponibles.

Los tests de la Capa de Correctores validan que las correcciones
programáticas se apliquen correctamente sin invocar al LLM.
"""

import asyncio
import csv
import os
import socket
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.nodes.curation_nodes import curate_metadata_node

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

CSV_REAL_PATH = "/tmp/source_from_pdfs.csv"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _servicio_disponible(host: str, puerto: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, puerto), timeout=timeout):
            return True
    except OSError:
        return False


def _escribir_csv_temp(registros: list[dict], campos: list[str]) -> str:
    """Escribe un CSV temporal y devuelve su ruta."""
    fd, path = tempfile.mkstemp(suffix=".csv", prefix="test_curation_")
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=campos, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(registros)
    return path


# ---------------------------------------------------------------------------
# Ruta 1: CSV existente (no requiere servicios externos)
# ---------------------------------------------------------------------------

class TestCurationDesdeCsvExistente:
    """
    Curación directa sobre el CSV generado por test_pdf_ingest_node.

    Mockea el agente LLM para que los tests sean deterministas y rápidos.
    Verifica que:
      - El CSV curado se escribe en curated_from_pdfs.csv (no sobreescribe source).
      - La Capa 1 detecta correctamente las anomalías del CSV real.
      - Los correctores programáticos actúan sobre las filas sospechosas.
      - Las estadísticas reflejan la curación aplicada.
    """

    @pytest.mark.skipif(
        not os.path.isfile(CSV_REAL_PATH),
        reason=f"CSV real no disponible en '{CSV_REAL_PATH}'. "
               "Ejecutar primero tests/integration/test_pdf_ingest_node.py.",
    )
    def test_curado_desde_csv_real(self):
        """
        Ejecuta curate_metadata_node sobre el CSV real de 10 PDFs.
        Mockea el agente para que los correctores programáticos sean el foco.
        """
        workspace_dir = tempfile.mkdtemp(prefix="test_curation_")

        state = {
            "workspace_dir": workspace_dir,
            "source_csv_path": CSV_REAL_PATH,
            "node_errors": {},
        }

        # Mockear el agente para que devuelva respuesta vacía
        # (verificamos solo el comportamiento de Capa 1 y 2a)
        with patch(
            "core.nodes.curation_nodes.build_metadata_curator_agent",
            new_callable=AsyncMock,
        ) as mock_build:
            mock_agente = AsyncMock()
            mock_agente.ainvoke = AsyncMock(return_value={
                "messages": [MagicMock(content="```json\n[]\n```")]
            })
            mock_build.return_value = mock_agente

            resultado = asyncio.run(curate_metadata_node(state))

        # 1. Verificar retorno del nodo
        assert isinstance(resultado, dict)
        assert "curated_csv_path" in resultado
        assert "curation_stats" in resultado
        # La nueva estrategia bifurca el resultado en dos archivos
        assert "pending_to_review_csv_path" in resultado

        # 2. El CSV curado debe escribirse en un archivo diferente al original
        curated_path = resultado["curated_csv_path"]
        assert curated_path != CSV_REAL_PATH, (
            "El CSV curado NO debe sobreescribir el CSV original."
        )
        assert "curated_from_pdfs.csv" in curated_path

        # 3. El CSV curado debe existir
        assert os.path.isfile(curated_path), f"El CSV curado no existe en '{curated_path}'"

        # 4. El CSV original debe mantenerse intacto
        assert os.path.isfile(CSV_REAL_PATH), "El CSV original fue eliminado o movido."

        # 5. Estadísticas deben ser coherentes
        stats = resultado["curation_stats"]
        assert stats["total"] == 10
        assert stats["limpias"] + stats["sospechosas_detectadas"] + stats["sin_datos"] == 10

        # 6. Bifurcación: la suma de filas curadas + pendientes == total de registros
        with open(curated_path, newline="", encoding="utf-8") as fh:
            filas_curadas = list(csv.DictReader(fh))

        pending_path = resultado["pending_to_review_csv_path"]
        filas_pendientes = []
        if pending_path and os.path.isfile(pending_path):
            with open(pending_path, newline="", encoding="utf-8") as fh:
                filas_pendientes = list(csv.DictReader(fh))

        assert len(filas_curadas) + len(filas_pendientes) == 10, (
            f"La suma de filas curadas ({len(filas_curadas)}) + pendientes "
            f"({len(filas_pendientes)}) no da el total esperado (10)."
        )

        # 7. Todas las filas del CSV curado NO deben tener curation_needed=True
        for fila in filas_curadas:
            assert fila.get("curation_needed", "False") not in ("True", "true", "1"), (
                f"Fila '{fila.get('id')}' en curated_csv tiene curation_needed=True "
                f"pero debería estar en pending_to_review."
            )

        print(f"\nEstadísticas de curación: {stats}")
        print(f"CSV curado ({len(filas_curadas)} filas): {curated_path}")
        print(f"CSV pendientes ({len(filas_pendientes)} filas): {pending_path}")

    @pytest.mark.skipif(
        not os.path.isfile(CSV_REAL_PATH),
        reason=f"CSV real no disponible en '{CSV_REAL_PATH}'.",
    )
    def test_citation_repetida_colapsada(self):
        """
        La fila 9 (citation con 30+ repeticiones de CONICET) debe quedar
        colapsada después de los correctores programáticos.
        """
        workspace_dir = tempfile.mkdtemp(prefix="test_curation_citation_")
        state = {
            "workspace_dir": workspace_dir,
            "source_csv_path": CSV_REAL_PATH,
            "node_errors": {},
        }

        with patch(
            "core.nodes.curation_nodes.build_metadata_curator_agent",
            new_callable=AsyncMock,
        ) as mock_build:
            mock_agente = AsyncMock()
            mock_agente.ainvoke = AsyncMock(return_value={
                "messages": [MagicMock(content="```json\n[]\n```")]
            })
            mock_build.return_value = mock_agente

            resultado = asyncio.run(curate_metadata_node(state))

        curated_path = resultado["curated_csv_path"]
        with open(curated_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            filas = {row["id"]: row for row in reader}

        fila_10 = filas.get("39-jaiio-ast-10.pdf-PDFA.pdf", {})
        citation_curada = fila_10.get("citation", "")

        # La citation original tenía 1564 chars; la curada debe ser más corta
        assert len(citation_curada) < 1000, (
            f"La citation de la fila 9 debería haberse colapsado, "
            f"pero tiene {len(citation_curada)} chars: {citation_curada[:100]}..."
        )
        print(f"\nCitation curada (fila 9): {citation_curada}")

    @pytest.mark.skipif(
        not os.path.isfile(CSV_REAL_PATH),
        reason=f"CSV real no disponible en '{CSV_REAL_PATH}'.",
    )
    def test_cid_artifacts_removidos(self):
        """
        Las filas con artefactos (cid:XX) en el abstract deben tener
        los artefactos removidos después de la curación.
        """
        workspace_dir = tempfile.mkdtemp(prefix="test_curation_cid_")
        state = {
            "workspace_dir": workspace_dir,
            "source_csv_path": CSV_REAL_PATH,
            "node_errors": {},
        }

        with patch(
            "core.nodes.curation_nodes.build_metadata_curator_agent",
            new_callable=AsyncMock,
        ) as mock_build:
            mock_agente = AsyncMock()
            mock_agente.ainvoke = AsyncMock(return_value={
                "messages": [MagicMock(content="```json\n[]\n```")]
            })
            mock_build.return_value = mock_agente

            resultado = asyncio.run(curate_metadata_node(state))

        curated_path = resultado["curated_csv_path"]
        with open(curated_path, newline="", encoding="utf-8") as fh:
            contenido = fh.read()

        assert "(cid:" not in contenido, (
            "El CSV curado no debería contener artefactos (cid:XX)."
        )


# ---------------------------------------------------------------------------
# Ruta 2: CSV sintético con casos controlados
# ---------------------------------------------------------------------------

class TestCurationDesdeCsvSintetico:
    """
    Tests sobre CSVs sintéticos para validar casos específicos de curación
    sin depender del CSV real del test de integración.
    """

    CAMPOS = ["id", "title", "author", "description", "date", "type", "citation"]

    def test_fila_limpia_no_se_envia_al_agente(self):
        """Una fila sin anomalías no debe invocar al agente LLM."""
        registros = [{
            "id": "limpio.pdf",
            "title": "Framework de segmentación de imágenes",
            "author": "Diego Comas|Gustavo Meschino",
            "description": "Texto normal sin ningún problema de formato.",
            "date": "2022",
            "type": "objeto de conferencia",
            "citation": "Universidad de Buenos Aires",
        }]

        csv_path = _escribir_csv_temp(registros, self.CAMPOS)
        workspace_dir = tempfile.mkdtemp(prefix="test_curation_sintetico_")

        state = {
            "workspace_dir": workspace_dir,
            "source_csv_path": csv_path,
        }

        with patch(
            "core.nodes.curation_nodes.build_metadata_curator_agent",
            new_callable=AsyncMock,
        ) as mock_build:
            asyncio.run(curate_metadata_node(state))
            # El agente NO debe haber sido construido para filas limpias
            mock_build.assert_not_called()

        os.unlink(csv_path)

    def test_csv_vacio_no_lanza_excepcion(self):
        """Un CSV vacío (solo header) debe manejarse sin errores y pending_to_review debe ser None."""
        csv_path = _escribir_csv_temp([], self.CAMPOS)
        workspace_dir = tempfile.mkdtemp(prefix="test_curation_vacio_")
        state = {"workspace_dir": workspace_dir, "source_csv_path": csv_path}

        resultado = asyncio.run(curate_metadata_node(state))

        assert "curated_csv_path" in resultado
        assert resultado["curation_stats"]["total"] == 0
        assert resultado.get("pending_to_review_csv_path") is None
        os.unlink(csv_path)

    def test_csv_fuente_invalido_retorna_error(self):
        """Si source_csv_path apunta a un archivo inexistente, debe retornar error."""
        state = {
            "workspace_dir": "/tmp",
            "source_csv_path": "/tmp/archivo_inexistente_12345.csv",
        }
        resultado = asyncio.run(curate_metadata_node(state))
        assert "error" in resultado.get("curation_stats", {})

    def test_sin_source_csv_path_lanza_value_error(self):
        """Si source_csv_path no está en el estado, debe lanzar ValueError."""
        state = {"workspace_dir": "/tmp"}
        with pytest.raises(ValueError, match="source_csv_path"):
            asyncio.run(curate_metadata_node(state))

    def test_curated_csv_path_no_sobreescribe_source(self):
        """El CSV curado debe escribirse en ruta diferente al source."""
        registros = [{
            "id": "test.pdf",
            "title": "E s t i m a r T e x t u r a s L o c a l e s",
            "type": "articulo",
            "citation": "texto",
        }]
        csv_path = _escribir_csv_temp(registros, self.CAMPOS)
        workspace_dir = tempfile.mkdtemp(prefix="test_no_sobreescribe_")

        state = {
            "workspace_dir": workspace_dir,
            "source_csv_path": csv_path,
        }

        with patch(
            "core.nodes.curation_nodes.build_metadata_curator_agent",
            new_callable=AsyncMock,
        ) as mock_build:
            mock_agente = AsyncMock()
            mock_agente.ainvoke = AsyncMock(return_value={
                "messages": [MagicMock(content="```json\n[]\n```")]
            })
            mock_build.return_value = mock_agente

            resultado = asyncio.run(curate_metadata_node(state))

        assert resultado["curated_csv_path"] != csv_path
        assert os.path.isfile(csv_path), "El CSV original fue eliminado."
        os.unlink(csv_path)


    def test_bifurcacion_filas_marcadas_van_a_pending(self):
        """
        Verifica que las filas que el agente marca con curation_needed=True
        terminan en pending_to_review_csv_path y NO en curated_csv_path.
        """
        # Una fila limpia y una sospechosa (agente falla en corregirla)
        registros = [
            {
                "id": "limpio.pdf",
                "title": "Framework de segmentación de imágenes",
                "author": "Diego Comas|Gustavo Meschino",
                "description": "Texto normal sin ningún problema de formato.",
                "date": "2022",
                "type": "objeto de conferencia",
                "citation": "Universidad de Buenos Aires",
            },
            {
                "id": "sospechoso.pdf",
                # CAMPO_VACIO en 'author': los correctores programáticos no pueden
                # inventar datos → pasa al agente; el mock devuelve [] → queda marcado
                "title": "Framework de Segmentacion de Imagenes",
                "author": "",
                "description": "Texto normal sin problemas de formato.",
                "date": "2023",
                "type": "articulo",
                "citation": "Fuente",
            },
        ]

        csv_path = _escribir_csv_temp(registros, self.CAMPOS)
        workspace_dir = tempfile.mkdtemp(prefix="test_bifurcacion_")

        state = {
            "workspace_dir": workspace_dir,
            "source_csv_path": csv_path,
        }

        # El agente devuelve lista vacía → no corrige nada → fila sospechosa queda marcada
        with patch(
            "core.nodes.curation_nodes.build_metadata_curator_agent",
            new_callable=AsyncMock,
        ) as mock_build:
            mock_agente = AsyncMock()
            mock_agente.ainvoke = AsyncMock(return_value={
                "messages": [MagicMock(content="```json\n[]\n```")]
            })
            mock_build.return_value = mock_agente

            resultado = asyncio.run(curate_metadata_node(state))

        # Verificar la bifurcación
        assert "curated_csv_path" in resultado
        assert "pending_to_review_csv_path" in resultado

        curated_path = resultado["curated_csv_path"]
        pending_path = resultado["pending_to_review_csv_path"]

        assert os.path.isfile(curated_path), "El CSV curado debe existir."
        assert pending_path is not None, "Debe haber un CSV de pendientes cuando hay filas marcadas."
        assert os.path.isfile(pending_path), f"El CSV pending no existe en '{pending_path}'."

        with open(curated_path, newline="", encoding="utf-8") as fh:
            filas_curadas = list(csv.DictReader(fh))
        with open(pending_path, newline="", encoding="utf-8") as fh:
            filas_pendientes = list(csv.DictReader(fh))

        # La fila limpia está en curated; la sospechosa en pending
        assert len(filas_curadas) == 1, f"Esperaba 1 fila curada, hubo {len(filas_curadas)}."
        assert len(filas_pendientes) == 1, f"Esperaba 1 fila pendiente, hubo {len(filas_pendientes)}."
        assert filas_curadas[0]["id"] == "limpio.pdf"
        assert filas_pendientes[0]["id"] == "sospechoso.pdf"
        assert filas_pendientes[0].get("curation_needed") in ("True", "true", "1", True), (
            "La fila en pending debe tener curation_needed=True."
        )

        # La suma total es consistente
        assert len(filas_curadas) + len(filas_pendientes) == len(registros)

        os.unlink(csv_path)


# ---------------------------------------------------------------------------
# Ruta 3: flujo completo extracción → curación (requiere servicios)
# ---------------------------------------------------------------------------

class TestCuracionDesdeExtraccion:
    """
    Test de integración que ejecuta el flujo completo:
    PDFIngest → CurateMetadata.

    Se salta si MinIO o el Metadata Extractor no están disponibles.
    """

    @pytest.fixture(scope="class")
    def servicios_activos(self):
        from core.utils.config import config

        if not _servicio_disponible("localhost", 9003):
            pytest.skip("MinIO no disponible en localhost:9003.")

        parsed = config.METADATA_EXTRACTOR_API_URL
        if parsed:
            from urllib.parse import urlparse
            parts = urlparse(parsed)
            host = parts.hostname or "localhost"
            port = parts.port or 80
            if not _servicio_disponible(host, port):
                pytest.skip(
                    f"Metadata Extractor no disponible en '{parsed}'."
                )

    def test_flujo_completo_extraccion_y_curacion(self, servicios_activos):
        """
        Ejecuta pdf_ingest_node seguido de curate_metadata_node y verifica
        que el CSV curado existe y contiene las correcciones esperadas.
        """
        import tempfile
        from core.nodes.ingest_nodes import pdf_ingest_node

        workspace_dir = tempfile.mkdtemp(prefix="test_extraccion_curacion_")
        state = {
            "workspace_dir": workspace_dir,
            "minio_bucket": "importacion",
            "minio_prefix": "",
            "node_errors": {},
        }

        # Paso 1: extracción de PDFs
        state_post_ingest = asyncio.run(pdf_ingest_node(state))
        state.update(state_post_ingest)

        assert "source_csv_path" in state
        assert os.path.isfile(state["source_csv_path"])

        # Paso 2: curación
        state_post_curation = asyncio.run(curate_metadata_node(state))
        state.update(state_post_curation)

        assert "curated_csv_path" in state
        assert os.path.isfile(state["curated_csv_path"])

        # El curado no debe sobreescribir el original
        assert state["curated_csv_path"] != state["source_csv_path"]

        print(f"\nCSV original: {state['source_csv_path']}")
        print(f"CSV curado:   {state['curated_csv_path']}")
        print(f"Estadísticas: {state.get('curation_stats', {})}")
