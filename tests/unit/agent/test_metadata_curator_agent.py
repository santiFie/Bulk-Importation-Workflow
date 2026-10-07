"""
Tests unitarios de helpers y parsing del MetadataCuratorAgent.

Verifica de forma aislada, rápida y sin dependencias externas:
  1. Serialización del prompt en formato Markdown con bloque JSON estructurado.
  2. Inclusión y exclusión adecuada de campos vacíos y metadatos de curación.
  3. Parsing tolerante de respuestas emitidas por el LLM (Markdown, JSON directo,
     objetos únicos, arrays vacíos o cadenas no parseables).
"""

from __future__ import annotations

import json
import re
import pytest

from core.agent.metadata_curator_agent import (
    construir_mensaje_curacion,
    parsear_respuesta_agente,
)


@pytest.mark.unit
class TestHelpersMensajeCuracion:
    """
    Tests unitarios de construir_mensaje_curacion() y parsear_respuesta_agente().
    No requieren red, MinIO ni LLM.
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
