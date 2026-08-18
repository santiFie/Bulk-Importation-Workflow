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

from langchain_core.messages import HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient

from core.state import State
from core.utils.config import config
from core.agent.metadata_extractor_agent import build_metadata_extractor_workflow

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
      1. Lista los objetos PDF en minio_bucket/minio_prefix vía el MCP de MinIO.
      2. Para cada PDF, invoca el MetadataExtractorAgent vía el MCP del
         Orchestrator para obtener metadatos estructurados.
      3. Agrega todos los metadatos extraídos en un CSV y lo escribe en
         workspace_dir/source_from_pdfs.csv.
      4. Actualiza state["source_csv_path"] con la ruta al CSV generado.

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

    # ── 1. Conectar al MetadataExtractor MCP ─────────────────────────────────
    extractor_client = MultiServerMCPClient({
        "OrchestratorMCP": {
            "url": config.ORCHESTRATOR_MCP_URL,
            "transport": "streamable_http",
        }
    })
    extractor_tools = await extractor_client.get_tools(server_name="OrchestratorMCP")
    extractor_graph = await build_metadata_extractor_workflow(extractor_tools)

    # ── 2. Conectar al MinIO MCP para listar los PDFs ────────────────────────
    minio_client = MultiServerMCPClient({
        "aistor": {
            "command": "docker",
            "args": [
                "run", "-i", "--rm", "--network=host",
                "-v", f"{config.DOWNLOADS_DIR}:/Downloads",
                "-e", "MINIO_ENDPOINT=localhost:9003",
                "-e", f"MINIO_ACCESS_KEY={config.MINIO_ROOT_USER}",
                "-e", f"MINIO_SECRET_KEY={config.MINIO_ROOT_PASSWORD}",
                "-e", "MINIO_USE_SSL=false",
                "quay.io/minio/aistor/mcp-server-aistor:latest",
                "--allowed-directories", "/Downloads",
                "--allow-write",
            ],
            "transport": "stdio",
        }
    })

    minio_tools = await minio_client.get_tools(server_name="aistor")

    # ── 3. Listar objetos PDF en el bucket ───────────────────────────────────
    list_prompt = (
        f"Lista todos los archivos PDF en el bucket '{minio_bucket}'"
        + (f" con prefijo '{minio_prefix}'" if minio_prefix else "")
        + ". Devuelve sólo los nombres (paths) de los objetos."
    )

    logger.info("[PDFIngest] Listando PDFs en MinIO...")
    list_response = await extractor_graph.ainvoke({
        "messages": [HumanMessage(content=list_prompt)]
    })
    pdf_list_raw = list_response["messages"][-1].content

    # Parsear la lista de PDFs (el agente devuelve texto; extrae los paths)
    pdf_paths = _parse_pdf_list(pdf_list_raw, minio_bucket, minio_prefix)
    logger.info("[PDFIngest] PDFs encontrados: %d", len(pdf_paths))

    if not pdf_paths:
        logger.warning("[PDFIngest] No se encontraron PDFs. Se devuelve CSV vacío.")
        _write_empty_csv(output_csv_path)
        return {"source_csv_path": output_csv_path}

    # ── 4. Extraer metadatos de cada PDF ────────────────────────────────────
    all_metadata: list[dict] = []
    errors: list[str] = []

    for pdf_path in pdf_paths:
        logger.info("[PDFIngest] Procesando: %s", pdf_path)
        try:
            extraction_prompt = (
                f"Descarga el archivo '{pdf_path}' del bucket '{minio_bucket}' "
                f"y extrae todos sus metadatos académicos (título, autores, "
                f"año, DOI, ISBN, ISSN, resumen, palabras clave, editorial)."
            )
            response = await extractor_graph.ainvoke({
                "messages": [HumanMessage(content=extraction_prompt)]
            })
            metadata = _parse_metadata_response(response["messages"][-1].content, pdf_path)
            all_metadata.append(metadata)
        except Exception as exc:
            logger.error("[PDFIngest] Error procesando '%s': %s", pdf_path, exc)
            errors.append(f"{pdf_path}: {exc}")

    # ── 5. Escribir CSV de salida ────────────────────────────────────────────
    _write_metadata_csv(all_metadata, output_csv_path)

    logger.info(
        "[PDFIngest] Completado. %d PDFs procesados, %d errores. CSV: '%s'",
        len(all_metadata), len(errors), output_csv_path,
    )

    return {
        "source_csv_path": output_csv_path,
        "node_errors": {**state.get("node_errors", {}), "PDFIngest_errors": "; ".join(errors)} if errors else state.get("node_errors", {}),
    }


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------

def _parse_pdf_list(raw_text: str, bucket: str, prefix: str) -> list[str]:
    """
    Extrae los paths de PDFs de la respuesta en texto libre del agente.
    Intenta parsear JSON si el agente lo devuelve; sino, línea por línea.
    """
    try:
        data = json.loads(raw_text)
        if isinstance(data, list):
            return [str(p) for p in data if str(p).lower().endswith(".pdf")]
    except (json.JSONDecodeError, TypeError):
        pass

    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    return [line for line in lines if line.lower().endswith(".pdf")]


def _parse_metadata_response(raw_text: str, pdf_path: str) -> dict:
    """
    Intenta parsear la respuesta de extracción de metadatos como JSON.
    Si falla, devuelve un diccionario mínimo con el path del PDF.
    """
    try:
        data = json.loads(raw_text)
        if isinstance(data, dict):
            data.setdefault("source_file", pdf_path)
            return data
    except (json.JSONDecodeError, TypeError):
        pass

    return {"source_file": pdf_path, "raw_extraction": raw_text[:500]}


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
