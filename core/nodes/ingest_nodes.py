"""
Nodos del subgrafo de Ingesta.

Responsable de normalizar distintas fuentes de entrada (CSV directo o
PDFs almacenados en MinIO) a un CSV de metadatos unificado que el resto
del pipeline pueda consumir.

Flujos soportados:
  - csv:       state["source_csv_path"] ya está definido → pasa directamente.
  - pdf_minio: descarga PDFs de MinIO, extrae metadatos vía el
               MetadataExtractorAgent y genera un CSV en el workspace.
"""

import csv
import json
import logging
import os
from typing import Any

import httpx
from minio import Minio

from core.state import State
from core.utils.config import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Función de enrutamiento (usada como conditional_edge en el subgrafo)
# ---------------------------------------------------------------------------

def route_input_source(state: State) -> str:
    """
    Determina el camino de ingesta según el tipo de fuente de entrada.

    Returns:
        "csv"       → fuente ya es un CSV, pasa directo al CrosswalkDedup.
        "pdf_minio" → PDFs en MinIO, requiere extracción de metadatos.
    """
    return state.get("input_source_type", "csv")


# ---------------------------------------------------------------------------
# Nodo de ingesta desde MinIO (pdf_minio)
# ---------------------------------------------------------------------------

async def pdf_ingest_node(state: State) -> dict[str, Any]:
    """
    Nodo PDFIngest — Descarga PDFs desde MinIO y extrae sus metadatos.

    Flujo interno:
      1. Lista los objetos PDF en minio_bucket/minio_prefix vía MinIO SDK.
      2. Para cada PDF, descarga el archivo en memoria.
      3. Envía el PDF a la API del Orchestrator para extracción de metadatos.
      4. Agrega todos los metadatos extraídos en un CSV y lo escribe en
         workspace_dir/source_from_pdfs.csv.
      5. Actualiza state["source_csv_path"] con la ruta al CSV generado.

    Requiere en el estado:
      - workspace_dir:  directorio de trabajo del lote (creado por SetupWorkspace).
      - minio_bucket:   nombre del bucket de MinIO.
      - minio_prefix:   prefijo (carpeta) dentro del bucket (puede estar vacío).

    Raises:
        ValueError: si minio_bucket no está definido en el estado.
    """
    workspace_dir = state["workspace_dir"]
    minio_bucket = state.get("minio_bucket", "")
    minio_prefix = state.get("minio_prefix", "")

    if not minio_bucket:
        raise ValueError("[PDFIngest] 'minio_bucket' no está definido en el estado.")

    output_csv_path = os.path.join(workspace_dir, "source_from_pdfs.csv")

    logger.info(
        "[PDFIngest] Iniciando ingesta desde MinIO. Bucket: '%s', Prefijo: '%s'",
        minio_bucket, minio_prefix,
    )

    # ── 1. Configurar MinIO Client ───────────────────────────────────────────
    minio_client = Minio(
        "localhost:9003",
        access_key=config.MINIO_ROOT_USER,
        secret_key=config.MINIO_ROOT_PASSWORD,
        secure=False
    )

    # ── 2. Listar objetos PDF en el bucket ───────────────────────────────────
    logger.info("[PDFIngest] Listando PDFs en MinIO...")
    
    try:
        objects = minio_client.list_objects(minio_bucket, prefix=minio_prefix, recursive=True)
        pdf_paths = [obj.object_name for obj in objects if obj.object_name.lower().endswith('.pdf')]
    except Exception as exc:
        logger.error("[PDFIngest] Error al listar objetos en MinIO: %s", exc)
        _write_empty_csv(output_csv_path)
        return {
            "source_csv_path": output_csv_path,
            "node_errors": {**state.get("node_errors", {}), "PDFIngest_errors": f"Error listando en MinIO: {exc}"},
        }

    logger.info("[PDFIngest] PDFs encontrados: %d", len(pdf_paths))

    if not pdf_paths:
        logger.warning("[PDFIngest] No se encontraron PDFs. Se devuelve CSV vacío.")
        _write_empty_csv(output_csv_path)
        return {"source_csv_path": output_csv_path}

    # ── 3. Extraer metadatos de cada PDF ──────────────────────
    all_metadata: list[dict] = []
    errors: list[str] = []

    for pdf_path in pdf_paths:
        logger.info("[PDFIngest] Procesando: %s", pdf_path)
        try:
            # Obtener el archivo desde MinIO
            response = minio_client.get_object(minio_bucket, pdf_path)
            file_bytes = response.read()
            response.close()
            response.release_conn()

            # Extraer el nombre de archivo
            filename = os.path.basename(pdf_path)
            if not filename:
                filename = "document.pdf"

            files = {"file": (filename, file_bytes)}
            data = {
                "normalization": "true",
                "type": "None",
                "deepanalyze": "false",
                "ocr": "false"
            }

            headers = {}
            headers["Authorization"] = f"Bearer {config.METADATA_EXTRACTOR_API_KEY}"

            metadata_extractor_url = config.METADATA_EXTRACTOR_API_URL
            
            # Enviar al orquestador backend
            api_resp = httpx.post(
                f"{metadata_extractor_url}/upload",
                headers=headers,
                files=files,
                data=data,
                timeout=120
            )
            api_resp.raise_for_status()
            metadata = api_resp.json()["data"]
            
            mapped_metadata = {
                "id": pdf_path,
                "title": metadata.get("title", ""),
                "author": "|".join(metadata.get("creator", []) + metadata.get("director", [])),
                "description": metadata.get("abstract", ""),
                "date": metadata.get("date", ""),
                "type": metadata.get("type", ""),
                "subject": metadata.get("subject", ""),
                "issn": metadata.get("issn", ""),
                "isbn": metadata.get("isbn", ""),
                "doi": metadata.get("doi", ""),
                "citation": metadata.get("originPlaceInfo", ""),
                "rights": metadata.get("rights", ""),
                "rightsurl": metadata.get("rightsurl", ""),
            }

            all_metadata.append(mapped_metadata)

        except Exception as exc:
            logger.error("[PDFIngest] Error procesando '%s': %s", pdf_path, exc)
            errors.append(f"{pdf_path}: {exc}")

    _write_metadata_csv(all_metadata, output_csv_path)

    logger.info(
        "[PDFIngest] Completado. %d PDFs procesados, %d errores. CSV: '%s'",
        len(all_metadata), len(errors), output_csv_path,
    )

    return {
        "source_csv_path": output_csv_path,
        "node_errors": {**state.get("node_errors", {}), "PDFIngest_errors": "; ".join(errors)} if errors else state.get("node_errors", {}),
    }


def _write_metadata_csv(records: list[dict], output_path: str) -> None:
    """Escribe la lista de metadatos en un archivo CSV."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    if not records:
        _write_empty_csv(output_path)
        return

    # Unión de todas las claves presentes para usar como header
    all_keys: list[str] = []
    for record in records:
        for key in record:
            if key not in all_keys:
                all_keys.append(key)

    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=all_keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def _write_empty_csv(output_path: str) -> None:
    """Escribe un CSV vacío con un header mínimo."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["title", "author", "year", "doi", "source_file"])
