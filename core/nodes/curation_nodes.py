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
import logging
import os
from dataclasses import dataclass, field
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
# Campos canónicos del CSV de salida
# ---------------------------------------------------------------------------

_CAMPOS_CSV = [
    "id", "title", "author", "description", "date", "type",
    "subject", "issn", "isbn", "doi", "citation", "rights", "rightsurl",
]

# Claves internas de curación que no deben escribirse en el CSV final
_CLAVES_INTERNAS = ("_curation", "_corrections_applied")


# ---------------------------------------------------------------------------
# Dataclasses de resultado
# ---------------------------------------------------------------------------

@dataclass
class CurationStats:
    """Estadísticas del proceso de curación de un lote de registros."""
    total: int = 0
    limpias: int = 0
    sospechosas_detectadas: int = 0
    sin_datos: int = 0
    curadas_programatico: int = 0
    curadas_agente: int = 0
    marcadas_revision: int = 0
    errores: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "limpias": self.limpias,
            "sospechosas_detectadas": self.sospechosas_detectadas,
            "sin_datos": self.sin_datos,
            "curadas_programatico": self.curadas_programatico,
            "curadas_agente": self.curadas_agente,
            "marcadas_revision": self.marcadas_revision,
            "errores": self.errores,
        }


@dataclass
class CurationResult:
    """Resultado inmutable del proceso de curación de un lote."""
    curadas: list[dict] = field(default_factory=list)
    pendientes: list[dict] = field(default_factory=list)
    stats: CurationStats = field(default_factory=CurationStats)

    def to_state_update(self, output_csv: str, pending_csv: str | None) -> dict[str, Any]:
        """Genera el dict de actualización del State de LangGraph."""
        return {
            "curated_csv_path": output_csv,
            "pending_to_review_csv_path": pending_csv,
            "curation_stats": self.stats.to_dict(),
        }


# ---------------------------------------------------------------------------
# Servicio de I/O de CSV
# ---------------------------------------------------------------------------

class CsvHandler:
    """Encapsula las operaciones de lectura y escritura de CSVs de curación."""

    @staticmethod
    def read(csv_path: str) -> list[dict]:
        """
        Lee un CSV y devuelve la lista de registros como dicts.

        Returns:
            Lista de registros. Lista vacía si el archivo no puede leerse.
        """
        registros: list[dict] = []
        try:
            with open(csv_path, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    registros.append(dict(row))
        except Exception as exc:
            logger.error("[CsvHandler] Error leyendo CSV '%s': %s", csv_path, exc)
        return registros

    @staticmethod
    def write(registros: list[dict], output_path: str) -> None:
        """
        Escribe los registros en un CSV usando los campos canónicos de `_CAMPOS_CSV`.

        Los campos extra (ej. `curation_needed`, `correction_notes`) se agregan
        al final para trazabilidad. Las claves internas (prefijadas con `_`) se omiten.
        """
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        if not registros:
            with open(output_path, "w", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerow(_CAMPOS_CSV)
            return

        campos_extra = [
            k for r in registros for k in r
            if k not in _CAMPOS_CSV and not k.startswith("_")
        ]
        # Preservar orden y unicidad de campos extra
        campos_extra = list(dict.fromkeys(campos_extra))
        fieldnames = _CAMPOS_CSV + campos_extra

        with open(output_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(registros)


# ---------------------------------------------------------------------------
# Resolutor de anomalías restantes
# ---------------------------------------------------------------------------

class AnomalyResolver:
    """
    Determina qué anomalías NO pudieron resolverse con los correctores programáticos.

    Re-verifica los campos corregidos contra las anomalías originales. Las
    anomalías que los correctores sí resolvieron se excluyen del resultado.

    Anomalías que SOLO el agente puede resolver (siempre pasan):
      - CAMPO_VACIO (el corrector no inventa datos)
      - CHARS_DISPERSOS residual (si fix_spaced_chars no reconstituyó el texto)
    """

    def __init__(self) -> None:
        from core.utils.heuristic_detectors import (
            detectar_chars_dispersos,
            detectar_artefactos_cid,
            detectar_repeticion_ciclica,
            detectar_texto_pegado,
        )
        self._detectores = {
            "CHARS_DISPERSOS":    detectar_chars_dispersos,
            "ARTEFACTO_CID":      detectar_artefactos_cid,
            "REPETICION_CICLICA": detectar_repeticion_ciclica,
            "TEXTO_PEGADO":       detectar_texto_pegado,
        }

    def resolve(
        self,
        fila_corregida: dict,
        anomalias_originales: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        """
        Calcula las anomalías que persisten después de los correctores programáticos.

        Args:
            fila_corregida:      Fila ya procesada por los correctores.
            anomalias_originales: Anomalías detectadas antes de los correctores.

        Returns:
            Dict de anomalías restantes. Vacío si la fila quedó limpia.
        """
        restantes: dict[str, list[str]] = {}

        for campo, anomalias in anomalias_originales.items():
            valor = str(fila_corregida.get(campo) or "").strip()
            aun_presentes = [
                a for a in anomalias
                if self._anomalia_persiste(a, valor)
            ]
            if aun_presentes:
                restantes[campo] = aun_presentes

        return restantes

    def _anomalia_persiste(self, anomalia: str, valor: str) -> bool:
        """Verifica si una anomalía específica persiste en el valor corregido."""
        if anomalia == "CAMPO_VACIO":
            return not valor
        if anomalia in self._detectores:
            return bool(valor and self._detectores[anomalia](valor))
        # Anomalías que los correctores no manejan → siempre al agente
        return True


# ---------------------------------------------------------------------------
# Orquestador del pipeline de curación
# ---------------------------------------------------------------------------

class CurationOrchestrator:
    """
    Orquesta el pipeline de 3 capas de curación de metadatos PDF.

    Coordina: triaje heurístico (Capa 1) → correctores programáticos (Capa 2a)
    → agente LLM (Capa 2b) → ensamblado del resultado.

    Attributes:
        threshold: Score mínimo para considerar un registro como sospechoso.
    """

    def __init__(self, threshold: float = 0.3) -> None:
        self._threshold = threshold
        self._resolver = AnomalyResolver()

    async def run(self, registros: list[dict]) -> CurationResult:
        """
        Ejecuta el pipeline completo de curación sobre el lote de registros.

        Args:
            registros: Lista de dicts de metadatos (un dict por ítem del CSV).

        Returns:
            CurationResult con los registros curados, pendientes y estadísticas.
        """
        limpias, sospechosas, sin_datos = triar_registros(registros, umbral=self._threshold)

        stats = CurationStats(
            total=len(registros),
            limpias=len(limpias),
            sospechosas_detectadas=len(sospechosas),
            sin_datos=len(sin_datos),
        )

        # Capa 2a: correctores programáticos
        curadas_por_fix, aun_sospechosas = self._apply_programmatic_fixes(sospechosas, stats)

        # Capa 2b: agente LLM (solo si quedan sospechosas)
        curadas_por_agente: list[dict] = []
        marcadas_revision: list[dict] = []
        if aun_sospechosas:
            curadas_por_agente, marcadas_revision = await self._invoke_agent(
                aun_sospechosas, stats
            )

        curadas_finales = limpias + curadas_por_fix + curadas_por_agente
        pendientes_finales = marcadas_revision + sin_datos

        orden_original = {r.get("id", ""): i for i, r in enumerate(registros)}
        curadas_finales.sort(key=lambda r: orden_original.get(r.get("id", ""), 9999))
        pendientes_finales.sort(key=lambda r: orden_original.get(r.get("id", ""), 9999))

        stats.marcadas_revision = len(marcadas_revision)

        logger.info(
            "[CurationOrchestrator] Completado. Aptas: %d (limpias=%d, fix=%d, agente=%d). "
            "En cuarentena: %d (marcadas=%d, sin_datos=%d).",
            len(curadas_finales),
            len(limpias), stats.curadas_programatico, stats.curadas_agente,
            len(pendientes_finales), stats.marcadas_revision, len(sin_datos),
        )

        return CurationResult(curadas=curadas_finales, pendientes=pendientes_finales, stats=stats)

    # ------------------------------------------------------------------
    # Capa 2a: correctores programáticos
    # ------------------------------------------------------------------

    def _apply_programmatic_fixes(
        self,
        sospechosas: list[dict],
        stats: CurationStats,
    ) -> tuple[list[dict], list[dict]]:
        """
        Aplica correctores programáticos a las filas sospechosas.

        Returns:
            Tupla (curadas_por_fix, aun_sospechosas).
        """
        curadas: list[dict] = []
        aun_sospechosas: list[dict] = []

        for fila in sospechosas:
            curation_meta = fila.get("_curation", {})
            anomalias = curation_meta.get("anomalias", {})

            fila_corregida = aplicar_correctores_programaticos(fila, anomalias)
            anomalias_restantes = self._resolver.resolve(fila_corregida, anomalias)

            if anomalias_restantes:
                fila_corregida["_curation"] = {
                    "anomalias": anomalias_restantes,
                    "score": curation_meta.get("score", 0.0),
                }
                aun_sospechosas.append(fila_corregida)
            else:
                _limpiar_metadatos_internos(fila_corregida)
                curadas.append(fila_corregida)
                stats.curadas_programatico += 1

        logger.info(
            "[CurationOrchestrator] Correctores programáticos: %d resueltas, %d al agente LLM.",
            len(curadas), len(aun_sospechosas),
        )
        return curadas, aun_sospechosas

    # ------------------------------------------------------------------
    # Capa 2b: agente curador LLM
    # ------------------------------------------------------------------

    async def _invoke_agent(
        self,
        filas_sospechosas: list[dict],
        stats: CurationStats,
    ) -> tuple[list[dict], list[dict]]:
        """
        Invoca al MetadataCuratorAgent con las filas que no pudieron corregirse
        programáticamente. Procesa todas en un único batch para minimizar overhead.

        Args:
            filas_sospechosas: Filas pre-procesadas por los correctores.
            stats:             Estadísticas (modificadas in-place para registrar errores).

        Returns:
            Tupla (curadas, marcadas_revision).
        """
        try:
            agente = await build_metadata_curator_agent()
            mensaje = construir_mensaje_curacion(filas_sospechosas)

            logger.info(
                "[CurationOrchestrator] Invocando agente curador con %d filas...",
                len(filas_sospechosas),
            )

            resultado = await agente.ainvoke(
                {"messages": [mensaje]},
                config={"recursion_limit": 15},
            )

            respuesta_texto = self._extract_agent_response(resultado)
            correcciones = parsear_respuesta_agente(respuesta_texto)

            return self._apply_agent_corrections(filas_sospechosas, correcciones, stats)

        except Exception as exc:
            logger.error(
                "[CurationOrchestrator] Error invocando agente curador: %s", exc, exc_info=True
            )
            stats.errores += len(filas_sospechosas)
            return [], self._mark_all_as_error(filas_sospechosas, exc)

    @staticmethod
    def _extract_agent_response(resultado: dict) -> str:
        """Extrae el último mensaje de texto de la respuesta del agente."""
        mensajes = resultado.get("messages", [])
        for msg in reversed(mensajes):
            if hasattr(msg, "content") and isinstance(msg.content, str):
                return msg.content
        return ""

    def _apply_agent_corrections(
        self,
        filas_sospechosas: list[dict],
        correcciones: list[dict],
        stats: CurationStats,
    ) -> tuple[list[dict], list[dict]]:
        """
        Aplica las correcciones del agente sobre las filas originales.

        Para cada fila, busca la corrección correspondiente por ID y la aplica.
        Las filas sin corrección o con campos marcados como `_curation_needed`
        se envían a cuarentena.
        """
        correcciones_por_id = {c.get("id", ""): c for c in correcciones if c.get("id")}
        curadas: list[dict] = []
        marcadas: list[dict] = []

        for fila in filas_sospechosas:
            fila_resultado = dict(fila)
            _limpiar_metadatos_internos(fila_resultado)

            correccion = correcciones_por_id.get(fila.get("id", ""), {})

            if correccion:
                fila_resultado = self._merge_correction(fila_resultado, correccion)

                if self._has_curation_needed_flags(correccion):
                    fila_resultado["curation_needed"] = True
                    marcadas.append(fila_resultado)
                    stats.marcadas_revision += 1
                else:
                    curadas.append(fila_resultado)
                    stats.curadas_agente += 1
            else:
                fila_resultado["curation_needed"] = True
                fila_resultado["correction_notes"] = (
                    "El agente no pudo corregir este registro. Revisar manualmente."
                )
                marcadas.append(fila_resultado)
                stats.marcadas_revision += 1

        return curadas, marcadas

    @staticmethod
    def _merge_correction(fila: dict, correccion: dict) -> dict:
        """Aplica los campos corregidos por el agente sobre la fila, preservando las notas."""
        _EXCLUIR = {"id", "correction_notes", "_curation"}
        campos_a_actualizar = {
            k: v for k, v in correccion.items()
            if k not in _EXCLUIR and not k.endswith("_curation_needed")
        }
        fila.update(campos_a_actualizar)
        if "correction_notes" in correccion:
            fila["correction_notes"] = correccion["correction_notes"]
        return fila

    @staticmethod
    def _has_curation_needed_flags(correccion: dict) -> bool:
        """Verifica si el agente marcó algún campo como pendiente de revisión."""
        return any(
            k.endswith("_curation_needed") and v
            for k, v in correccion.items()
        )

    @staticmethod
    def _mark_all_as_error(filas: list[dict], exc: Exception) -> list[dict]:
        """Marca todas las filas como error cuando el agente falla completamente."""
        marcadas: list[dict] = []
        for fila in filas:
            fila_resultado = dict(fila)
            _limpiar_metadatos_internos(fila_resultado)
            fila_resultado["curation_needed"] = True
            fila_resultado["correction_notes"] = f"Error en agente curador: {exc}"
            marcadas.append(fila_resultado)
        return marcadas


# ---------------------------------------------------------------------------
# Helpers privados compartidos
# ---------------------------------------------------------------------------

def _limpiar_metadatos_internos(fila: dict) -> None:
    """Elimina in-place las claves internas de curación del dict de la fila."""
    for clave in _CLAVES_INTERNAS:
        fila.pop(clave, None)


# ---------------------------------------------------------------------------
# Alias de compatibilidad (importado por tests)
# ---------------------------------------------------------------------------

def _calcular_anomalias_restantes(
    fila_corregida: dict,
    anomalias_originales: dict[str, list[str]],
) -> dict[str, list[str]]:
    """
    Alias de compatibilidad para tests que importan esta función directamente.

    Delega en `AnomalyResolver.resolve()`.
    """
    return AnomalyResolver().resolve(fila_corregida, anomalias_originales)


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
            "pending_to_review_csv_path": None,
            "curation_stats": {"error": f"CSV no encontrado: {source_csv}"},
        }

    output_csv = os.path.join(workspace_dir, "curated_from_pdfs.csv")
    pending_csv = os.path.join(workspace_dir, "pending_to_review.csv")

    logger.info(
        "[CurateMetadata] Iniciando curación. CSV fuente: '%s', umbral: %.2f",
        source_csv, config.CURATION_ANOMALY_THRESHOLD,
    )

    registros = CsvHandler.read(source_csv)

    if not registros:
        logger.warning("[CurateMetadata] CSV vacío. Escribiendo sin cambios a '%s'.", output_csv)
        CsvHandler.write([], output_csv)
        return {
            "curated_csv_path": output_csv,
            "pending_to_review_csv_path": None,
            "curation_stats": CurationStats().to_dict(),
        }

    logger.info("[CurateMetadata] Leídos %d registros desde '%s'.", len(registros), source_csv)

    orchestrator = CurationOrchestrator(threshold=config.CURATION_ANOMALY_THRESHOLD)
    result = await orchestrator.run(registros)

    CsvHandler.write(result.curadas, output_csv)

    if result.pendientes:
        CsvHandler.write(result.pendientes, pending_csv)
        logger.warning(
            "[CurateMetadata] %d ítem(s) pendientes de revisión → '%s'. "
            "Requieren revisión manual antes de continuar.",
            len(result.pendientes), pending_csv,
        )
    else:
        pending_csv = None
        logger.info("[CurateMetadata] Ningún ítem requiere revisión manual.")

    return result.to_state_update(output_csv, pending_csv)
