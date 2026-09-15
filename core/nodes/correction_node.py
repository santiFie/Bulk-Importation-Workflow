"""
Nodo MetadataCorrections — Paso 6 del pipeline.

Lee el CSV en formato SEDICI y aplica correcciones programáticas
específicas según el repositorio origen (source_name).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def metadata_corrections(state: dict) -> dict[str, Any]:
    """
    Paso 6 — Corrección programática de metadatos.

    Lee el CSV en formato SEDICI generado por el Paso 5 (sedici_ready_csv_path)
    y aplica un conjunto de correcciones específicas según el repositorio origen
    (source_name), devolviendo el mismo archivo corregido en su lugar.

    Correcciones genéricas (todos los repositorios):
      - Normalización de separadores a ||
      - Normalización de dc.language a códigos de dos letras (es, en, pt)

    Correcciones específicas de SCOPUS:
      - Generación de mods.originInfo.place[es] desde autores_unlp_nombre
      - Filtrado de autores: si hay más de 30, conservar solo los de la UNLP

    Entrada:  state["sedici_ready_csv_path"]  — CSV en formato SEDICI (Paso 5)
    Salida:   mismo archivo sobreescrito con las correcciones aplicadas
    """
    from core.scripts.metadata_corrections import get_corrector

    csv_path = state["sedici_ready_csv_path"]
    source_name = state.get("source_name", "")

    if not os.path.isfile(csv_path):
        raise FileNotFoundError(f"[MetadataCorrections] CSV no encontrado: {csv_path}")

    df = pd.read_csv(csv_path)
    corrector = get_corrector(source_name)
    df_corrected = corrector.correct(df)

    df_corrected.to_csv(csv_path, index=False)
    logger.info("[MetadataCorrections] Correcciones aplicadas sobre '%s'", csv_path)
    return {}
