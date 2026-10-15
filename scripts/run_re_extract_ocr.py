"""
Script para ejecutar la herramienta 're_extract_with_ocr' sobre PDFs almacenados en MinIO.

Descarga los PDFs especificados del bucket 'importacion-jaio', ejecuta la herramienta
de extracción OCR del agente curador y consolida los resultados en un reporte Markdown
y un JSON estructurado.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from minio import Minio

# Asegurar que el directorio raíz esté en sys.path para importar módulos de core
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.agent.metadata_curator_agent import re_extract_with_ocr
from core.utils.config import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("re_extract_runner")

TARGET_BUCKET = "importacion-jaio"
TARGET_PDFS = [
    "39-jaiio-ast-03.pdf-PDFA.pdf",
    "39-jaiio-ast-06.pdf-PDFA.pdf",
    "39-jaiio-ast-10.pdf-PDFA.pdf",
]
MINIO_ENDPOINT = "localhost:9003"


def get_minio_client() -> Minio:
    """Inicializa y retorna el cliente MinIO configurado."""
    return Minio(
        MINIO_ENDPOINT,
        access_key=config.MINIO_ROOT_USER,
        secret_key=config.MINIO_ROOT_PASSWORD,
        secure=False,
    )


def download_pdf_from_minio(client: Minio, bucket_name: str, object_name: str, target_dir: str) -> str:
    """
    Descarga un archivo PDF de MinIO al directorio local especificado.
    
    Retorna la ruta absoluta al archivo descargado.
    """
    local_path = os.path.join(target_dir, object_name)
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    logger.info("Descargando '%s' desde bucket '%s'...", object_name, bucket_name)
    client.fget_object(bucket_name, object_name, local_path)
    return local_path


def generate_markdown_report(results: list[dict[str, Any]], output_path: str) -> None:
    """Genera un archivo Markdown con visualización clara de las extracciones."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    lines: list[str] = [
        "# Reporte de Extracción con `re_extract_with_ocr`",
        "",
        f"- **Bucket de origen:** `{TARGET_BUCKET}`",
        f"- **Total de archivos procesados:** {len(results)}",
        "",
        "## Resumen General",
        "",
        "| Archivo PDF | Método Utilizado | ¿OCR Aplicado? | Caracteres Extraídos | Estado |",
        "| :--- | :---: | :---: | :---: | :---: |",
    ]

    for item in results:
        filename = item["filename"]
        res = item.get("result", {})
        has_error = "error" in res
        status = "❌ Error" if has_error else "✅ Exitoso"
        method = res.get("metodo", "N/A")
        ocr_applied = "Sí" if res.get("ocr_applied") else "No"
        extracted_text = res.get("texto_extraido", "")
        char_count = len(extracted_text)

        lines.append(f"| `{filename}` | `{method}` | {ocr_applied} | {char_count:,} | {status} |")

    lines.append("")
    lines.append("---")
    lines.append("## Detalle de Extracciones por Documento")
    lines.append("")

    for item in results:
        filename = item["filename"]
        res = item.get("result", {})
        method = res.get("metodo", "N/A")
        ocr_applied = res.get("ocr_applied", False)
        error = res.get("error")
        text = res.get("texto_extraido", "")

        lines.append(f"### 📄 Documento: `{filename}`")
        lines.append(f"- **Método:** `{method}`")
        lines.append(f"- **OCR Aplicado:** {'Sí' if ocr_applied else 'No'}")

        if error:
            lines.append(f"- **Error:** `{error}`")
        else:
            lines.append(f"- **Longitud del texto:** {len(text)} caracteres")
            lines.append("")
            lines.append("#### Texto Extraído:")
            lines.append("```text")
            lines.append(text if text else "(No se extrajo texto)")
            lines.append("```")
        lines.append("")
        lines.append("---")
        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info("Reporte Markdown generado en: %s", output_path)


def main() -> None:
    """Función principal de orquestación del script."""
    client = get_minio_client()

    if not client.bucket_exists(TARGET_BUCKET):
        logger.error("El bucket '%s' no existe en MinIO.", TARGET_BUCKET)
        sys.exit(1)

    temp_dir = tempfile.mkdtemp(prefix="minio_ocr_")
    logger.info("Directorio temporal para descargas: %s", temp_dir)

    all_results: list[dict[str, Any]] = []

    for pdf_name in TARGET_PDFS:
        logger.info("--------------------------------------------------")
        logger.info("Procesando documento: %s", pdf_name)
        try:
            local_pdf_path = download_pdf_from_minio(client, TARGET_BUCKET, pdf_name, temp_dir)
            logger.info("Archivo descargado en: %s", local_pdf_path)

            logger.info("Ejecutando tool 're_extract_with_ocr'...")
            tool_output = re_extract_with_ocr.invoke({"pdf_path": local_pdf_path})

            logger.info(
                "Extracción completada. Método: '%s', OCR aplicado: %s",
                tool_output.get("metodo"),
                tool_output.get("ocr_applied"),
            )

            all_results.append({
                "filename": pdf_name,
                "local_path": local_pdf_path,
                "result": tool_output,
            })

        except Exception as exc:
            logger.error("Error procesando '%s': %s", pdf_name, exc, exc_info=True)
            all_results.append({
                "filename": pdf_name,
                "error": str(exc),
                "result": {"error": str(exc), "ocr_applied": False},
            })

    output_dir = os.path.join(str(ROOT_DIR), "data")
    md_output_path = os.path.join(output_dir, "output_re_extract_jaio.md")
    json_output_path = os.path.join(output_dir, "output_re_extract_jaio.json")

    # Guardar reporte Markdown
    generate_markdown_report(all_results, md_output_path)

    # Guardar archivo JSON
    with open(json_output_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    logger.info("Resultado JSON guardado en: %s", json_output_path)

    logger.info("==================================================")
    logger.info("Proceso finalizado con éxito.")
    logger.info("Archivos de salida generados:")
    logger.info("  1. Markdown: %s", md_output_path)
    logger.info("  2. JSON:     %s", json_output_path)
    logger.info("==================================================")


if __name__ == "__main__":
    main()
