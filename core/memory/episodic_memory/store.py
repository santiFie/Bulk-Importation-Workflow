"""
Almacén vectorial para la Memoria Episódica utilizando ChromaDB local.

Gestiona la persistencia de episodios de error y su recuperación
semántica empleando sentence-transformers (all-MiniLM-L6-v2) de forma local.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

import chromadb
from chromadb.utils import embedding_functions

from core.memory.episodic_memory.models import (
    Episode,
    EpisodeMetadata,
    EpisodePayload,
    EpisodeQueryResult,
)

logger = logging.getLogger(__name__)

# Directorio por defecto de persistencia en el repositorio
DEFAULT_EPISODIC_DIR = Path(__file__).resolve().parents[3] / "data" / "episodic_memory" / "chroma_db"
COLLECTION_NAME = "episodic_error_memory"
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"


class EpisodicMemoryStore:
    """
    Cliente de almacenamiento y recuperación de episodios en ChromaDB local.
    """

    def __init__(
        self,
        persist_directory: Optional[Path | str] = None,
        embedding_model: str = EMBEDDING_MODEL_NAME,
    ) -> None:
        self.persist_directory = Path(persist_directory or DEFAULT_EPISODIC_DIR)
        self.persist_directory.mkdir(parents=True, exist_ok=True)

        self._embedding_function = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=embedding_model
        )

        self._client = chromadb.PersistentClient(path=str(self.persist_directory))
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=self._embedding_function,
            metadata={"hnsw:space": "cosine"},
        )

    def count(self) -> int:
        """Devuelve la cantidad de episodios indexados."""
        return self._collection.count()

    def save_episode(self, episode: Episode) -> str:
        """
        Indexa un nuevo episodio en la base de datos vectorial.

        Args:
            episode: Instancia completa del episodio a registrar.

        Returns:
            ID del episodio persistido.
        """
        metadata_dict: dict[str, Any] = {
            "tool": episode.metadata.tool,
            "source_name": episode.metadata.source_name,
            "error_class": episode.metadata.error_class,
            "affected_column": episode.metadata.affected_column,
            "resolved_by": episode.metadata.resolved_by,
            "timestamp": episode.metadata.timestamp,
            "payload_json": episode.payload.model_dump_json(),
        }

        self._collection.upsert(
            ids=[episode.id],
            documents=[episode.embedding_content],
            metadatas=[metadata_dict],
        )

        logger.info(
            "[EpisodicMemoryStore] Episodio '%s' guardado exitosamente (tool='%s', column='%s').",
            episode.id,
            episode.metadata.tool,
            episode.metadata.affected_column,
        )
        return episode.id

    def search_episodes(
        self,
        query_text: str,
        filter_metadata: Optional[dict[str, Any]] = None,
        top_k: int = 2,
    ) -> list[EpisodeQueryResult]:
        """
        Busca episodios semánticamente similares aplicando filtros deterministas.

        Args:
            query_text: Texto representativo del error y contexto.
            filter_metadata: Diccionario para filtrado estricto en ChromaDB (ej. {'tool': 'deduplicator'}).
            top_k: Cantidad máxima de resultados a retornar.

        Returns:
            Lista de EpisodeQueryResult ordenados por mayor similitud.
        """
        if self._collection.count() == 0:
            return []

        where_clause = filter_metadata if filter_metadata else None

        results = self._collection.query(
            query_texts=[query_text],
            n_results=top_k,
            where=where_clause,
        )

        query_results: list[EpisodeQueryResult] = []
        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0] if "distances" in results else [0.0] * len(ids)

        for ep_id, doc, meta, dist in zip(ids, documents, metadatas, distances):
            try:
                payload_dict = json.loads(meta.get("payload_json", "{}"))
                payload = EpisodePayload.model_validate(payload_dict)

                metadata = EpisodeMetadata(
                    tool=str(meta.get("tool", "")),
                    source_name=str(meta.get("source_name", "")),
                    error_class=str(meta.get("error_class", "")),
                    affected_column=str(meta.get("affected_column", "")),
                    resolved_by=meta.get("resolved_by", "autonomous"),
                    timestamp=str(meta.get("timestamp", "")),
                )

                episode = Episode(
                    id=ep_id,
                    metadata=metadata,
                    embedding_content=doc,
                    payload=payload,
                )

                # Para distancia coseno en Chroma, similitud = 1.0 - distancia
                similarity = max(0.0, min(1.0, 1.0 - float(dist)))

                query_results.append(
                    EpisodeQueryResult(
                        episode=episode,
                        similarity_score=similarity,
                    )
                )
            except Exception as exc:
                logger.warning(
                    "[EpisodicMemoryStore] Error deserializando episodio '%s': %s",
                    ep_id,
                    exc,
                )

        return query_results

    def seed_default_episodes(self, force: bool = False) -> int:
        """
        Puebla la memoria episódica con casos iniciales de referencia si está vacía.

        Args:
            force: Si es True, fuerza la inserción de los casos semilla aun si ya existen.

        Returns:
            Cantidad de episodios insertados.
        """
        if self._collection.count() > 0 and not force:
            return 0

        seed_cases = _get_default_seed_episodes()
        for ep in seed_cases:
            self.save_episode(ep)

        logger.info("[EpisodicMemoryStore] %d episodios semilla insertados.", len(seed_cases))
        return len(seed_cases)


# TODO: Analizar default episodes
def _get_default_seed_episodes() -> list[Episode]:
    """Retorna casos de error conocidos y documentados para arranque del pipeline."""
    return [
        Episode(
            id="seed_dedup_date_english_month",
            metadata=EpisodeMetadata(
                tool="deduplicator",
                source_name="unlp_doaj",
                error_class="ValueError",
                affected_column="date",
                resolved_by="autonomous",
            ),
            embedding_content=(
                "Tool: deduplicator. Error: Error al comparar fechas: 1999, June — "
                "invalid literal for int() with base 10: 'June'. Column: date. Sample: '1999, June'"
            ),
            payload=EpisodePayload(
                error_summary="El comparador de fechas del deduplicador falló porque contenía nombres de mes en inglés en lugar de formato numérico.",
                culprit_sample="1999, June",
                transformation_strategy="value_mapping",
                solution_applied={
                    "type": "month_name_to_iso",
                    "mapping": {
                        "January": "01", "February": "02", "March": "03", "April": "04",
                        "May": "05", "June": "06", "July": "07", "August": "08",
                        "September": "09", "October": "10", "November": "11", "December": "12",
                    },
                    "target_format": "YYYY-MM",
                },
                reasoning="Se extrae el año numérico de 4 dígitos y se mapea el nombre textual del mes a su ordinal de dos dígitos (YYYY-MM).",
            ),
        ),
        Episode(
            id="seed_dedup_date_spanish_month",
            metadata=EpisodeMetadata(
                tool="deduplicator",
                source_name="unlp_doaj",
                error_class="ValueError",
                affected_column="date",
                resolved_by="autonomous",
            ),
            embedding_content=(
                "Tool: deduplicator. Error: Error al comparar fechas: 2004, Julio — "
                "invalid literal for int() with base 10: 'Julio'. Column: date. Sample: '2004, Julio'"
            ),
            payload=EpisodePayload(
                error_summary="El comparador de fechas del deduplicador falló por nombres de meses en español en formato texto.",
                culprit_sample="2004, Julio",
                transformation_strategy="value_mapping",
                solution_applied={
                    "type": "month_name_to_iso",
                    "mapping": {
                        "Enero": "01", "Febrero": "02", "Marzo": "03", "Abril": "04",
                        "Mayo": "05", "Junio": "06", "Julio": "07", "Agosto": "08",
                        "Septiembre": "09", "Octubre": "10", "Noviembre": "11", "Diciembre": "12",
                    },
                    "target_format": "YYYY-MM",
                },
                reasoning="Se normalizan los meses en español a números de dos dígitos en formato ISO.",
            ),
        ),
        Episode(
            id="seed_dedup_date_month_range",
            metadata=EpisodeMetadata(
                tool="deduplicator",
                source_name="scopus",
                error_class="ValueError",
                affected_column="date",
                resolved_by="hitl",
            ),
            embedding_content=(
                "Tool: deduplicator. Error: Error al comparar fechas: June-July 1999 — "
                "invalid literal for int() with base 10: 'June-July'. Column: date. Sample: 'June-July 1999'"
            ),
            payload=EpisodePayload(
                error_summary="Rango bimensual textual en columna date.",
                culprit_sample="June-July 1999",
                transformation_strategy="regex_extraction",
                solution_applied={
                    "type": "regex_extract_year",
                    "pattern": r"\b(19\d\d|20\d\d)\b",
                    "group": 1,
                    "target_format": "YYYY",
                },
                reasoning="Cuando una publicación abarca un rango de meses (ej. Junio-Julio), la política institucional de SEDICI extrae el año canónico.",
            ),
        ),
    ]
