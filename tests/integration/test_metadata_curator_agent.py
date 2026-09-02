"""
Tests de integración del MetadataCuratorAgent.

Evalúa el agente con casos de prueba reales del bucket MinIO 'importacion-jaio'
(10 PDFs del 39 JAIIO - Simposio Argentino de Tecnología). Cubre:

  1. Helpers del agente (sin red ni LLM).
  2. Correcciones programáticas por tipo de anomalía (Capa 2a).
  3. Evaluación del agente LLM real sobre anomalías irresolubles (Capa 2b).
     Usa fallback automático: Groq → Nvidia → OpenRouter.

Flujo de curación:
  Capa 1 (heurísticas) → Capa 2a (correctores programáticos) → Capa 2b (agente LLM)

Casos de anomalías reales en el bucket 'importacion-jaio':
  - ast-04: TEXTO_PEGADO grave en description (LaTransformadaDiscretadeKarhunen...)
  - ast-05: TEXTO_PEGADO en description (ofdifferent types...)
  - ast-06: CHARS_DISPERSOS en title + CAMPO_VACIO en date y description
  - ast-09: CAMPO_VACIO en date (tiene ISSN 1850-2806 para enriquecer)
  - ast-10: TEXTO_PEGADO grave en description + CAMPO_VACIO en date

Prerequisitos:
  - MinIO disponible en localhost:9003 con el bucket 'importacion-jaio'.
  - Metadata Extractor disponible (para extraer CSV del bucket).
  - Al menos una API key configurada: GROQ_API_KEY, NVIDIA_API_KEY o OPEN_ROUTER_API_KEY.
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import re
import socket
import tempfile
from typing import Any

import pytest

from core.agent.metadata_curator_agent import (
    build_metadata_curator_agent,
    construir_mensaje_curacion,
    parsear_respuesta_agente,
)
from core.nodes.curation_nodes import _calcular_anomalias_restantes
from core.utils.heuristic_detectors import (
    analizar_fila,
    calcular_estadisticas_lote,
    triar_registros,
)
from core.utils.text_fixers import aplicar_correctores_programaticos


# ---------------------------------------------------------------------------
# Configuración y helpers de test
# ---------------------------------------------------------------------------

# Ruta al CSV cacheado del bucket
_CACHE_CSV_JAIO = "/tmp/source_from_pdfs_jaio.csv"

# Datos reales del CSV del bucket (cargados una sola vez por la sesión)
_REGISTROS_JAIO: list[dict] | None = None


def _servicio_disponible(host: str, puerto: int, timeout: float = 2.0) -> bool:
    """Verifica si un servicio TCP está disponible."""
    try:
        with socket.create_connection((host, puerto), timeout=timeout):
            return True
    except OSError:
        return False


def _tiene_api_key_llm() -> bool:
    """Verifica si hay al menos una API key de LLM configurada."""
    from core.utils.config import config
    return bool(
        config.GROQ_API_KEY
        or config.NVIDIA_API_KEY
        or config.OPEN_ROUTER_API_KEY
    )


def _hay_metadata_extractor() -> bool:
    """Verifica disponibilidad del Metadata Extractor."""
    from core.utils.config import config
    if not config.METADATA_EXTRACTOR_API_URL:
        return False
    from urllib.parse import urlparse
    parts = urlparse(config.METADATA_EXTRACTOR_API_URL)
    host = parts.hostname or "localhost"
    port = parts.port or 80
    return _servicio_disponible(host, port)


def _cargar_csv_jaio() -> list[dict]:
    """
    Carga (o genera) el CSV de los 10 PDFs del bucket 'importacion-jaio'.

    Intenta leer el CSV desde caché en /tmp. Si no existe, extrae los PDFs
    del bucket MinIO usando pdf_ingest_node.

    Returns:
        Lista de dicts con los metadatos extraídos (10 filas).
    """
    global _REGISTROS_JAIO

    if _REGISTROS_JAIO is not None:
        return _REGISTROS_JAIO

    # Intentar leer desde caché
    if os.path.isfile(_CACHE_CSV_JAIO):
        with open(_CACHE_CSV_JAIO, newline="", encoding="utf-8") as fh:
            _REGISTROS_JAIO = list(csv.DictReader(fh))
        return _REGISTROS_JAIO

    # Si no hay caché, extraer desde MinIO
    if not _servicio_disponible("localhost", 9003):
        pytest.skip("MinIO no disponible en localhost:9003.")

    if not _hay_metadata_extractor():
        pytest.skip(
            "Metadata Extractor no disponible. "
            "Levantarlo o generar el CSV manualmente con test_pdf_ingest_node.py."
        )

    from core.nodes.ingest_nodes import pdf_ingest_node

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
        pytest.fail(
            f"pdf_ingest_node no generó el CSV. Resultado: {result}"
        )

    # Copiar a caché para ejecuciones futuras
    import shutil
    shutil.copy(csv_path, _CACHE_CSV_JAIO)

    with open(csv_path, newline="", encoding="utf-8") as fh:
        _REGISTROS_JAIO = list(csv.DictReader(fh))

    return _REGISTROS_JAIO


def _construir_modelo_con_fallback():
    """
    Construye el modelo LLM del curador con fallback automático.

    Orden de prioridad: Groq → Nvidia → OpenRouter.
    Lanza un skip de pytest si no hay ninguna API key disponible.

    Returns:
        Instancia del modelo LLM lista para usar con bind_tools().
    """
    from core.utils.config import config
    from core.utils.get_local_model import FallbackLLM

    try:
        return FallbackLLM(
            groq_model=config.METADATA_CURATOR_MODEL,
            openrouter_model=config.METADATA_CURATOR_MODEL,
        ).resolve()
    except RuntimeError:
        pytest.skip(
            "No hay API key de LLM disponible. "
            "Configurar GROQ_API_KEY, NVIDIA_API_KEY o OPEN_ROUTER_API_KEY."
        )


# ---------------------------------------------------------------------------
# Clase 1: Tests unitarios de los helpers del agente (sin red ni LLM) (Nivel 1)
# ---------------------------------------------------------------------------

class TestHelpersMensajeCuracion:
    """
    Tests unitarios de construir_mensaje_curacion() y parsear_respuesta_agente().
    No requieren red ni LLM.
    """

    FILA_CON_ANOMALIAS: dict = {
        "id": "39-jaiio-ast-06.pdf-PDFA.pdf",
        "title": "E s t i m * a A c g u i s á t n",
        "author": "Juan Pablo|Miguel María Elena",
        "description": "",
        "date": "",
        "type": "objeto de conferencia",
        "_curation": {
            "anomalias": {
                "title": ["CHARS_DISPERSOS"],
                "date": ["CAMPO_VACIO"],
                "description": ["CAMPO_VACIO"],
            },
            "score": 0.9,
        },
        "_corrections_applied": ["title:CHARS_DISPERSOS"],
    }

    def test_construir_mensaje_serializa_json_valido(self):
        """construir_mensaje_curacion() debe generar un HumanMessage con JSON válido."""
        msg = construir_mensaje_curacion([self.FILA_CON_ANOMALIAS])

        assert msg.content
        assert "```json" in msg.content

        # Extraer y parsear el JSON del bloque markdown
        match = re.search(r"```json\s*(\[.*?\])\s*```", msg.content, re.DOTALL)
        assert match, "No se encontró el bloque ```json``` en el mensaje"

        filas = json.loads(match.group(1))
        assert isinstance(filas, list)
        assert len(filas) == 1

    def test_construir_mensaje_incluye_campos_curation(self):
        """El mensaje debe incluir _curation y _corrections_applied."""
        msg = construir_mensaje_curacion([self.FILA_CON_ANOMALIAS])

        match = re.search(r"```json\s*(\[.*?\])\s*```", msg.content, re.DOTALL)
        filas = json.loads(match.group(1))
        fila = filas[0]

        assert "_curation" in fila, "Debe incluir _curation"
        assert "_corrections_applied" in fila, "Debe incluir _corrections_applied"
        assert fila["_curation"]["anomalias"]["title"] == ["CHARS_DISPERSOS"]

    def test_construir_mensaje_excluye_campos_vacios(self):
        """Los campos con valor vacío no deben aparecer (excepto id, _curation)."""
        msg = construir_mensaje_curacion([self.FILA_CON_ANOMALIAS])

        match = re.search(r"```json\s*(\[.*?\])\s*```", msg.content, re.DOTALL)
        filas = json.loads(match.group(1))
        fila = filas[0]

        # isbn y doi están vacíos y no deben aparecer
        assert "isbn" not in fila
        assert "doi" not in fila
        assert "id" in fila  # id siempre debe aparecer

    def test_construir_mensaje_multiples_filas(self):
        """Debe manejar múltiples filas correctamente."""
        fila2 = dict(self.FILA_CON_ANOMALIAS)
        fila2["id"] = "39-jaiio-ast-10.pdf-PDFA.pdf"
        msg = construir_mensaje_curacion([self.FILA_CON_ANOMALIAS, fila2])

        assert "2 filas" in msg.content

    def test_parsear_respuesta_bloque_json(self):
        """parsear_respuesta_agente() debe extraer JSON de un bloque ```json```."""
        respuesta = (
            "Aquí están las correcciones:\n"
            "```json\n"
            '[{"id": "ast-06.pdf", "title": "Estimación Adquisición", '
            '"correction_notes": "Título reconstruido"}]\n'
            "```"
        )
        resultado = parsear_respuesta_agente(respuesta)

        assert isinstance(resultado, list)
        assert len(resultado) == 1
        assert resultado[0]["id"] == "ast-06.pdf"
        assert resultado[0]["title"] == "Estimación Adquisición"

    def test_parsear_respuesta_json_directo(self):
        """parsear_respuesta_agente() debe manejar JSON directo sin bloque markdown."""
        respuesta = '[{"id": "ast-09.pdf", "correction_notes": "date marcado para revisión"}]'
        resultado = parsear_respuesta_agente(respuesta)

        assert isinstance(resultado, list)
        assert resultado[0]["id"] == "ast-09.pdf"

    def test_parsear_respuesta_json_objeto_unico(self):
        """Debe envolver un objeto JSON único en una lista."""
        respuesta = '{"id": "ast-10.pdf", "title": "Métodos de Monte Carlo"}'
        resultado = parsear_respuesta_agente(respuesta)

        assert isinstance(resultado, list)
        assert len(resultado) == 1

    def test_parsear_respuesta_invalida_retorna_lista_vacia(self):
        """Una respuesta que no contiene JSON válido debe devolver lista vacía."""
        resultado = parsear_respuesta_agente("No pude procesar estos registros.")
        assert resultado == []

    def test_parsear_respuesta_json_vacio(self):
        """Lista vacía en la respuesta del agente es válida."""
        resultado = parsear_respuesta_agente("```json\n[]\n```")
        assert resultado == []


# ---------------------------------------------------------------------------
# Clase 2: Validación de correcciones por tipo de anomalía (datos reales) (Nivel 1)
# ---------------------------------------------------------------------------

class TestCorreccionesEspecificasPorTipo:
    """
    Valida que cada tipo de anomalía se procesa correctamente por los
    correctores programáticos (Capa 2a).

    Utiliza datos sintéticos para garantizar velocidad y aislamiento (Nivel 1),
    sin dependencia de MinIO ni red.
    """

    def test_chars_dispersos_se_aplica_fix_programatico(self):
        """fix_spaced_chars() debe colapsar el texto con espacios intermedios."""
        fila = {
            "id": "test1.pdf",
            "title": "E s t i m a c i o n d e t e x t u r a s",
            "type": "articulo",
            "_curation": {"anomalias": {"title": ["CHARS_DISPERSOS"]}}
        }
        fila_corregida = aplicar_correctores_programaticos(fila, fila["_curation"]["anomalias"])
        
        assert fila_corregida["title"] != "E s t i m a c i o n d e t e x t u r a s"
        assert len(fila_corregida["title"].split()) < 10
        assert "title:CHARS_DISPERSOS" in fila_corregida.get("_corrections_applied", [])

    def test_texto_pegado_en_description_aplica_fix_glued_words(self):
        """fix_glued_words() se aplica sobre description (campo de texto libre)."""
        fila = {
            "id": "test2.pdf",
            "description": "LaTransformadaDiscretadeKarhunenLoèveesla",
            "_curation": {"anomalias": {"description": ["TEXTO_PEGADO"]}}
        }
        fila_corregida = aplicar_correctores_programaticos(fila, fila["_curation"]["anomalias"])
        
        assert "La Transformada" in fila_corregida["description"] or len(fila_corregida["description"].split()) > 1
        assert "description:TEXTO_PEGADO" in fila_corregida.get("_corrections_applied", [])

    def test_texto_pegado_en_title_no_aplica_fix_glued_words(self):
        """fix_glued_words() NO se aplica a campos de título (solo desc/citation)."""
        fila = {
            "id": "test3.pdf",
            "title": "LaTransformadaDiscretadeKarhunenLoève",
            "_curation": {"anomalias": {"title": ["TEXTO_PEGADO"]}}
        }
        fila_corregida = aplicar_correctores_programaticos(fila, fila["_curation"]["anomalias"])
        
        assert "title:TEXTO_PEGADO" not in fila_corregida.get("_corrections_applied", [])

    def test_campo_vacio_obligatorio_persiste_tras_correctores(self):
        """Un CAMPO_VACIO no puede resolverse programáticamente → debe quedar restante."""
        fila = {
            "id": "test4.pdf",
            "title": "",
            "_curation": {"anomalias": {"title": ["CAMPO_VACIO"]}, "score": 0.6}
        }
        fila_corregida = aplicar_correctores_programaticos(fila, fila["_curation"]["anomalias"])
        restantes = _calcular_anomalias_restantes(fila_corregida, fila["_curation"]["anomalias"])

        assert "title" in restantes
        assert "CAMPO_VACIO" in restantes["title"]

    def test_autores_duplicados_se_normalizan(self):
        """normalizar_autores() se aplica siempre sobre el campo author."""
        fila = {
            "id": "test5.pdf",
            "author": "Andrea Silvetti|Claudio Delrieux|Andrea Silvetti",
        }
        fila_corregida = aplicar_correctores_programaticos(fila, {})
        assert fila_corregida["author"] == "Andrea Silvetti|Claudio Delrieux"
        assert "author:DUPLICADOS" in fila_corregida.get("_corrections_applied", [])

    def test_artefacto_cid_removido_programaticamente(self):
        """remove_cid_artifacts() reemplaza (cid:XX) por su equivalente o lo elimina."""
        fila = {
            "id": "test6.pdf",
            "description": "di(cid:27)erent criterions",
            "_curation": {"anomalias": {"description": ["ARTEFACTO_CID"]}}
        }
        fila_corregida = aplicar_correctores_programaticos(fila, fila["_curation"]["anomalias"])
        assert "(cid:" not in fila_corregida["description"]
        assert "description:ARTEFACTO_CID" in fila_corregida.get("_corrections_applied", [])

    def test_repeticion_ciclica_colapsada_programaticamente(self):
        """deduplicate_cyclic_text() colapsa texto repetido."""
        texto_repetido = "CONICET, Buenos Aires - CONICET, Buenos Aires - CONICET, Buenos Aires - "
        fila = {
            "id": "test7.pdf",
        "citation": texto_repetido,
            "_curation": {"anomalias": {"citation": ["REPETICION_CICLICA"]}}
        }
        fila_corregida = aplicar_correctores_programaticos(fila, fila["_curation"]["anomalias"])
        assert len(fila_corregida["citation"]) < len(texto_repetido)
        assert "citation:REPETICION_CICLICA" in fila_corregida.get("_corrections_applied", [])


# ---------------------------------------------------------------------------
# Clase 3: Tests del agente LLM real (Nivel 2 y Nivel 3)
# ---------------------------------------------------------------------------

import json
from pathlib import Path
from unittest.mock import patch


def _cargar_dataset_evaluacion() -> list[dict]:
    """Carga el dataset de evaluación estático."""
    path = Path(__file__).parent.parent / "data" / "curation_dataset.json"
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def agente_curador_fixture():
    """Construye el agente curador con el modelo LLM disponible."""
    if not _tiene_api_key_llm():
        pytest.skip("No hay API key de LLM disponible.")
    from core.agent.metadata_curator_agent import _construir_modelo_curador
    return asyncio.run(_construir_modelo_curador())


@pytest.mark.parametrize(
    "caso", _cargar_dataset_evaluacion(), ids=lambda c: c["case_name"]
)
class TestAgenteCurador_DatasetEval:
    """
    Nivel 2: Evaluación LLM sobre Dataset Estático.
    Valida el razonamiento del modelo y el enrutamiento a tools.
    Las tools se mockean para garantizar determinismo y velocidad.
    """

    def _invocar_agente(self, agente, fila_preparada) -> list[dict]:
        import asyncio
        from core.agent.metadata_curator_agent import construir_mensaje_curacion, parsear_respuesta_agente
        
        mensaje = construir_mensaje_curacion([fila_preparada])
        state = {"messages": [mensaje]}
        final_state = asyncio.run(agente.ainvoke(state, config={"recursion_limit": 15}))
        
        ultima_respuesta = final_state["messages"][-1]
        return parsear_respuesta_agente(ultima_respuesta.content)

    def test_evaluacion_caso(self, caso: dict, agente_curador_fixture):
        """Ejecuta un caso del dataset inyectando mock tools y verificando expected_output."""
        fila_input = caso["input"]
        expected_output = caso["expected_output"]
        mock_tools = caso.get("mock_tools", {})

        # Preparar mocks
        ocr_mock = mock_tools.get("re_extract_with_ocr")
        enricher_mock = mock_tools.get("validate_with_enrichers")

        import contextlib
        with patch.object(
            __import__("core.agent.metadata_curator_agent", fromlist=["re_extract_with_ocr"]).re_extract_with_ocr, 
            "func", 
            side_effect=lambda **kwargs: ocr_mock # Reemplaza la tool re_extract_with_ocr con el mock
        ) if ocr_mock else contextlib.nullcontext(), \
             patch.object(
            __import__("core.agent.metadata_curator_agent", fromlist=["validate_with_enrichers"]).validate_with_enrichers, 
            "func", 
            side_effect=lambda **kwargs: enricher_mock # Reemplaza la tool validate_with_enrichers con el mock
        ) if enricher_mock else contextlib.nullcontext():

            # Invocar agente
            correcciones = self._invocar_agente(agente_curador_fixture, fila_input)

            # Verificar que el agente devolvió correcciones
            # Si esperamos un curation_needed, el agente DEBE devolver una correccion para ese id
            correccion = next((c for c in correcciones if c.get("id") == fila_input.get("id", "dummy-ilegible.pdf")), None)
            
            if correccion is None:
                # Si el LLM no devuelve nada, LangGraph lo dejará intacto. Esto es un fallo si esperábamos que arregle algo.
                pytest.fail(f"El agente no devolvió ninguna corrección para el ID {fila_input.get('id')}.")

            # Verificar expected output
            for key, expected_value in expected_output.items():
                actual_value = correccion.get(key)
                assert actual_value == expected_value, (
                    f"Fallo en {key}: esperado '{expected_value}', obtenido '{actual_value}'.\n"
                    f"Respuesta completa: {correccion}"
                )


class TestAgenteCurador_IntegracionE2E:
    """
    Nivel 3: Tests End-to-End con Tools Reales.
    Valida que el agente integrado puede conectarse a MinIO, descargar PDFs
    y ejecutar OCR exitosamente sobre archivos físicos.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def registros_jaio(cls) -> list[dict]:
        """Carga los registros del bucket (o los genera desde MinIO)."""
        return _cargar_csv_jaio()

    def test_agente_ejecuta_ocr_real_en_pdf_corrupto(self, registros_jaio, agente_curador_fixture):
        """
        Prueba la integración completa de la tool re_extract_with_ocr.
        El agente debe procesar ast-06, decidir usar OCR, y la tool debe
        bajar el PDF real desde MinIO (si no está cacheado) y extraer texto.
        """
        fila = next((r for r in registros_jaio if r.get("id") == "39-jaiio-ast-06.pdf-PDFA.pdf"), None)
        if fila is None:
            pytest.fail("Registro ast-06 no encontrado en el CSV del bucket.")

        # Preparar la fila como lo hace curate_metadata_node
        from core.nodes.curation_nodes import _calcular_anomalias_restantes
        from core.utils.text_fixers import aplicar_correctores_programaticos
        import asyncio
        
        anomalias = fila.get("_curation", {}).get("anomalias", {})
        fila_post_fix = aplicar_correctores_programaticos(fila, anomalias)
        restantes = _calcular_anomalias_restantes(fila_post_fix, anomalias)

        if not restantes:
            # Forzamos para probar el flujo de tools si las heurísticas fueron muy buenas
            restantes = {"title": ["CHARS_DISPERSOS"]}

        fila_input = {k: v for k, v in fila_post_fix.items() if v}
        fila_input["_curation"] = {"anomalias": restantes, "score": fila.get("_curation", {}).get("score", 1.0)}

        # Invocamos al agente SIN mocks. Usará las tools reales.
        from core.agent.metadata_curator_agent import construir_mensaje_curacion, parsear_respuesta_agente
        mensaje = construir_mensaje_curacion([fila_input])
        state = {"messages": [mensaje]}
        
        final_state = asyncio.run(agente_curador_fixture.ainvoke(state, config={"recursion_limit": 15}))
        ultima_respuesta = final_state["messages"][-1]
        correcciones = parsear_respuesta_agente(ultima_respuesta.content)
        
        correccion = next((c for c in correcciones if c.get("id") == fila.get("id")), None)
        
        assert correccion is not None, "El agente no devolvió corrección."
        
        titulo_agente = correccion.get("title", "")
        marcado = correccion.get("title_curation_needed", False)

        # Confirmar que hizo algo (OCR exitoso o al menos falló pero marcó curation_needed)
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
