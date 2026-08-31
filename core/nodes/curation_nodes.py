"""
Nodos del subgrafo de Curación de Metadatos PDF.

Responsable de detectar y corregir metadatos malformados en el CSV
generado por el PDFIngest antes de que el pipeline continúe con el
CrosswalkDedupSubgraph.

Arquitectura de 3 capas:
    Capa 1 (0 tokens):      Detección heurística — triaje del lote.
    Capa 2 (tokens selectivos): Agente curador — solo filas sospechosas.
    Capa 3 (0 tokens LLM): Correctores programáticos + validación con enrichers
                             (la Capa 3 está integrada como tools del agente en Capa 2).

El resultado se escribe en `workspace_dir/curated_from_pdfs.csv`.
El CSV original `source_from_pdfs.csv` nunca se sobreescribe.
"""

from __future__ import annotations

import csv
import json
import logging
import os
from typing import Any

from core.agent.metadata_curator_agent import (
    build_metadata_curator_agent,
    construir_mensaje_curacion,
    parsear_respuesta_agente,
)
from core.state import State
from core.utils.config import config
from core.utils.heuristic_detectors import triar_registros
from core.utils.text_fixers import aplicar_correctores_programaticos

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Campos del CSV de salida (orden canónico)
# ---------------------------------------------------------------------------

_CAMPOS_CSV = [
    "id", "title", "author", "description", "date", "type",
    "subject", "issn", "isbn", "doi", "citation", "rights", "rightsurl",
]


# ---------------------------------------------------------------------------
# Nodo principal: curate_metadata_node
# ---------------------------------------------------------------------------

async def curate_metadata_node(state: State) -> dict[str, Any]:
    """
    Nodo CurateMetadata — Cura los metadatos del CSV generado por PDFIngest.

    Puede ser invocado desde dos rutas:
      1. Tras PDFIngest: lee `source_csv_path` (CSV recién generado).
      2. Directamente desde un CSV existente: también lee `source_csv_path`.

    En ambos casos, el CSV curado se escribe en `workspace_dir/curated_from_pdfs.csv`
    y se actualiza `state["curated_csv_path"]` con esa ruta.

    Flujo interno:
      1. Leer el CSV de source_csv_path.
      2. Capa 1: triar_registros() — clasificar filas en limpias/sospechosas.
      3. Para filas sospechosas: aplicar correctores programáticos (text_fixers).
      4. Si quedan anomalías irresolubles: invocar MetadataCuratorAgent (LLM).
      5. Escribir el CSV curado en curated_from_pdfs.csv.
      6. Actualizar state con curated_csv_path y curation_stats.

    Raises:
        ValueError: Si `source_csv_path` no está definido en el estado.
    """
    source_csv = state.get("source_csv_path", "")
    workspace_dir = state.get("workspace_dir", "/tmp")

    if not source_csv:
        raise ValueError(
            "[CurateMetadata] 'source_csv_path' no está definido en el estado. "
            "Asegurate de que el PDFIngest se ejecutó antes o de que el path esté configurado."
        )

    if not os.path.isfile(source_csv):
        logger.error("[CurateMetadata] CSV fuente no encontrado: '%s'", source_csv)
        return {
            "curated_csv_path": source_csv,
            "curation_stats": {"error": f"CSV no encontrado: {source_csv}"},
        }

    output_csv = os.path.join(workspace_dir, "curated_from_pdfs.csv")
    umbral = config.CURATION_ANOMALY_THRESHOLD

    logger.info(
        "[CurateMetadata] Iniciando curación. CSV fuente: '%s', umbral: %.2f",
        source_csv, umbral,
    )

    # ── 1. Leer CSV fuente ───────────────────────────────────────────────────
    registros = _leer_csv(source_csv)

    if not registros:
        logger.warning("[CurateMetadata] CSV vacío. Copiando sin cambios a '%s'.", output_csv)
        _escribir_csv([], output_csv)
        return {
            "curated_csv_path": output_csv,
            "curation_stats": {"total": 0, "limpias": 0, "curadas": 0, "marcadas": 0, "errores": 0},
        }

    logger.info("[CurateMetadata] Leídos %d registros desde '%s'.", len(registros), source_csv)

    # ── 2. Capa 1: triaje heurístico ─────────────────────────────────────────
    limpias, sospechosas, sin_datos = triar_registros(registros, umbral=umbral)

    stats: dict[str, Any] = {
        "total": len(registros),
        "limpias": len(limpias),
        "sospechosas_detectadas": len(sospechosas),
        "sin_datos": len(sin_datos),
        "curadas_programatico": 0,
        "curadas_agente": 0,
        "marcadas_revision": 0,
        "errores": 0,
    }

    # ── 3. Capa 2a: correctores programáticos sobre sospechosas ──────────────
    # Se aplican ANTES del LLM para reducir el trabajo del agente
    sospechosas_post_fix: list[dict] = []
    curadas_por_fix: list[dict] = []

    for fila in sospechosas:
        curation_meta = fila.get("_curation", {})
        anomalias = curation_meta.get("anomalias", {})

        fila_corregida = aplicar_correctores_programaticos(fila, anomalias)

        # Verificar si quedan anomalías significativas después de los correctores
        anomalias_restantes = _calcular_anomalias_restantes(fila_corregida, anomalias)

        if anomalias_restantes:
            # Aún hay problemas → enviar al agente
            fila_corregida["_curation"] = {
                "anomalias": anomalias_restantes,
                "score": curation_meta.get("score", 0.0),
            }
            sospechosas_post_fix.append(fila_corregida)
        else:
            # Los correctores resolvieron todo → fila limpia
            _limpiar_metadatos_internos(fila_corregida)
            curadas_por_fix.append(fila_corregida)
            stats["curadas_programatico"] += 1

    logger.info(
        "[CurateMetadata] Correctores programáticos: %d filas resueltas, %d al agente LLM.",
        len(curadas_por_fix), len(sospechosas_post_fix),
    )

    # ── 4. Capa 2b: agente curador LLM (solo si quedan sospechosas) ──────────
    curadas_por_agente: list[dict] = []
    marcadas_revision: list[dict] = []

    if sospechosas_post_fix:
        curadas_por_agente, marcadas_revision = await _invocar_agente_curador(
            sospechosas_post_fix, stats
        )

    # ── 5. Ensamblar resultado final ─────────────────────────────────────────
    todos_los_registros = (
        limpias
        + curadas_por_fix
        + curadas_por_agente
        + marcadas_revision
        + sin_datos
    )

    # Restaurar orden original por id
    orden_original = {r.get("id", ""): i for i, r in enumerate(registros)}
    todos_los_registros.sort(
        key=lambda r: orden_original.get(r.get("id", ""), 9999)
    )

    _escribir_csv(todos_los_registros, output_csv)

    stats["marcadas_revision"] = len(marcadas_revision)

    logger.info(
        "[CurateMetadata] Curación completada. "
        "Limpias: %d, Fix programático: %d, Agente: %d, Marcadas: %d, Sin datos: %d. "
        "CSV curado: '%s'",
        len(limpias), stats["curadas_programatico"],
        stats["curadas_agente"], stats["marcadas_revision"],
        len(sin_datos), output_csv,
    )

    return {
        "curated_csv_path": output_csv,
        "curation_stats": stats,
    }


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------

def _leer_csv(csv_path: str) -> list[dict]:
    """Lee un CSV y devuelve la lista de registros como dicts."""
    registros: list[dict] = []
    try:
        with open(csv_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                registros.append(dict(row))
    except Exception as exc:
        logger.error("[CurateMetadata] Error leyendo CSV '%s': %s", csv_path, exc)
    return registros


def _escribir_csv(registros: list[dict], output_path: str) -> None:
    """
    Escribe los registros curados en un CSV.

    Usa los campos canónicos definidos en `_CAMPOS_CSV`. Los campos extra
    generados por la curación (ej. `curation_needed`, `correction_notes`)
    se agregan al final para trazabilidad.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    if not registros:
        with open(output_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(_CAMPOS_CSV)
        return

    # Unión de todos los campos presentes (canónicos primero, luego extras)
    campos_extra: list[str] = []
    for r in registros:
        for k in r.keys():
            if k not in _CAMPOS_CSV and k not in campos_extra and not k.startswith("_"):
                campos_extra.append(k)

    fieldnames = _CAMPOS_CSV + campos_extra

    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(registros)


def _limpiar_metadatos_internos(fila: dict) -> None:
    """Elimina in-place las claves internas de curación del dict de la fila."""
    for clave in ("_curation", "_corrections_applied"):
        fila.pop(clave, None)


def _calcular_anomalias_restantes(
    fila_corregida: dict,
    anomalias_originales: dict[str, list[str]],
) -> dict[str, list[str]]:
    """
    Determina qué anomalías NO pudieron resolverse con los correctores programáticos.

    Re-verifica los campos corregidos contra las anomalías originales. Las
    anomalías que los correctores sí resolvieron se omiten del resultado.

    Anomalías que SOLO el agente puede resolver (siempre pasan):
      - CAMPO_VACIO (el corrector no inventa datos)
      - CHARS_DISPERSOS residual (si fix_spaced_chars no reconstituyó el texto)

    Returns:
        Dict de anomalías restantes. Si está vacío, la fila quedó limpia.
    """
    from core.utils.heuristic_detectors import (
        detectar_chars_dispersos,
        detectar_artefactos_cid,
        detectar_repeticion_ciclica,
        detectar_texto_pegado,
    )

    _DETECTORES = {
        "CHARS_DISPERSOS":    detectar_chars_dispersos,
        "ARTEFACTO_CID":      detectar_artefactos_cid,
        "REPETICION_CICLICA": detectar_repeticion_ciclica,
        "TEXTO_PEGADO":       detectar_texto_pegado,
    }

    restantes: dict[str, list[str]] = {}

    for campo, anomalias in anomalias_originales.items():
        valor = str(fila_corregida.get(campo) or "").strip()
        anomalias_aun_presentes: list[str] = []

        for anomalia in anomalias:
            if anomalia == "CAMPO_VACIO":
                # Un campo vacío sigue vacío → el agente decide si puede completarlo
                if not valor:
                    anomalias_aun_presentes.append("CAMPO_VACIO")
            elif anomalia in _DETECTORES:
                if valor and _DETECTORES[anomalia](valor):
                    anomalias_aun_presentes.append(anomalia)
            else:
                # Anomalías que los correctores no manejan → siempre al agente
                anomalias_aun_presentes.append(anomalia)

        if anomalias_aun_presentes:
            restantes[campo] = anomalias_aun_presentes

    return restantes


async def _invocar_agente_curador(
    filas_sospechosas: list[dict],
    stats: dict,
) -> tuple[list[dict], list[dict]]:
    """
    Invoca al MetadataCuratorAgent con las filas sospechosas que no pudieron
    ser corregidas programáticamente.

    El agente procesa las filas en un único batch para minimizar el overhead
    de inicialización. Los resultados se parsean y se aplican sobre las filas
    originales.

    Args:
        filas_sospechosas: Filas ya pre-procesadas por correctores programáticos.
        stats:             Dict de estadísticas (modificado in-place).

    Returns:
        Tupla (curadas, marcadas_revision).
    """
    try:
        agente = await build_metadata_curator_agent()
        mensaje_entrada = construir_mensaje_curacion(filas_sospechosas)

        logger.info(
            "[CurateMetadata] Invocando agente curador con %d filas sospechosas...",
            len(filas_sospechosas),
        )

        resultado_estado = await agente.ainvoke(
            {"messages": [mensaje_entrada]},
            config={"recursion_limit": 15},
        )

        # Extraer el último mensaje (respuesta final del agente)
        mensajes = resultado_estado.get("messages", [])
        respuesta_texto = ""
        for msg in reversed(mensajes):
            if hasattr(msg, "content") and isinstance(msg.content, str):
                respuesta_texto = msg.content
                break

        correcciones = parsear_respuesta_agente(respuesta_texto)

        # Aplicar correcciones sobre las filas originales
        curadas: list[dict] = []
        marcadas: list[dict] = []

        # Indexar correcciones por id para lookup rápido
        correcciones_por_id: dict[str, dict] = {
            c.get("id", ""): c for c in correcciones if c.get("id")
        }

        for fila in filas_sospechosas:
            fila_id = fila.get("id", "")
            correccion = correcciones_por_id.get(fila_id, {})

            fila_resultado = dict(fila)
            _limpiar_metadatos_internos(fila_resultado)

            if correccion:
                # Aplicar solo los campos corregidos por el agente
                campos_a_actualizar = {
                    k: v for k, v in correccion.items()
                    if k not in ("id", "correction_notes", "_curation")
                    and not k.endswith("_curation_needed")
                }
                fila_resultado.update(campos_a_actualizar)

                # Preservar notas de curación para trazabilidad
                if "correction_notes" in correccion:
                    fila_resultado["correction_notes"] = correccion["correction_notes"]

                # Verificar si algún campo fue marcado como curation_needed
                tiene_marcados = any(
                    k.endswith("_curation_needed") and v
                    for k, v in correccion.items()
                )

                if tiene_marcados:
                    # Algunos campos marcados, pero el agente hizo algo → semi-curado
                    fila_resultado["curation_needed"] = True
                    marcadas.append(fila_resultado)
                    stats["marcadas_revision"] = stats.get("marcadas_revision", 0) + 1
                else:
                    curadas.append(fila_resultado)
                    stats["curadas_agente"] = stats.get("curadas_agente", 0) + 1
            else:
                # El agente no devolvió correcciones para este id → marcar
                fila_resultado["curation_needed"] = True
                fila_resultado["correction_notes"] = (
                    "El agente no pudo corregir este registro. Revisar manualmente."
                )
                marcadas.append(fila_resultado)
                stats["marcadas_revision"] = stats.get("marcadas_revision", 0) + 1

        return curadas, marcadas

    except Exception as exc:
        logger.error("[CurateMetadata] Error invocando agente curador: %s", exc, exc_info=True)
        stats["errores"] = stats.get("errores", 0) + len(filas_sospechosas)

        # En caso de error del agente, devolver todas las filas como marcadas
        marcadas_por_error: list[dict] = []
        for fila in filas_sospechosas:
            fila_resultado = dict(fila)
            _limpiar_metadatos_internos(fila_resultado)
            fila_resultado["curation_needed"] = True
            fila_resultado["correction_notes"] = f"Error en agente curador: {exc}"
            marcadas_por_error.append(fila_resultado)

        return [], marcadas_por_error
