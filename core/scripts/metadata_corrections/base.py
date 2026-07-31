"""
Paso 6 — Corrección de metadatos antes de generar el SAF.

Cada repositorio origen puede requerir correcciones específicas sobre el CSV
en formato SEDICI (salida del Paso 5). Esta capa aplica transformaciones
programáticas deterministas, sin intervención de agentes LLM.

Patrón de extensión
-------------------
Para agregar correcciones para un nuevo repositorio:
1. Crear una subclase de `MetadataCorrector` en un módulo nuevo bajo este paquete.
2. Registrarla con `@register_corrector("nombre_repositorio")`.
3. Implementar `correct(df)` con las transformaciones necesarias.
"""

from __future__ import annotations

import re
import logging
from abc import ABC, abstractmethod
from typing import Callable

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registro global de correctores por nombre de repositorio origen
# ---------------------------------------------------------------------------

_CORRECTOR_REGISTRY: dict[str, type["MetadataCorrector"]] = {}


def register_corrector(source_name: str):
    """
    Decorador para registrar una subclase de MetadataCorrector bajo un nombre
    de repositorio origen (case-insensitive).

    Ejemplo
    -------
    @register_corrector("scopus")
    class ScopusCorrector(MetadataCorrector):
        ...
    """
    def decorator(cls: type[MetadataCorrector]):
        _CORRECTOR_REGISTRY[source_name.lower()] = cls
        return cls
    return decorator


def get_corrector(source_name: str) -> "MetadataCorrector":
    """
    Devuelve una instancia del corrector apropiado para el repositorio origen.
    Si no hay corrector específico registrado, devuelve el corrector base
    (que aplica únicamente las normalizaciones genéricas).
    """
    cls = _CORRECTOR_REGISTRY.get(source_name.lower(), GenericCorrector)
    return cls()


# ---------------------------------------------------------------------------
# Clase base abstracta
# ---------------------------------------------------------------------------

class MetadataCorrector(ABC):
    """
    Clase base para correctores de metadatos.

    Subclases deben implementar `_apply_specific_corrections(df)` con las
    transformaciones propias del repositorio. Las normalizaciones genéricas
    (separadores, idioma) se aplican automáticamente en `correct()`.
    """

    # Separador canónico para SEDICI
    SEPARATOR = "||"

    # Mapa de normalización de idiomas (minúsculas → código SEDICI)
    LANGUAGE_MAP: dict[str, str] = {
        # Inglés
        "english": "en",
        "inglés": "en",
        "ingles": "en",
        "eng": "en",
        # Español
        "spanish": "es",
        "español": "es",
        "espanol": "es",
        "spa": "es",
        # Portugués
        "portuguese": "pt",
        "portugués": "pt",
        "portugues": "pt",
        "por": "pt",
    }

    # Columnas de idioma que deben normalizarse (con o sin sufijo de idioma)
    LANGUAGE_COLUMNS_PATTERNS: list[str] = [
        "dc.language",
        r"dc\.language\[.*\]",
    ]

    def correct(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Punto de entrada principal. Aplica primero las normalizaciones
        genéricas y luego las específicas del repositorio.

        Parameters
        ----------
        df : pd.DataFrame
            CSV en formato SEDICI (salida del Paso 5).

        Returns
        -------
        pd.DataFrame
            DataFrame con las correcciones aplicadas (mismo schema).
        """
        df = df.copy()
        df = self._normalize_separators(df)
        df = self._normalize_languages(df)
        df = self._apply_specific_corrections(df)
        logger.info(
            "[MetadataCorrector:%s] Correcciones aplicadas sobre %d filas.",
            self.__class__.__name__,
            len(df),
        )
        return df

    # ------------------------------------------------------------------
    # Normalizaciones genéricas (comunes a todos los repositorios)
    # ------------------------------------------------------------------

    def _normalize_separators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Reemplaza variantes de separadores (|, |||, ;, etc.) por el
        separador canónico de SEDICI (||) en todas las columnas de texto.

        Los separadores reconocidos son:
          - |||  (SCOPUS legacy)
          - |    (un solo pipe, cuando no forma parte de ||)
          - ;    (punto y coma, en algunos exportadores)
        """
        # Patterns a reemplazar, en orden de precedencia (más específico primero)
        replacements: list[tuple[str, str]] = [
            (r"\|\|\|", self.SEPARATOR),   # ||| → ||
            # | solo (no precedido ni seguido de |) → ||
            (r"(?<!\|)\|(?!\|)", self.SEPARATOR),
        ]

        for col in df.select_dtypes(include="object").columns:
            for pattern, replacement in replacements:
                df[col] = df[col].astype(str).str.replace(
                    pattern, replacement, regex=True
                )
                # Limpiar "nan" que puede haber quedado tras astype(str)
                df[col] = df[col].replace("nan", "")

        return df

    def _normalize_languages(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Normaliza el contenido de las columnas de idioma al código de dos
        letras esperado por SEDICI: 'es', 'en', 'pt'.

        Actúa sobre cualquier columna cuyo nombre empiece con 'dc.language'.
        """
        lang_cols = [
            c for c in df.columns
            if re.match(r"dc\.language", c, re.IGNORECASE)
        ]

        for col in lang_cols:
            df[col] = df[col].apply(self._map_language)

        return df

    def _map_language(self, value: str) -> str:
        """Convierte un valor de idioma a su código SEDICI normalizado."""
        if not isinstance(value, str) or not value.strip():
            return value
        normalized = self.LANGUAGE_MAP.get(value.strip().lower())
        if normalized:
            return normalized
        # Si ya es un código de dos letras conocido, lo devuelve en minúscula
        clean = value.strip().lower()
        if clean in ("es", "en", "pt", "fr", "de", "it", "zh", "ja", "ar"):
            return clean
        logger.warning(
            "[MetadataCorrector] Idioma desconocido: '%s'. Se conserva sin cambios.", value
        )
        return value

    # ------------------------------------------------------------------
    # Hook para correcciones específicas del repositorio
    # ------------------------------------------------------------------

    def _apply_specific_corrections(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Correcciones específicas del repositorio. Sobreescribir en subclases.
        Por defecto no aplica ninguna corrección adicional.
        """
        return df


# ---------------------------------------------------------------------------
# Corrector genérico (fallback)
# ---------------------------------------------------------------------------

class GenericCorrector(MetadataCorrector):
    """
    Corrector base que se usa cuando no hay uno específico para el repositorio.
    Solo aplica las normalizaciones genéricas (separadores, idioma).
    """

    def _apply_specific_corrections(self, df: pd.DataFrame) -> pd.DataFrame:
        return df
