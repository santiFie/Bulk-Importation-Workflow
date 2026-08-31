"""
Detectores heurísticos de anomalías en metadatos extraídos de PDFs.

Módulo de la Capa 1 del pipeline de curación. Analiza cada campo de una
fila de metadatos y asigna un score de anomalía sin consumir tokens LLM.

Solo las filas que superen el umbral `CURATION_ANOMALY_THRESHOLD` se envían
al agente curador (Capa 2), minimizando el costo de inferencia.

Tipos de anomalías detectables:
    - CHARS_DISPERSOS:   texto con espacios entre cada carácter (OCR de columnas)
    - ARTEFACTO_CID:     marcadores internos de PDF como `(cid:27)`
    - TEXTO_PEGADO:      palabras fusionadas sin espacio (OCR de PDFs multi-columna)
    - REPETICION_CICLICA: subcadenas repetidas en loop (extractor atascado)
    - LONGITUD_ANOMALA:  campo extremadamente largo respecto a la media del lote
    - CAMPO_VACIO:       campo obligatorio sin valor
"""

from __future__ import annotations

import logging
import re
import statistics
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes y patrones compilados
# ---------------------------------------------------------------------------

# Patrón para detectar artefactos CID embebidos en texto de PDF
_RE_CID = re.compile(r"\(cid:\d+\)")

# Patrón para detectar repetición cíclica: subcadena de ≥15 chars repetida ≥2 veces
_RE_CICLO = re.compile(r"(.{15,}?)\1{2,}")

# Separadores de palabras normales en español/inglés
_RE_WORD_BOUNDARY = re.compile(r"\b\w+\b")

# Carácteres de control y no imprimibles (excepto saltos de línea y tabuladores normales)
_RE_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# Umbral de longitud mínima para analizar un campo
_MIN_FIELD_LEN = 5

# Número de desviaciones estándar para considerar un campo anómalamente largo
_Z_SCORE_UMBRAL = 3.0

# Campos que se consideran obligatorios para calcular CAMPO_VACIO
_CAMPOS_OBLIGATORIOS = {"title", "author", "type"}


# ---------------------------------------------------------------------------
# Detectores individuales (funciones puras)
# ---------------------------------------------------------------------------

def detectar_chars_dispersos(texto: str) -> bool:
    """
    Detecta texto con caracteres separados por espacios individuales,
    síntoma típico de extracción OCR sobre texto en columnas o tablas.

    Ejemplo detectado: "E s t i m a r  T e x t u r a s"
    Ejemplo normal:    "Estimar Texturas"

    Heurística: si más del 60 % de las "palabras" tienen longitud 1
    y el texto tiene al menos 10 tokens, se considera disperso.
    """
    if len(texto) < _MIN_FIELD_LEN:
        return False
    tokens = texto.split()
    if len(tokens) < 8:
        return False
    chars_simples = sum(1 for t in tokens if len(t) == 1)
    return (chars_simples / len(tokens)) > 0.60


def detectar_artefactos_cid(texto: str) -> bool:
    """
    Detecta marcadores internos del formato PDF de la forma `(cid:XX)`.

    Estos artefactos aparecen cuando el parser de texto del PDF no puede
    resolver el glyph correspondiente a un carácter especial o de una
    fuente embebida no estándar.

    Ejemplo: "di(cid:27)erent criterions are compared:besta(cid:30)ne"
    """
    return bool(_RE_CID.search(texto))


def detectar_repeticion_ciclica(texto: str) -> bool:
    """
    Detecta subcadenas repetidas en bucle dentro de un campo.

    Síntoma típico del extractor cuando itera sobre el mismo bloque de
    texto varias veces sin detectar el límite del campo.

    Ejemplo: "CONICET, Buenos Aires – CONICET, Buenos Aires – CONICET..."
    """
    if len(texto) < 30:
        return False
    return bool(_RE_CICLO.search(texto))


def detectar_texto_pegado(texto: str) -> bool:
    """
    Detecta palabras fusionadas sin espacio, otro síntoma frecuente de
    extracción defectuosa en PDFs con layouts multi-columna o ligaduras.

    Heurística: si más del 15 % de las 'palabras' (tokens delimitados
    por espacios) supera los 30 caracteres, el texto probablemente tiene
    palabras pegadas.

    Ejemplos: "LaTransformadaDiscretadeKarhunen", "segmentation ofdifferent"
    """
    if len(texto) < _MIN_FIELD_LEN:
        return False
    tokens = texto.split()
    if not tokens:
        return False
    palabras_largas = sum(1 for t in tokens if len(t) > 30)
    return (palabras_largas / len(tokens)) > 0.15


def detectar_chars_control(texto: str) -> bool:
    """
    Detecta caracteres de control o no imprimibles en el texto.
    Indica posible corrupción binaria filtrada como texto ASCII.
    """
    return bool(_RE_CONTROL.search(texto))


def detectar_longitud_anomala(
    valor: str,
    media: float,
    desviacion: float,
) -> bool:
    """
    Detecta si un campo tiene una longitud anómalamente larga
    respecto a la distribución del mismo campo en el lote.

    Usa z-score: si len(valor) > media + Z_SCORE_UMBRAL * desviación → anómalo.

    Args:
        valor:      Texto del campo a evaluar.
        media:      Media de longitudes de ese campo en el lote.
        desviacion: Desviación estándar de longitudes de ese campo en el lote.
    """
    if desviacion < 1.0:
        # Sin variabilidad en el lote; usar umbral absoluto de 500 chars
        return len(valor) > 500
    z = (len(valor) - media) / desviacion
    return z > _Z_SCORE_UMBRAL


# ---------------------------------------------------------------------------
# Función de análisis de lote: calcula estadísticas por campo
# ---------------------------------------------------------------------------

def calcular_estadisticas_lote(
    registros: list[dict[str, Any]],
    campos: list[str],
) -> dict[str, tuple[float, float]]:
    """
    Calcula media y desviación estándar de longitudes para cada campo
    del lote de registros. Usado por el detector de longitud anómala.

    Args:
        registros: Lista de dicts con los metadatos de cada PDF.
        campos:    Nombres de campos a analizar.

    Returns:
        Dict `{campo: (media, desviación)}` para cada campo solicitado.
    """
    stats: dict[str, tuple[float, float]] = {}
    for campo in campos:
        longitudes = [
            len(str(r.get(campo, "") or ""))
            for r in registros
        ]
        if len(longitudes) < 2:
            stats[campo] = (float(longitudes[0]) if longitudes else 0.0, 0.0)
        else:
            stats[campo] = (statistics.mean(longitudes), statistics.stdev(longitudes))
    return stats


# ---------------------------------------------------------------------------
# Analizador principal de fila
# ---------------------------------------------------------------------------

def analizar_fila(
    fila: dict[str, Any],
    stats_lote: dict[str, tuple[float, float]],
) -> dict[str, list[str]]:
    """
    Analiza todos los campos de una fila y retorna un dict de anomalías
    detectadas por campo.

    Args:
        fila:       Dict con los metadatos de un ítem (una fila del CSV).
        stats_lote: Estadísticas de longitud del lote (de calcular_estadisticas_lote).

    Returns:
        Dict `{campo: [lista_de_anomalías]}`. Si el dict está vacío,
        la fila se considera limpia. Ejemplos de anomalías:
        - "CHARS_DISPERSOS"
        - "ARTEFACTO_CID"
        - "REPETICION_CICLICA"
        - "TEXTO_PEGADO"
        - "LONGITUD_ANOMALA"
        - "CAMPO_VACIO"
    """
    anomalias: dict[str, list[str]] = {}

    for campo, valor_raw in fila.items():
        valor = str(valor_raw or "").strip()
        campo_anomalias: list[str] = []

        # Verificar campo obligatorio vacío
        if campo in _CAMPOS_OBLIGATORIOS and not valor:
            campo_anomalias.append("CAMPO_VACIO")

        if valor:
            if detectar_chars_dispersos(valor):
                campo_anomalias.append("CHARS_DISPERSOS")

            if detectar_artefactos_cid(valor):
                campo_anomalias.append("ARTEFACTO_CID")

            if detectar_repeticion_ciclica(valor):
                campo_anomalias.append("REPETICION_CICLICA")

            if detectar_texto_pegado(valor):
                campo_anomalias.append("TEXTO_PEGADO")

            if detectar_chars_control(valor):
                campo_anomalias.append("CHARS_CONTROL")

            # Longitud anómala (solo si tenemos estadísticas del lote)
            if campo in stats_lote:
                media, desviacion = stats_lote[campo]
                if detectar_longitud_anomala(valor, media, desviacion):
                    campo_anomalias.append("LONGITUD_ANOMALA")

        if campo_anomalias:
            anomalias[campo] = campo_anomalias

    return anomalias


def calcular_score_anomalia(anomalias: dict[str, list[str]]) -> float:
    """
    Calcula un score de anomalía agregado para una fila en el rango [0, 1].

    El score pondera las anomalías por severidad: las anomalías críticas
    (CHARS_DISPERSOS, REPETICION_CICLICA) pesan más que las leves
    (TEXTO_PEGADO, LONGITUD_ANOMALA).

    Args:
        anomalias: Resultado de `analizar_fila`.

    Returns:
        Float en [0, 1] donde 0 = sin anomalías, 1 = gravemente malformado.
    """
    if not anomalias:
        return 0.0

    # Peso por tipo de anomalía (valores > 1 son posibles antes de normalizar)
    _PESOS: dict[str, float] = {
        "CHARS_DISPERSOS":   0.9,
        "REPETICION_CICLICA": 0.8,
        "CAMPO_VACIO":       0.6,
        "ARTEFACTO_CID":     0.5,
        "TEXTO_PEGADO":      0.4,
        "LONGITUD_ANOMALA":  0.35,
        "CHARS_CONTROL":     0.7,
    }

    score_total = 0.0
    for campo_anomalias in anomalias.values():
        for anomalia in campo_anomalias:
            score_total += _PESOS.get(anomalia, 0.3)

    # Normalizar: cap en 1.0
    return min(score_total, 1.0)


# ---------------------------------------------------------------------------
# Función de triaje de lote
# ---------------------------------------------------------------------------

def triar_registros(
    registros: list[dict[str, Any]],
    umbral: float = 0.3,
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Clasifica un lote de registros en limpios, sospechosos y no analizables.

    Flujo:
      1. Calcula estadísticas de longitud del lote (para detector longitud anómala).
      2. Analiza cada fila con `analizar_fila`.
      3. Clasifica según score vs. umbral.

    Args:
        registros: Lista de dicts de metadatos (un dict por PDF).
        umbral:    Score mínimo para considerar una fila como sospechosa.
                   Default: 0.3 (configurable via config.CURATION_ANOMALY_THRESHOLD).

    Returns:
        Tupla (limpios, sospechosos, sin_datos):
          - limpios:      registros sin anomalías detectadas (score < umbral).
          - sospechosos:  registros con anomalías y metadatos de curación adjuntos.
                          Cada dict incluye una clave "_curation" con
                          {"anomalias": {...}, "score": float}.
          - sin_datos:    registros sin ningún campo con valor (totalmente vacíos).
    """
    if not registros:
        return [], [], []

    # Campos a analizar para longitud anómala (excluir 'id' que siempre varía)
    campos_a_medir = [k for k in registros[0].keys() if k != "id"]
    stats_lote = calcular_estadisticas_lote(registros, campos_a_medir)

    limpios: list[dict] = []
    sospechosos: list[dict] = []
    sin_datos: list[dict] = []

    for registro in registros:
        # Verificar si el registro tiene algún campo con valor
        tiene_datos = any(
            v and str(v).strip()
            for k, v in registro.items()
            if k != "id"
        )
        if not tiene_datos:
            sin_datos.append(registro)
            continue

        anomalias = analizar_fila(registro, stats_lote)
        score = calcular_score_anomalia(anomalias)

        if score >= umbral:
            registro_con_meta = dict(registro)
            registro_con_meta["_curation"] = {
                "anomalias": anomalias,
                "score": round(score, 3),
            }
            sospechosos.append(registro_con_meta)
            logger.debug(
                "[HeuristicDetector] Fila sospechosa id='%s', score=%.3f, anomalías=%s",
                registro.get("id", "?"),
                score,
                anomalias,
            )
        else:
            limpios.append(registro)

    logger.info(
        "[HeuristicDetector] Triaje completado: %d limpias, %d sospechosas, %d sin datos (total=%d)",
        len(limpios), len(sospechosos), len(sin_datos), len(registros),
    )

    return limpios, sospechosos, sin_datos
