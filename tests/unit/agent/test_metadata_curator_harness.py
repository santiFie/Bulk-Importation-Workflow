"""
Tests unitarios de arnés para el MetadataCuratorAgent con tools simuladas (Mocks).

Evalúa el flujo determinista del grafo del agente (invocación, enrutamiento a tools
simuladas y consolidación de correcciones) usando el dataset de prueba
`tests/data/ingest/curation/curation_dataset.json`.

No consume servicios de red externos para OCR ni APIs bibliográficas externas,
garantizando velocidad y determinismo en suites unitarias.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.agent.metadata_curator_agent import (
    construir_mensaje_curacion,
    parsear_respuesta_agente,
)
from core.utils.config import config


def _cargar_dataset_evaluacion() -> list[dict]:
    """Carga el dataset de casos de prueba para el arnés de curación."""
    path = Path(__file__).parent.parent.parent / "data" / "ingest" / "curation" / "curation_dataset.json"
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _tiene_api_key_llm() -> bool:
    """Verifica si hay al menos una API key de LLM configurada para el arnés."""
    return bool(
        config.GROQ_API_KEY
        or config.NVIDIA_API_KEY
        or config.OPEN_ROUTER_API_KEY
    )


@pytest.fixture(scope="module")
def agente_curador_fixture():
    """Construye el agente curador con el modelo LLM configurado."""
    if not _tiene_api_key_llm():
        pytest.skip("No hay API key de LLM disponible para instanciar el arnés.")
    from core.agent.metadata_curator_agent import build_metadata_curator_agent
    return asyncio.run(build_metadata_curator_agent())



@pytest.mark.unit
@pytest.mark.eval
@pytest.mark.parametrize(
    "caso", _cargar_dataset_evaluacion(), ids=lambda c: c["case_name"]
)
class TestMetadataCuratorHarnessMocked:
    """
    Test de arnés unitario: Valida el razonamiento y enrutamiento del agente hacia tools.
    Las tools se mockean con side_effect para garantizar determinismo e independencia de red.
    """

    def _invocar_agente(self, agente, fila_preparada: dict) -> list[dict]:
        import time
        mensaje = construir_mensaje_curacion([fila_preparada])
        state = {"messages": [mensaje]}
        
        # Reintentar hasta 3 veces ante errores transitorios de red/API
        for intento in range(3):
            try:
                final_state = asyncio.run(agente.ainvoke(state, config={"recursion_limit": 15}))
                ultima_respuesta = final_state["messages"][-1]
                return parsear_respuesta_agente(ultima_respuesta.content)
            except Exception as e:
                if intento < 2 and ("Connection error" in str(e) or "429" in str(e)):
                    time.sleep(2)
                    continue
                raise


    def test_harness_ejecucion_con_mock_tools(self, caso: dict, agente_curador_fixture):
        """Ejecuta un caso inyectando mock tools y validando que el agente consuma el resultado simulado."""
        fila_input = caso["input"]
        expected_output = caso["expected_output"]
        mock_tools = caso.get("mock_tools", {})

        ocr_mock = mock_tools.get("re_extract_with_ocr")
        enricher_mock = mock_tools.get("validate_with_enrichers")

        with patch.object(
            __import__("core.agent.metadata_curator_agent", fromlist=["re_extract_with_ocr"]).re_extract_with_ocr,
            "func",
            side_effect=lambda **kwargs: ocr_mock,
        ) if ocr_mock else contextlib.nullcontext(), \
             patch.object(
            __import__("core.agent.metadata_curator_agent", fromlist=["validate_with_enrichers"]).validate_with_enrichers,
            "func",
            side_effect=lambda **kwargs: enricher_mock,
        ) if enricher_mock else contextlib.nullcontext():

            correcciones = self._invocar_agente(agente_curador_fixture, fila_input)

            correccion = next(
                (c for c in correcciones if c.get("id") == fila_input.get("id", "dummy-ilegible.pdf")),
                None,
            )

            if correccion is None:
                pytest.fail(f"El arnés del agente no devolvió ninguna corrección para el ID {fila_input.get('id')}.")

            for key, expected_value in expected_output.items():
                actual_value = correccion.get(key)
                assert actual_value == expected_value, (
                    f"Fallo en {key}: esperado '{expected_value}', obtenido '{actual_value}'.\n"
                    f"Respuesta completa: {correccion}"
                )
