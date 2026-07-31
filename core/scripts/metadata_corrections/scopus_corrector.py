"""
Corrector de metadatos específico para el repositorio SCOPUS.

Correcciones aplicadas (según documentación del pipeline):
1. Recuperar los nombres de afiliaciones UNLP desde la columna
   `autores_unlp_nombre` (o su equivalente mapeado al formato SEDICI) y
   generar/completar el metadato `mods.originInfo.place[es]`.
2. Cambiar todos los separadores por `||` (delegado al padre).
3. Normalizar `dc.language` a 'es', 'en', 'pt' (delegado al padre).
4. Filtrar autores: si la cantidad de autores es mayor a 30, conservar
   solo los autores pertenecientes a la UNLP.
"""

from __future__ import annotations

import logging
import re

import pandas as pd

from .base import MetadataCorrector, register_corrector

logger = logging.getLogger(__name__)

# Número máximo de autores antes de aplicar el filtrado por UNLP
MAX_AUTHORS = 30

# Separador canónico SEDICI
SEP = "||"


@register_corrector("scopus")
class ScopusCorrector(MetadataCorrector):
    """
    Corrector para ítems provenientes de SCOPUS.

    El crosswalk del Paso 5 mapea los campos del CSV original de SCOPUS al
    formato SEDICI. Sin embargo, algunos campos requieren transformaciones
    adicionales que el crosswalk no puede expresar de forma declarativa.
    """

    def _apply_specific_corrections(self, df: pd.DataFrame) -> pd.DataFrame:
        df = self._fix_affiliation_place(df)
        df = self._filter_authors(df)
        return df

    # ------------------------------------------------------------------
    # Corrección 1 — Afiliaciones → mods.originInfo.place[es]
    # ------------------------------------------------------------------

    def _fix_affiliation_place(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Completa `mods.originInfo.place[es]` usando los nombres de las
        afiliaciones UNLP de los autores.

        Lógica:
        - Busca la columna `autores_unlp_nombre` (nombre original del CSV de
          SCOPUS con autores de la UNLP, generado por el proceso de María).
          Si el crosswalk ya la renombró a otra columna, intenta detectar la
          columna más probable por patrones de nombre.
        - Si la columna de afiliaciones existe y `mods.originInfo.place[es]`
          está vacía (o no existe), la rellena con el valor de afiliación.
        - Si ya existe un valor en `mods.originInfo.place[es]`, lo conserva.
        """
        place_col = self._find_col(df, r"mods\.originInfo\.place")
        affil_col = self._find_col(
            df,
            r"autores_unlp_nombre|unlp.*nombre|afilia[ct]",
        )

        if affil_col is None:
            logger.debug(
                "[ScopusCorrector] No se encontró columna de afiliaciones UNLP. "
                "Se omite la corrección de mods.originInfo.place."
            )
            return df

        if place_col is None:
            # Crear la columna si no existe
            place_col = "mods.originInfo.place[es]"
            df[place_col] = ""

        def _fill_place(row: pd.Series) -> str:
            current = str(row[place_col]).strip() if pd.notna(row[place_col]) else ""
            if current and current not in ("", "nan"):
                return current
            affil = str(row[affil_col]).strip() if pd.notna(row[affil_col]) else ""
            if affil and affil not in ("", "nan"):
                # Normalizar separadores en la afiliación antes de usarla
                affil = re.sub(r"\|\|\|", SEP, affil)
                affil = re.sub(r"(?<!\|)\|(?!\|)", SEP, affil)
                return affil
            return current

        df[place_col] = df.apply(_fill_place, axis=1)
        logger.info(
            "[ScopusCorrector] mods.originInfo.place corregido desde '%s'.", affil_col
        )
        return df

    # ------------------------------------------------------------------
    # Corrección 2 — Filtrado de autores
    # ------------------------------------------------------------------

    def _filter_authors(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Si un ítem tiene más de MAX_AUTHORS autores, conserva únicamente
        los autores pertenecientes a la UNLP.

        Columnas involucradas:
        - Columna de autores completa (e.g. `sedici.creator.person[es]` o
          `dc.contributor.author`).
        - Columna de autores UNLP (e.g. `autores_unlp_nombre`).

        Si la columna de autores UNLP no existe en el CSV de salida del
        Paso 5 (es posible que el crosswalk no la haya conservado), se
        intenta usar la columna de origen si todavía está presente.
        En ese caso se emite un warning y no se filtra.
        """
        author_col = self._find_col(
            df,
            r"sedici\.creator\.person|dc\.contributor\.author",
        )
        unlp_col = self._find_col(
            df,
            r"autores_unlp_nombre|unlp.*nombre",
        )

        if author_col is None:
            logger.debug(
                "[ScopusCorrector] No se encontró columna de autores. "
                "Se omite el filtrado."
            )
            return df

        if unlp_col is None:
            logger.warning(
                "[ScopusCorrector] No se encontró columna de autores UNLP "
                "(%s). No se puede aplicar el filtrado por autores UNLP "
                "cuando hay más de %d autores.",
                "autores_unlp_nombre",
                MAX_AUTHORS,
            )
            return df

        def _apply_filter(row: pd.Series) -> str:
            authors_raw = str(row[author_col]) if pd.notna(row[author_col]) else ""
            unlp_raw = str(row[unlp_col]) if pd.notna(row[unlp_col]) else ""

            if not authors_raw or authors_raw == "nan":
                return authors_raw

            # Separar lista de autores (|| es el separador normalizado)
            authors = [a.strip() for a in authors_raw.split(SEP) if a.strip()]

            if len(authors) <= MAX_AUTHORS:
                # Dentro del límite: conservar todos
                return SEP.join(authors)

            # Más de MAX_AUTHORS: usar solo los de la UNLP
            if not unlp_raw or unlp_raw == "nan":
                logger.warning(
                    "[ScopusCorrector] Ítem con %d autores pero sin autores "
                    "UNLP definidos. Se conservan todos los autores.",
                    len(authors),
                )
                return SEP.join(authors)

            unlp_authors = [a.strip() for a in unlp_raw.split(SEP) if a.strip()]
            if not unlp_authors:
                return SEP.join(authors)

            logger.info(
                "[ScopusCorrector] Ítem con %d autores → reducido a %d autores UNLP.",
                len(authors),
                len(unlp_authors),
            )
            return SEP.join(unlp_authors)

        df[author_col] = df.apply(_apply_filter, axis=1)
        return df

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_col(df: pd.DataFrame, pattern: str) -> str | None:
        """
        Devuelve el nombre de la primera columna del DataFrame que coincida
        con el patrón regex (case-insensitive). Devuelve None si no hay match.
        """
        for col in df.columns:
            if re.search(pattern, col, re.IGNORECASE):
                return col
        return None
