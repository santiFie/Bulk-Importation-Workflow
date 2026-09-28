"""
Modelos de datos para el sistema de Memoria Episódica (CBR).

Define las estructuras para serializar, almacenar y recuperar episodios
de errores y sus correspondientes resoluciones.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, Field


class EpisodeMetadata(BaseModel):
    """Metadatos deterministas y filtrables de un episodio de error."""

    tool: str = Field(description="Nombre de la herramienta donde ocurrió el error (ej. 'deduplicator', 'crosswalk').")
    source_name: str = Field(description="Repositorio o fuente de datos de origen (ej. 'unlp_doaj', 'scopus').")
    error_class: str = Field(description="Tipo o clase de excepción (ej. 'ValueError', 'KeyError').")
    affected_column: str = Field(default="", description="Columna del CSV donde se localizó la anomalía.")
    resolved_by: Literal["autonomous", "hitl"] = Field(
        default="autonomous",
        description="Si la solución fue generada y validada por el agente o intervenida por el operador.",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Fecha y hora de creación del episodio en formato ISO 8601.",
    )


class EpisodePayload(BaseModel):
    """Detalle analítico y solución aplicada para resolver la falla."""

    error_summary: str = Field(description="Explicación concisa y en lenguaje claro del problema.")
    culprit_sample: str = Field(default="", description="Ejemplo representativo del valor que originó el error.")
    transformation_strategy: str = Field(
        description="Nombre de la estrategia aplicada (ej. 'value_mapping', 'regex_extraction', 'date_to_iso')."
    )
    solution_applied: dict[str, Any] = Field(
        default_factory=dict,
        description="Parámetros técnicos exactos de la solución (ej. mapeos, regex, formatos de salida).",
    )
    reasoning: str = Field(default="", description="Explicación de por qué esta solución resuelve la causa raíz.")


class Episode(BaseModel):
    """Representación integral de un episodio para indexación vectorial."""

    id: str = Field(description="Identificador único del episodio (ej. 'ep_dedup_20260925_001').")
    metadata: EpisodeMetadata
    embedding_content: str = Field(
        description="Texto semántico estructurado sobre el cual se calcula el vector de embedding."
    )
    payload: EpisodePayload


class EpisodeQueryResult(BaseModel):
    """Resultado de una búsqueda en la memoria episódica con métrica de similitud."""

    episode: Episode
    similarity_score: float = Field(
        description="Puntaje de similitud coseno o distancia normalizada (entre 0.0 y 1.0)."
    )
