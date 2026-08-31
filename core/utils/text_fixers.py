"""
Correctores de texto programáticos para metadatos extraídos de PDFs.

Módulo de las tools de corrección automática (Capa 2, sub-capa programática).
Todas las funciones son puras y deterministas: no consumen tokens LLM.

Se aplican ANTES de invocar al agente curador para reducir el trabajo
del LLM al mínimo. El agente solo interviene si los correctores no
logran un resultado satisfactorio.

Correctores disponibles:
    - fix_spaced_chars:        E s t i m a r → Estimar
    - remove_cid_artifacts:    (cid:27) → '' (o Unicode correspondiente)
    - deduplicate_cyclic_text: colapsa repeticiones cíclicas
    - fix_glued_words:         palabraspegadas → palabras pegadas (heurístico)
    - normalizar_autores:      elimina duplicados en listas de autores
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mapa parcial de códigos CID → caracteres Unicode comunes en PDFs académicos
# ---------------------------------------------------------------------------

_CID_MAP: dict[int, str] = {
    # Ligaturas tipográficas frecuentes en papers
    11: "ff",
    12: "fi",
    13: "fl",
    14: "ffi",
    15: "ffl",
    # Caracteres especiales
    23: "-",   # guión em
    27: "ff",  # variante de ligatura
    28: "fi",
    29: "fl",
    30: "ffi",
    31: "ffl",
    # Espacios tipográficos
    32: " ",
}

_RE_CID = re.compile(r"\(cid:(\d+)\)")
_RE_ESPACIOS_MULTIPLES = re.compile(r" {2,}")
_RE_CICLO = re.compile(r"(.{15,}?)\1{2,}", re.DOTALL)

# Separador de autores (pipe) para detectar repetición
_SEP_AUTOR = "|"


# ---------------------------------------------------------------------------
# Corrector 1: caracteres dispersos
# ---------------------------------------------------------------------------

def fix_spaced_chars(texto: str) -> str:
    """
    Colapsa texto con caracteres separados por espacios individuales.

    Detecta el patrón `X Y Z ...` (tokens de longitud 1 intercalados con
    espacios) y lo une eliminando los espacios intermedios. Respeta los
    espacios reales entre palabras al detectar transiciones de token-de-1
    a token-de-más-de-1.

    Ejemplo:
        "E s t i m a r  T e x t u r a s" → "Estimar Texturas"
        "Framework de segmentación" → sin cambios

    Args:
        texto: Texto a corregir.

    Returns:
        Texto corregido. Si no se detecta el patrón, devuelve el texto original.
    """
    if not texto:
        return texto

    tokens = texto.split(" ")
    if not tokens:
        return texto

    resultado: list[str] = []
    buffer_disperso: list[str] = []

    for token in tokens:
        if len(token) <= 1:
            buffer_disperso.append(token)
        else:
            if buffer_disperso:
                # Verificar si el buffer tiene suficientes chars dispersos
                chars = [c for c in buffer_disperso if c]
                if len(chars) >= 3:
                    resultado.append("".join(chars))
                else:
                    resultado.extend(buffer_disperso)
                buffer_disperso = []
            resultado.append(token)

    # Vaciar buffer al final
    if buffer_disperso:
        chars = [c for c in buffer_disperso if c]
        if chars:
            resultado.append("".join(chars))

    corregido = " ".join(resultado).strip()
    corregido = _RE_ESPACIOS_MULTIPLES.sub(" ", corregido)

    if corregido != texto:
        logger.debug(
            "[TextFixer] fix_spaced_chars: '%s...' → '%s...'",
            texto[:40], corregido[:40],
        )
    return corregido


# ---------------------------------------------------------------------------
# Corrector 2: artefactos CID
# ---------------------------------------------------------------------------

def remove_cid_artifacts(texto: str) -> str:
    """
    Reemplaza marcadores internos de PDF `(cid:XX)` por su equivalente
    Unicode según el mapa `_CID_MAP`, o los elimina si no hay mapeo.

    Ejemplo:
        "di(cid:27)erent" → "different"
        "besta(cid:30)ne" → "bestane" (fallback: eliminar)

    Args:
        texto: Texto con posibles artefactos CID.

    Returns:
        Texto con artefactos removidos o reemplazados.
    """
    if not texto or "(cid:" not in texto:
        return texto

    def reemplazar(match: re.Match) -> str:
        codigo = int(match.group(1))
        return _CID_MAP.get(codigo, "")  # vacío si no hay mapeo

    corregido = _RE_CID.sub(reemplazar, texto)
    corregido = _RE_ESPACIOS_MULTIPLES.sub(" ", corregido).strip()

    if corregido != texto:
        logger.debug(
            "[TextFixer] remove_cid_artifacts: removidos artefactos CID en '%s...'",
            texto[:50],
        )
    return corregido


# ---------------------------------------------------------------------------
# Corrector 3: texto repetido en ciclo
# ---------------------------------------------------------------------------

def deduplicate_cyclic_text(texto: str) -> str:
    """
    Detecta y colapsa subcadenas repetidas en ciclo dentro de un campo.

    Estrategia conservadora: solo colapsa si la repetición es clara
    (subcadena ≥ 15 chars repetida ≥ 3 veces). Preserva la primera
    ocurrencia completa y descarta las duplicaciones.

    Ejemplo:
        "Facultad UBA – CONICET – CONICET – CONICET – CONICET"
        → "Facultad UBA – CONICET"

    Args:
        texto: Texto a desduplicar.

    Returns:
        Texto con repeticiones colapsadas. Si no se detectan, sin cambios.
    """
    if not texto or len(texto) < 30:
        return texto

    match = _RE_CICLO.search(texto)
    if not match:
        return texto

    # Tomar solo la primera ocurrencia del patrón completo
    inicio = match.start()
    primera_ocurrencia = match.group(1)

    # El texto resultante: todo lo anterior al ciclo + la primera ocurrencia
    corregido = texto[:inicio] + primera_ocurrencia
    corregido = corregido.strip().rstrip("–,; ")

    logger.debug(
        "[TextFixer] deduplicate_cyclic_text: colapsado texto cíclico (%d → %d chars)",
        len(texto), len(corregido),
    )
    return corregido


# ---------------------------------------------------------------------------
# Corrector 4: palabras pegadas (heurístico liviano)
# ---------------------------------------------------------------------------

# Prefijos y artículos comunes en español e inglés para detectar fronteras
_PREFIJOS_COMUNES = {
    "de", "del", "la", "las", "los", "el", "en", "y", "a", "con",
    "para", "por", "un", "una", "the", "of", "and", "in", "a", "to",
    "que", "se", "is", "are", "was", "were", "be", "been",
}

_RE_CAMEL_LIKE = re.compile(r"(?<=[a-záéíóúñ])(?=[A-ZÁÉÍÓÚÑ])")
_RE_NUMERO_LETRA = re.compile(r"(?<=\d)(?=[A-Za-záéíóúñ])|(?<=[A-Za-záéíóúñ])(?=\d)")


def fix_glued_words(texto: str) -> str:
    """
    Intenta re-espaciar palabras pegadas usando heurísticas ligeras.

    Heurísticas aplicadas (en orden, conservadoras):
    1. Insertar espacio antes de mayúscula tras minúscula (CamelCase accidental).
    2. Insertar espacio en transiciones dígito↔letra.
    3. Normalizar espacios múltiples resultantes.

    Esta función es intencionalmnete conservadora: no intenta segmentación
    lingüística completa (que requeriría un diccionario completo o LLM).
    Para casos graves, el agente curador se encargará.

    Ejemplo:
        "LaTransformadaDiscretadeKarhunen" → "La Transformada Discretade Karhunen"
        (resultado parcial; el LLM completa lo que queda)

    Args:
        texto: Texto con posibles palabras pegadas.

    Returns:
        Texto con separaciones básicas insertadas.
    """
    if not texto:
        return texto

    # Paso 1: CamelCase accidental
    corregido = _RE_CAMEL_LIKE.sub(" ", texto)
    # Paso 2: transición dígito ↔ letra
    corregido = _RE_NUMERO_LETRA.sub(" ", corregido)
    # Paso 3: normalizar espacios
    corregido = _RE_ESPACIOS_MULTIPLES.sub(" ", corregido).strip()

    if corregido != texto:
        logger.debug(
            "[TextFixer] fix_glued_words: '%s...' → '%s...'",
            texto[:40], corregido[:40],
        )
    return corregido


# ---------------------------------------------------------------------------
# Corrector 5: autores duplicados
# ---------------------------------------------------------------------------

def normalizar_autores(texto: str, sep: str = "|") -> str:
    """
    Elimina autores duplicados en cadenas de autores separadas por `sep`.

    Preserva el orden de aparición. La comparación se hace normalizando
    Unicode (NFKD) y eliminando espacios extra antes de comparar.

    Ejemplo:
        "Andrea Silvetti|Claudio Delrieux|Andrea Silvetti"
        → "Andrea Silvetti|Claudio Delrieux"

    Args:
        texto: Cadena de autores separada por `sep`.
        sep:   Separador de autores (default: "|").

    Returns:
        Cadena con duplicados eliminados.
    """
    if not texto or sep not in texto:
        return texto

    autores = [a.strip() for a in texto.split(sep)]

    def normalizar(s: str) -> str:
        s_norm = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
        return s_norm.lower().strip()

    vistos: set[str] = set()
    unicos: list[str] = []
    for autor in autores:
        clave = normalizar(autor)
        if clave and clave not in vistos:
            vistos.add(clave)
            unicos.append(autor)

    resultado = sep.join(unicos)
    if resultado != texto:
        logger.debug(
            "[TextFixer] normalizar_autores: %d → %d autores únicos",
            len(autores), len(unicos),
        )
    return resultado


# ---------------------------------------------------------------------------
# Aplicador compuesto: corre todos los correctores sobre una fila
# ---------------------------------------------------------------------------

def aplicar_correctores_programaticos(
    fila: dict,
    anomalias_por_campo: dict[str, list[str]],
) -> dict:
    """
    Aplica los correctores programáticos pertinentes a cada campo de la fila
    según las anomalías detectadas por `analizar_fila`.

    Solo modifica los campos que presentan anomalías. Los campos limpios
    se copian sin cambios. No invoca al LLM.

    Args:
        fila:                 Dict con los metadatos originales de la fila.
        anomalias_por_campo:  Resultado de `analizar_fila` (campo → [anomalías]).

    Returns:
        Nuevo dict con los campos corregidos programáticamente.
        Los campos que no pudieron corregirse conservan su valor original.
        Se agrega `_corrections_applied: list[str]` para trazabilidad.
    """
    fila_corregida = dict(fila)
    correcciones_aplicadas: list[str] = []

    # Remover metadatos de curación internos si existen (no son metadatos reales)
    fila_corregida.pop("_curation", None)

    for campo, anomalias in anomalias_por_campo.items():
        valor_original = str(fila.get(campo) or "")
        valor = valor_original

        if "ARTEFACTO_CID" in anomalias:
            valor = remove_cid_artifacts(valor)
            if valor != valor_original:
                correcciones_aplicadas.append(f"{campo}:ARTEFACTO_CID")

        if "REPETICION_CICLICA" in anomalias:
            valor = deduplicate_cyclic_text(valor)
            if len(valor) < len(valor_original):
                correcciones_aplicadas.append(f"{campo}:REPETICION_CICLICA")

        if "CHARS_DISPERSOS" in anomalias:
            valor = fix_spaced_chars(valor)
            correcciones_aplicadas.append(f"{campo}:CHARS_DISPERSOS")

        if "TEXTO_PEGADO" in anomalias and campo in ("description", "citation"):
            # Aplicar solo en campos de texto libre; no en autores/título
            valor = fix_glued_words(valor)
            correcciones_aplicadas.append(f"{campo}:TEXTO_PEGADO")

        fila_corregida[campo] = valor

    # Normalizar autores independientemente de si hay anomalía detectada
    if "author" in fila_corregida:
        autores_originales = str(fila_corregida["author"] or "")
        fila_corregida["author"] = normalizar_autores(autores_originales)
        if fila_corregida["author"] != autores_originales:
            correcciones_aplicadas.append("author:DUPLICADOS")

    fila_corregida["_corrections_applied"] = correcciones_aplicadas
    return fila_corregida
