"""
Tests unitarios para los detectores heurísticos de anomalías en metadatos PDF.

No requiere servicios externos, LLM ni MinIO.
Verifica el comportamiento de cada detector de forma aislada y el
triaje completo con datos reales extraídos del CSV de prueba.
"""

import pytest

from core.utils.heuristic_detectors import (
    analizar_fila,
    calcular_estadisticas_lote,
    calcular_score_anomalia,
    detectar_artefactos_cid,
    detectar_chars_control,
    detectar_chars_dispersos,
    detectar_longitud_anomala,
    detectar_repeticion_ciclica,
    detectar_texto_pegado,
    triar_registros,
)


# ---------------------------------------------------------------------------
# Detectores individuales
# ---------------------------------------------------------------------------

class TestDetectarCharsDispersos:
    """Tests para el detector de caracteres separados por espacios."""

    def test_detecta_titulo_con_chars_dispersos(self):
        """El título de la fila 5 del CSV real debe detectarse."""
        titulo = "E s t i m * a A c g u i s á t n in M d a e ilin l g a C o d m e p T l e e x j i t d u a r d a s"
        assert detectar_chars_dispersos(titulo) is True

    def test_no_detecta_texto_normal(self):
        """Texto normal en español no debe activar el detector."""
        assert detectar_chars_dispersos("Framework de segmentación de imágenes") is False

    def test_no_detecta_texto_ingles_normal(self):
        assert detectar_chars_dispersos("A Comparative Study of Implementation Strategies") is False

    def test_no_detecta_texto_corto(self):
        """Textos muy cortos no tienen suficientes tokens para el análisis."""
        assert detectar_chars_dispersos("A B C") is False

    def test_detecta_texto_completamente_disperso(self):
        """Texto puramente disperso."""
        assert detectar_chars_dispersos("F r a m e w o r k d e s e g m e n t a c i ó n") is True


class TestDetectarArtefactosCid:
    """Tests para el detector de marcadores internos de PDF (cid:XX)."""

    def test_detecta_cid_en_abstract(self):
        texto = "Two di(cid:27)erent criterions are compared:besta(cid:30)neunbiasedfusionrule"
        assert detectar_artefactos_cid(texto) is True

    def test_detecta_cid_simple(self):
        assert detectar_artefactos_cid("texto con (cid:12) artefacto") is True

    def test_no_detecta_texto_limpio(self):
        assert detectar_artefactos_cid("texto sin artefactos normales") is False

    def test_no_detecta_parentesis_normales(self):
        """Paréntesis que no son artefactos CID no deben activar el detector."""
        assert detectar_artefactos_cid("(Universidad de Buenos Aires)") is False


class TestDetectarRepeticionCiclica:
    """Tests para el detector de subcadenas repetidas en loop."""

    def test_detecta_repeticion_citation_real(self):
        """La citation de la fila 9 con CONICET repetido 30+ veces."""
        citation = (
            "Facultad deIngeniería, Universidad de Buenos Aires, CONICET, Buenos Aires, Argentina "
            "– CONICET, Buenos Aires, Argentina – CONICET, Buenos Aires, Argentina "
            "– CONICET, Buenos Aires, Argentina – CONICET, Buenos Aires, Argentina "
            "– CONICET, Buenos Aires, Argentina – CONICET, Buenos Aires, Argentina"
        )
        assert detectar_repeticion_ciclica(citation) is True

    def test_no_detecta_texto_normal(self):
        assert detectar_repeticion_ciclica("Universidad de Buenos Aires, CONICET") is False

    def test_no_detecta_texto_corto(self):
        assert detectar_repeticion_ciclica("CONICET CONICET") is False

    def test_detecta_repeticion_sintetica(self):
        texto = "palabra larga repetida " * 4
        assert detectar_repeticion_ciclica(texto) is True


class TestDetectarTextoPegado:
    """Tests para el detector de palabras sin espacio."""

    def test_detecta_abstract_pegado(self):
        """Abstract con palabras fusionadas real del CSV."""
        abstract = (
            "EnelmarcodelfiltradoBayesiano,sepresentaunmodeloen elcualelprocesodemedición"
            "yelestadosiguientesoncondicionalmente dependientes,dadoel conjunto deobservaciones"
        )
        assert detectar_texto_pegado(abstract) is True

    def test_no_detecta_texto_normal(self):
        texto = "El procesamiento de imágenes y video para el estudio de fenómenos naturales"
        assert detectar_texto_pegado(texto) is False

    def test_detecta_titulo_completamente_pegado(self):
        assert detectar_texto_pegado("LaTransformadaDiscretadeKarhunenLoèveeslatransformadaoptima") is True


class TestDetectarLongitudAnomala:
    """Tests para el detector de longitud outlier."""

    def test_detecta_campo_extremadamente_largo(self):
        """El campo citation de 1564 chars vs media de ~50 debe ser anómalo."""
        assert detectar_longitud_anomala("x" * 1564, media=80.0, desviacion=40.0) is True

    def test_no_detecta_longitud_normal(self):
        assert detectar_longitud_anomala("Universidad de Buenos Aires", media=80.0, desviacion=40.0) is False

    def test_umbral_absoluto_sin_variabilidad(self):
        """Con desviación < 1, usa umbral absoluto de 500 chars."""
        assert detectar_longitud_anomala("x" * 600, media=50.0, desviacion=0.0) is True
        assert detectar_longitud_anomala("x" * 100, media=50.0, desviacion=0.0) is False


# ---------------------------------------------------------------------------
# Análisis de fila completo
# ---------------------------------------------------------------------------

class TestAnalizarFila:
    """Tests del analizador completo de una fila."""

    def test_fila_limpia_sin_anomalias(self):
        fila = {
            "id": "test-01.pdf",
            "title": "Framework de segmentación de imágenes",
            "author": "Diego Comas|Gustavo Meschino",
            "description": "Una descripción normal sin problemas de formato.",
            "date": "2022",
            "type": "objeto de conferencia",
        }
        stats = calcular_estadisticas_lote([fila], ["title", "author", "description", "citation"])
        anomalias = analizar_fila(fila, stats)
        assert anomalias == {}

    def test_fila_con_cid_y_texto_pegado(self):
        fila = {
            "id": "test-02.pdf",
            "title": "Data Fusion BAUE",
            "description": "Two di(cid:27)erent criterions:besta(cid:30)neunbiasedfusionrule",
            "type": "articulo",
        }
        stats = calcular_estadisticas_lote([fila], ["description"])
        anomalias = analizar_fila(fila, stats)
        assert "description" in anomalias
        assert "ARTEFACTO_CID" in anomalias["description"]

    def test_fila_con_titulo_disperso(self):
        fila = {
            "id": "test-06.pdf",
            "title": "E s t i m * a A c g u i s á t n in M d a e ilin l g a C o d m e p T l e",
            "type": "objeto de conferencia",
        }
        stats = calcular_estadisticas_lote([fila], ["title"])
        anomalias = analizar_fila(fila, stats)
        assert "title" in anomalias
        assert "CHARS_DISPERSOS" in anomalias["title"]

    def test_fila_con_campo_obligatorio_vacio(self):
        fila = {
            "id": "test-05.pdf",
            "title": "",  # campo obligatorio vacío
            "type": "objeto de conferencia",
        }
        stats = calcular_estadisticas_lote([fila], ["title"])
        anomalias = analizar_fila(fila, stats)
        assert "title" in anomalias
        assert "CAMPO_VACIO" in anomalias["title"]


# ---------------------------------------------------------------------------
# Score de anomalía
# ---------------------------------------------------------------------------

class TestCalcularScoreAnomalia:
    """Tests para la función de scoring de anomalías."""

    def test_score_cero_sin_anomalias(self):
        assert calcular_score_anomalia({}) == 0.0

    def test_score_maximo_con_chars_dispersos(self):
        score = calcular_score_anomalia({"title": ["CHARS_DISPERSOS"]})
        assert score >= 0.8  # CHARS_DISPERSOS tiene peso 0.9

    def test_score_normalizado_en_rango(self):
        anomalias = {
            "title": ["CHARS_DISPERSOS"],
            "citation": ["REPETICION_CICLICA", "LONGITUD_ANOMALA"],
            "description": ["ARTEFACTO_CID", "TEXTO_PEGADO"],
        }
        score = calcular_score_anomalia(anomalias)
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# Triaje completo de lote
# ---------------------------------------------------------------------------

class TestTriarRegistros:
    """Tests de triaje completo sobre datos reales del CSV de prueba."""

    # Datos representativos de las 10 filas del CSV real
    REGISTROS_REALES = [
        {
            "id": "39-jaiio-ast-01.pdf-PDFA.pdf",
            "title": "Framework de segmentación y análisis de imágenes mediante reconocimiento de texturas",
            "author": "Diego S. Comas|Gustavo J. Meschino|Virginia L. Ballarin",
            "description": "Cuando se combinan técnicas de extracción de características y reconocimiento de patrones...",
            "type": "objeto de conferencia",
        },
        {
            "id": "39-jaiio-ast-06.pdf-PDFA.pdf",
            "title": "E s t i m * a A c g u i s á t n in M d a e ilin l g a C o d m e p T l e e x j i t d u a r d a s",
            "type": "objeto de conferencia",
            "subject": "Ciencias físicas",
        },
        {
            "id": "39-jaiio-ast-10.pdf-PDFA.pdf",
            "title": "Métodos secuenciales de Monte Carlo",
            "type": "objeto de conferencia",
            "citation": (
                "Facultad deIngeniería, Universidad de Buenos Aires, CONICET, Buenos Aires, Argentina "
                "– CONICET, Buenos Aires, Argentina – CONICET, Buenos Aires, Argentina "
                "– CONICET, Buenos Aires, Argentina – CONICET, Buenos Aires, Argentina "
                "– CONICET, Buenos Aires, Argentina – CONICET, Buenos Aires, Argentina"
            ),
        },
        {
            "id": "39-jaiio-ast-03.pdf-PDFA.pdf",
            "title": "Data Fusion BAUE",
            "description": "Two di(cid:27)erent criterions:besta(cid:30)neunbiasedfusionrule(BAUE)",
            "type": "articulo",
        },
    ]

    def test_triaje_clasifica_correctamente(self):
        """Las filas con anomalías claras deben quedar en sospechosas."""
        limpias, sospechosas, sin_datos = triar_registros(self.REGISTROS_REALES, umbral=0.3)

        # La fila 0 (normal) debe estar limpia
        ids_limpias = {r["id"] for r in limpias}
        assert "39-jaiio-ast-01.pdf-PDFA.pdf" in ids_limpias

        # Las filas con anomalías deben estar en sospechosas
        ids_sospechosas = {r["id"] for r in sospechosas}
        assert "39-jaiio-ast-06.pdf-PDFA.pdf" in ids_sospechosas  # chars dispersos
        assert "39-jaiio-ast-10.pdf-PDFA.pdf" in ids_sospechosas  # repetición cíclica

    def test_sospechosas_incluyen_metadatos_de_curacion(self):
        """Las filas sospechosas deben tener _curation con anomalías y score."""
        _, sospechosas, _ = triar_registros(self.REGISTROS_REALES, umbral=0.3)
        for fila in sospechosas:
            assert "_curation" in fila
            assert "anomalias" in fila["_curation"]
            assert "score" in fila["_curation"]
            assert fila["_curation"]["score"] >= 0.3

    def test_triaje_lote_vacio(self):
        """Un lote vacío no debe lanzar excepciones."""
        limpias, sospechosas, sin_datos = triar_registros([])
        assert limpias == []
        assert sospechosas == []
        assert sin_datos == []

    def test_umbral_alto_clasifica_todo_como_limpio(self):
        """Con umbral 1.0, casi nada debería ser sospechoso."""
        limpias, sospechosas, _ = triar_registros(self.REGISTROS_REALES, umbral=1.0)
        assert len(sospechosas) == 0
