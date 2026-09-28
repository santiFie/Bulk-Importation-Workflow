"""
Recuperador determinista para la Memoria Episódica (Zero-LLM Retrieval).

Orquesta la formulación de consultas sintácticas y la búsqueda jerárquica
en ChromaDB sin intermediación ni consumo de tokens de modelos de lenguaje.
"""

from __future__ import annotations

import csv
import logging
import os
import re
from typing import Any, Optional

from core.memory.episodic_memory.models import EpisodeQueryResult
from core.memory.episodic_memory.store import EpisodicMemoryStore

logger = logging.getLogger(__name__)


def extract_error_context(error_msg: str, csv_path: Optional[str] = None) -> dict[str, Any]:
    """
    Analiza determinísticamente el mensaje de error y el archivo CSV para
    identificar la columna y muestras problemáticas.

    Args:
        error_msg: Traza o mensaje de excepción devuelto por la herramienta.
        csv_path: Ruta al CSV involucrado para escanear coincidencias (opcional).

    Returns:
        Diccionario con 'error_class', 'sample_tokens' y 'affected_column'.
    """
    # 1. Extraer clase de error
    error_class_match = re.search(r"\b([A-Z][a-zA-Z]+Error)\b", error_msg)
    error_class = error_class_match.group(1) if error_class_match else "UnknownError"

    # 2. Extraer literales citados entre comillas simples o dobles
    quoted_tokens = re.findall(r"['\"]([^'\"]+)['\"]", error_msg)
    sample_tokens = [tok.strip() for tok in quoted_tokens if tok.strip() and len(tok.strip()) < 50]

    affected_column = ""

    # 3. Si se dispone del CSV, buscar qué columna contiene alguno de los tokens
    if csv_path and os.path.isfile(csv_path) and sample_tokens:
        try:
            with open(csv_path, "r", encoding="utf-8", errors="replace") as fh:
                sample_lines = [fh.readline() for _ in range(50)]
            
            # Detectar delimitador
            dialect_delim = ","
            try:
                sniffer = csv.Sniffer()
                sample_text = "".join(sample_lines[:10])
                dialect_delim = sniffer.sniff(sample_text).delimiter
            except Exception:
                dialect_delim = ","

            reader = csv.DictReader(sample_lines, delimiter=dialect_delim)
            for row in reader:
                for col, val in row.items():
                    if not col or not val:
                        continue
                    for token in sample_tokens:
                        if token.lower() in str(val).lower():
                            affected_column = col
                            break
                    if affected_column:
                        break
                if affected_column:
                    break
        except Exception as exc:
            logger.warning("[extract_error_context] No se pudo escanear el CSV '%s': %s", csv_path, exc)

    # Heurística de respaldo si no se encontró por escaneo
    if not affected_column:
        lower_msg = error_msg.lower()
        known_months = {
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
            "enero", "febrero", "marzo", "abril", "mayo", "junio",
            "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
        }
        if any(w in lower_msg for w in ["fecha", "date", "year", "año"]):
            affected_column = "date"
        elif any(t.lower() in known_months for t in sample_tokens):
            affected_column = "date"
        elif any(w in lower_msg for w in ["autor", "author", "creator"]):
            affected_column = "author"
        elif any(w in lower_msg for w in ["titulo", "title"]):
            affected_column = "title"

    return {
        "error_class": error_class,
        "sample_tokens": sample_tokens,
        "affected_column": affected_column,
    }


def build_search_query(tool: str, error_msg: str, column: str = "", sample: str = "") -> str:
    """Construye la cadena canónica de búsqueda para el embedding denso."""
    # Limpiar saltos de línea y normalizar espacios
    clean_error = " ".join(error_msg.split())
    parts = [f"Tool: {tool}", f"Error: {clean_error}"]
    if column:
        parts.append(f"Column: {column}")
    if sample:
        parts.append(f"Sample: '{sample}'")
    return ". ".join(parts)


def retrieve_relevant_episodes(
    tool: str,
    source_name: str,
    error_msg: str,
    csv_path: Optional[str] = None,
    top_k: int = 2,
    store: Optional[EpisodicMemoryStore] = None,
    min_similarity: float = 0.45,
) -> list[EpisodeQueryResult]:
    """
    Ejecuta la recuperación jerárquica en dos niveles:
      1. Nivel local: filtro estricto por tool y source_name.
      2. Nivel global: fallback filtrando únicamente por tool si no hay coincidencias locales fuertes.

    Args:
        tool: Herramienta afectada ('deduplicator', 'crosswalk', etc.).
        source_name: Nombre del repositorio origen ('unlp_doaj', 'scopus', etc.).
        error_msg: Mensaje o traza del fallo.
        csv_path: Ruta al CSV involucrado para enriquecimiento determinista.
        top_k: Cantidad de resultados máximos deseados.
        store: Instancia del almacén (si es None, se crea una por defecto).
        min_similarity: Umbral mínimo de similitud para descartar falsos positivos.

    Returns:
        Lista de EpisodeQueryResult que superaron el umbral de similitud.
    """
    memory_store = store or EpisodicMemoryStore()

    # Asegurar casos semilla si la base está recién creada
    memory_store.seed_default_episodes()

    context = extract_error_context(error_msg, csv_path)
    sample_repr = context["sample_tokens"][0] if context["sample_tokens"] else ""
    query_text = build_search_query(
        tool=tool,
        error_msg=error_msg,
        column=context["affected_column"],
        sample=sample_repr,
    )

    logger.debug("[EpisodicRetriever] Query formulada: %s", query_text)

    # Nivel 1: Búsqueda específica por repositorio (tool + source_name)
    tier1_filter = {"$and": [{"tool": tool}, {"source_name": source_name}]}
    try:
        tier1_results = memory_store.search_episodes(
            query_text=query_text,
            filter_metadata=tier1_filter,
            top_k=top_k,
        )
        if tier1_results and tier1_results[0].similarity_score >= 0.70:
            logger.info(
                "[EpisodicRetriever] Coincidencia local fuerte hallada en Tier 1 (similitud=%.2f).",
                tier1_results[0].similarity_score,
            )
            return [r for r in tier1_results if r.similarity_score >= min_similarity]
    except Exception as exc:
        logger.debug("[EpisodicRetriever] Tier 1 no arrojó resultados o falló filtro: %s", exc)

    # Nivel 2: Búsqueda global para la herramienta (tool)
    tier2_filter = {"tool": tool}
    tier2_results = memory_store.search_episodes(
        query_text=query_text,
        filter_metadata=tier2_filter,
        top_k=top_k,
    )

    filtered = [r for r in tier2_results if r.similarity_score >= min_similarity]
    logger.info(
        "[EpisodicRetriever] Búsqueda finalizada: %d episodios relevantes recuperados (umbral=%.2f).",
        len(filtered),
        min_similarity,
    )
    return filtered
