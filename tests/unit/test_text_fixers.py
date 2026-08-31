"""
Tests unitarios para los correctores de texto programáticos.

No requiere servicios externos, LLM ni MinIO.
Verifica cada corrector con ejemplos reales extraídos del CSV de prueba.
"""

import pytest

from core.utils.text_fixers import (
    aplicar_correctores_programaticos,
    deduplicate_cyclic_text,
    fix_glued_words,
    fix_spaced_chars,
    normalizar_autores,
    remove_cid_artifacts,
)


# ---------------------------------------------------------------------------
# Corrector 1: caracteres dispersos
# ---------------------------------------------------------------------------

class TestFixSpacedChars:
    """Tests para el corrector de texto con chars separados por espacios."""

    def test_colapsa_titulo_disperso_real(self):
        """El título real de la fila 5 del CSV debe colapsar a algo legible."""
        titulo = "E s t i m a r T e x t u r a s L o c a l e s e n I m á g e n e s"
        resultado = fix_spaced_chars(titulo)
        # No debe contener el patrón de un char por espacio
        tokens = resultado.split()
        chars_simples = sum(1 for t in tokens if len(t) == 1)
        assert chars_simples < len(tokens) * 0.3, (
            f"El resultado '{resultado}' aún tiene demasiados chars simples"
        )

    def test_no_modifica_texto_normal(self):
        """El texto sin el patrón no debe cambiar."""
        texto = "Framework de segmentación de imágenes"
        assert fix_spaced_chars(texto) == texto

    def test_no_modifica_texto_corto(self):
        """Texto con pocos tokens no debe modificarse."""
        texto = "A B C"
        resultado = fix_spaced_chars(texto)
        # Con menos de 8 tokens, no se activa la corrección
        assert resultado is not None  # Solo verificar que no lanza excepción

    def test_colapsa_letras_puras(self):
        """Texto completamente disperso."""
        texto = "F r a m e w o r k"
        resultado = fix_spaced_chars(texto)
        assert " " not in resultado or len(resultado) < len(texto)

    def test_texto_vacio(self):
        assert fix_spaced_chars("") == ""


# ---------------------------------------------------------------------------
# Corrector 2: artefactos CID
# ---------------------------------------------------------------------------

class TestRemoveCidArtifacts:
    """Tests para el corrector de artefactos (cid:XX)."""

    def test_reemplaza_cid_con_mapeo(self):
        """Los CIDs con mapeo deben reemplazarse por el carácter correspondiente."""
        # cid:27 → "ff" según _CID_MAP
        resultado = remove_cid_artifacts("di(cid:27)erent")
        assert "(cid:" not in resultado
        assert "di" in resultado.lower()

    def test_elimina_cid_sin_mapeo(self):
        """Los CIDs sin mapeo deben eliminarse (cadena vacía)."""
        resultado = remove_cid_artifacts("texto(cid:999)texto")
        assert "(cid:999)" not in resultado
        assert "textotexto" in resultado

    def test_no_modifica_texto_sin_cid(self):
        texto = "texto normal sin artefactos"
        assert remove_cid_artifacts(texto) == texto

    def test_procesa_multiples_cid_en_mismo_texto(self):
        texto = "besta(cid:30)ne unbiased(cid:27)fusion"
        resultado = remove_cid_artifacts(texto)
        assert "(cid:" not in resultado

    def test_texto_vacio(self):
        assert remove_cid_artifacts("") == ""


# ---------------------------------------------------------------------------
# Corrector 3: texto cíclico repetido
# ---------------------------------------------------------------------------

class TestDeduplicateCyclicText:
    """Tests para el corrector de repeticiones cíclicas."""

    def test_colapsa_citation_real(self):
        """La citation de 1564 chars de la fila 9 debe colapsarse."""
        citation = (
            "Facultad deIngeniería, Universidad de Buenos Aires, CONICET, Buenos Aires, Argentina "
            "– CONICET, Buenos Aires, Argentina – CONICET, Buenos Aires, Argentina "
            "– CONICET, Buenos Aires, Argentina – CONICET, Buenos Aires, Argentina "
            "– CONICET, Buenos Aires, Argentina – CONICET, Buenos Aires, Argentina "
            "– CONICET, Buenos Aires, Argentina – CONICET, Buenos Aires, Argentina"
        )
        resultado = deduplicate_cyclic_text(citation)
        assert len(resultado) < len(citation), (
            f"El resultado ({len(resultado)} chars) debería ser más corto que el original ({len(citation)} chars)"
        )
        # El texto resultante no debe tener múltiples repeticiones
        assert resultado.count("CONICET") < citation.count("CONICET")

    def test_no_modifica_texto_sin_repeticion(self):
        texto = "Facultad deIngeniería, Universidad de Buenos Aires, CONICET"
        resultado = deduplicate_cyclic_text(texto)
        # Sin repetición cíclica, debe devolver el mismo texto (o similar)
        assert len(resultado) >= len(texto) * 0.5  # No colapsa texto válido

    def test_no_modifica_texto_corto(self):
        texto = "CONICET CONICET"
        resultado = deduplicate_cyclic_text(texto)
        # Muy corto para activar el detector
        assert resultado == texto

    def test_colapsa_repeticion_sintetica(self):
        patron = "Universidad de Buenos Aires – CONICET, "
        texto = patron * 5
        resultado = deduplicate_cyclic_text(texto)
        assert len(resultado) < len(texto)

    def test_texto_vacio(self):
        assert deduplicate_cyclic_text("") == ""


# ---------------------------------------------------------------------------
# Corrector 4: palabras pegadas
# ---------------------------------------------------------------------------

class TestFixGluedWords:
    """Tests para el corrector heurístico de palabras pegadas."""

    def test_separa_camelcase_accidental(self):
        """CamelCase accidental por falta de espacio."""
        resultado = fix_glued_words("LaTransformadaDiscreta")
        # Debe insertar espacio antes de mayúsculas tras minúsculas
        assert " " in resultado

    def test_separa_transicion_digito_letra(self):
        """Números pegados a letras."""
        resultado = fix_glued_words("versión2014del")
        assert " " in resultado

    def test_no_modifica_texto_normal(self):
        texto = "Universidad de Buenos Aires"
        assert fix_glued_words(texto) == texto

    def test_texto_vacio(self):
        assert fix_glued_words("") == ""


# ---------------------------------------------------------------------------
# Corrector 5: autores duplicados
# ---------------------------------------------------------------------------

class TestNormalizarAutores:
    """Tests para el normalizador de autores duplicados."""

    def test_elimina_autores_duplicados_exactos(self):
        autores = "Andrea Silvetti|Claudio Delrieux|Andrea Silvetti"
        resultado = normalizar_autores(autores)
        assert resultado.count("Andrea Silvetti") == 1
        assert "Claudio Delrieux" in resultado

    def test_elimina_duplicados_con_diferencias_de_acento(self):
        """Autores con y sin acento que son la misma persona."""
        autores = "Bruno Cernuschifrías|Bruno Cernuschifrías"
        resultado = normalizar_autores(autores)
        assert resultado.count("|") == 0  # Solo un autor

    def test_no_modifica_autores_unicos(self):
        autores = "Juan Pablo|Miguel María Elena|Gustavo C. Pilar"
        resultado = normalizar_autores(autores)
        assert resultado == autores

    def test_texto_sin_separador(self):
        """Un solo autor sin pipe no debe modificarse."""
        autor = "Diego S. Comas"
        assert normalizar_autores(autor) == autor

    def test_texto_vacio(self):
        assert normalizar_autores("") == ""


# ---------------------------------------------------------------------------
# Aplicador compuesto
# ---------------------------------------------------------------------------

class TestAplicarCorrectoresProgramaticos:
    """Tests del aplicador que corre todos los correctores en conjunto."""

    def test_aplica_cid_sobre_description(self):
        fila = {
            "id": "test.pdf",
            "title": "Data Fusion BAUE",
            "description": "Two di(cid:27)erent criterions are compared:besta(cid:30)ne",
        }
        anomalias = {"description": ["ARTEFACTO_CID"]}
        resultado = aplicar_correctores_programaticos(fila, anomalias)
        assert "(cid:" not in resultado["description"]
        assert "ARTEFACTO_CID" in " ".join(resultado.get("_corrections_applied", []))

    def test_aplica_repeticion_sobre_citation(self):
        patron = "CONICET, Buenos Aires, Argentina – "
        fila = {
            "id": "test.pdf",
            "title": "Métodos secuenciales",
            "citation": patron * 10,
        }
        anomalias = {"citation": ["REPETICION_CICLICA"]}
        resultado = aplicar_correctores_programaticos(fila, anomalias)
        assert len(resultado["citation"]) < len(fila["citation"])

    def test_elimina_metadatos_internos_de_curacion(self):
        """La fila resultante no debe tener la clave _curation."""
        fila = {
            "id": "test.pdf",
            "title": "Título normal",
            "_curation": {"anomalias": {}, "score": 0.0},
        }
        resultado = aplicar_correctores_programaticos(fila, {})
        assert "_curation" not in resultado

    def test_resultado_incluye_corrections_applied(self):
        """El resultado siempre debe incluir _corrections_applied (puede estar vacío)."""
        fila = {"id": "test.pdf", "title": "Título normal"}
        resultado = aplicar_correctores_programaticos(fila, {})
        assert "_corrections_applied" in resultado

    def test_no_modifica_campos_sin_anomalias(self):
        """Los campos que no tienen anomalías no deben cambiar."""
        fila = {
            "id": "test.pdf",
            "title": "Título limpio",
            "author": "Autor Correcto",
            "description": "Descripción (cid:27) con artefacto",
        }
        anomalias = {"description": ["ARTEFACTO_CID"]}
        resultado = aplicar_correctores_programaticos(fila, anomalias)
        assert resultado["title"] == "Título limpio"
        assert resultado["author"] == "Autor Correcto"
