"""
Agente de Curación de Metadatos PDF.

Agente LangGraph de la Capa 2 del pipeline de curación. Recibe filas
de metadatos pre-clasificadas como sospechosas por la Capa 1 (detectores
heurísticos) y aplica correcciones usando tools especializadas.

Herramientas disponibles:
    - re_extract_with_ocr:      Re-extrae el PDF con OCR (Tesseract + PyMuPDF).
    - validate_with_enrichers:  Consulta Crossref/OpenAlex para validar/completar.

Diseño deliberadamente conservador: el agente solo interviene en campos
con anomalías claras; si no puede corregir con certeza, marca el campo
como `curation_needed` en lugar de inventar valores.

Flujo del agente:
    START → metadata_curator_node → tools (si necesario) → metadata_curator_node → END
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from core.clients.enrichers.provider_factory import EnricherFactory
from core.utils.config import config
from core.utils.get_local_model import FallbackLLM
from core.utils.prompt_loader import load_agent_prompt


logger = logging.getLogger(__name__)

def _construir_modelo_curador() -> FallbackLLM:
    """
    Instancia el modelo LLM para el agente curador con fallback automático.
    """
    return FallbackLLM(
        groq_model=config.METADATA_CURATOR_MODEL,
        nvidia_model=config.METADATA_CURATOR_MODEL,
        openrouter_model=config.METADATA_CURATOR_MODEL,
    ).resolve()



# ---------------------------------------------------------------------------
# Estado del agente curador
# ---------------------------------------------------------------------------

class MetadataCuratorState(TypedDict):
    """Estado del agente curador de metadatos PDF."""
    messages: Annotated[list[BaseMessage], add_messages]


# ---------------------------------------------------------------------------
# Definición de tools del agente curador
# ---------------------------------------------------------------------------

@tool
def re_extract_with_ocr(pdf_path: str) -> dict[str, Any]:
    """
    Re-extrae metadatos de un PDF habilitando OCR para manejo de imágenes
    y texto no seleccionable.

    Estrategia de dos pasos:
      1. PyMuPDF con extracción por bloques (mantiene estructura de layout).
      2. Si falla o el resultado es pobre, aplica Tesseract OCR por página.

    Usar cuando:
      - El título o campos clave tienen CHARS_DISPERSOS (texto en columnas OCR).
      - El abstract tiene TEXTO_PEGADO grave irrecuperable con heurísticas.

    Args:
        pdf_path: Ruta absoluta o nombre del archivo PDF (ej. "39-jaiio-ast-06.pdf-PDFA.pdf").

    Returns:
        Dict con los metadatos re-extraídos. Incluye `"ocr_applied": true` si se usó Tesseract.
        Devuelve `{"error": "..."}` si el PDF no está disponible.
    """
    import os
    import io

    # Intentar con PyMuPDF primero (extracción estructurada por bloques)
    try:
        import fitz  # PyMuPDF

        # Verificar rutas posibles del PDF
        rutas_candidatas = [
            pdf_path,
            os.path.join("/tmp", pdf_path),
        ]

        doc = None
        ruta_usada = None
        for ruta in rutas_candidatas:
            if os.path.isfile(ruta):
                doc = fitz.open(ruta)
                ruta_usada = ruta
                break

        if doc is None:
            return {
                "error": f"PDF no encontrado en rutas: {rutas_candidatas}",
                "ocr_applied": False,
            }

        # Extraer texto de las primeras 3 páginas (suficiente para metadatos)
        textos: list[str] = []
        for i, page in enumerate(doc):
            if i >= 3:
                break
            texto_pagina = page.get_text("text")  # type: ignore
            if texto_pagina.strip():
                textos.append(texto_pagina)

        doc.close()
        texto_completo = "\n".join(textos)

        if texto_completo.strip():
            logger.info("[MetadataCuratorAgent] PyMuPDF extrajo %d chars de '%s'", len(texto_completo), pdf_path)
            return {
                "texto_extraido": texto_completo[:3000],  # Limitar para no saturar contexto
                "ocr_applied": False,
                "metodo": "pymupdf_blocks",
            }

    except ImportError:
        logger.warning("[MetadataCuratorAgent] PyMuPDF no disponible, intentando Tesseract")
    except Exception as exc:
        logger.warning("[MetadataCuratorAgent] PyMuPDF falló: %s. Intentando Tesseract.", exc)

    # Fallback: Tesseract OCR
    try:
        import pytesseract
        from PIL import Image
        import fitz  # PyMuPDF necesario también para renderizar páginas

        rutas_candidatas = [pdf_path, os.path.join("/tmp", pdf_path)]
        ruta_usada = next((r for r in rutas_candidatas if os.path.isfile(r)), None)

        if not ruta_usada:
            return {
                "error": f"PDF no disponible para OCR: {pdf_path}",
                "ocr_applied": False,
            }

        doc = fitz.open(ruta_usada)
        textos_ocr: list[str] = []

        for i, page in enumerate(doc):
            if i >= 2:  # Solo primeras 2 páginas para OCR (más costoso)
                break
            # Renderizar página como imagen a 200 DPI
            pix = page.get_pixmap(dpi=200)  # type: ignore
            img_bytes = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_bytes))
            texto = pytesseract.image_to_string(img, lang="spa+eng")
            if texto.strip():
                textos_ocr.append(texto)

        doc.close()
        texto_ocr = "\n".join(textos_ocr)

        logger.info(
            "[MetadataCuratorAgent] Tesseract OCR extrajo %d chars de '%s'",
            len(texto_ocr), pdf_path,
        )
        return {
            "texto_extraido": texto_ocr[:3000],
            "ocr_applied": True,
            "metodo": "tesseract_ocr",
        }

    except ImportError:
        return {
            "error": "Tesseract (pytesseract) no instalado. Instalar con: pip install pytesseract",
            "ocr_applied": False,
        }
    except Exception as exc:
        logger.error("[MetadataCuratorAgent] Error en Tesseract OCR: %s", exc)
        return {"error": str(exc), "ocr_applied": False}


@tool
def validate_with_enrichers(
    strategy: str,
    doi: str = "",
    issn: str = "",
    title: str = "",
) -> dict[str, Any]:
    """
    Consulta la fuente bibliográfica más apropiada para obtener metadatos
    autoritativos del ítem y compararlos/completar los extraídos del PDF.

    Estrategias disponibles (elegir la más pertinente según los campos disponibles):
      - "by_doi":   Consulta Crossref usando el DOI. Más precisa. Usar si el DOI es legible.
      - "by_issn":  Consulta OpenAlex usando el ISSN. Usar si no hay DOI pero hay ISSN.
      - "by_title": Consulta OpenAlex usando el título. Baja confianza; solo si el título es legible.

    NO llamar si los identificadores también están corruptos.
    NO llamar más de una vez por registro.

    Args:
        strategy: Una de "by_doi", "by_issn", "by_title".
        doi:      DOI del documento (requerido si strategy="by_doi").
        issn:     ISSN de la revista (requerido si strategy="by_issn").
        title:    Título del documento (requerido si strategy="by_title").

    Returns:
        Dict con los metadatos encontrados en la fuente autoritativa, o
        `{"found": false, "reason": "..."}` si no se encontró el ítem.
    """
    strategy = strategy.lower().strip()

    try:
        if strategy == "by_doi" and doi:
            enricher = EnricherFactory.create("crossref")
            resultado = enricher.enrich_by_doi(doi)
            if resultado:
                return {"found": True, "strategy": "crossref_doi", "data": resultado}
            # Fallback a DOI Negotiation si Crossref no lo tiene
            enricher_dn = EnricherFactory.create("doi_negotiation")
            resultado_dn = enricher_dn.enrich_by_doi(doi)
            if resultado_dn:
                return {"found": True, "strategy": "doi_negotiation", "data": resultado_dn}
            return {"found": False, "reason": f"DOI '{doi}' no encontrado en Crossref ni DOI Negotiation"}

        elif strategy == "by_issn" and issn:
            enricher = EnricherFactory.create("openalex")
            resultado = enricher.enrich_by_issn(issn)
            if resultado:
                return {"found": True, "strategy": "openalex_issn", "data": resultado}
            return {"found": False, "reason": f"ISSN '{issn}' no encontrado en OpenAlex"}

        elif strategy == "by_title" and title:
            enricher = EnricherFactory.create("openalex")
            resultado = enricher.enrich_by_title(title)
            if resultado:
                return {"found": True, "strategy": "openalex_title", "data": resultado}
            return {"found": False, "reason": f"Título no encontrado en OpenAlex: '{title[:60]}'"}

        else:
            return {
                "found": False,
                "reason": f"Estrategia '{strategy}' no válida o parámetros vacíos. "
                          "Usar: by_doi (con doi), by_issn (con issn), by_title (con title).",
            }

    except Exception as exc:
        logger.error("[MetadataCuratorAgent] Error en validate_with_enrichers: %s", exc)
        return {"found": False, "reason": f"Error consultando enricher: {exc}"}


# ---------------------------------------------------------------------------
# Construcción del agente curador
# ---------------------------------------------------------------------------

_CURATOR_TOOLS = [re_extract_with_ocr, validate_with_enrichers]


async def build_metadata_curator_agent():
    """
    Construye el grafo del agente curador de metadatos.

    Selecciona el modelo LLM con fallback automático (Groq → Nvidia → OpenRouter)
    usando `_construir_modelo_curador()`. Vincula las dos tools de curación:
    OCR y validación con enrichers.

    Returns:
        Grafo compilado listo para ser invocado con un HumanMessage.
    """
    curator_model = _construir_modelo_curador().bind_tools(tools=_CURATOR_TOOLS)

    async def metadata_curator_node(state: MetadataCuratorState) -> dict:
        """
        Nodo principal del agente curador.

        Recibe el estado con los mensajes del usuario (la lista de filas
        sospechosas en JSON), construye el prompt del sistema y llama al
        modelo con las herramientas disponibles.
        """
        sys_msg = SystemMessage(content=load_agent_prompt("metadata_curator_agent"))
        prompt = [sys_msg] + state["messages"]
        response = await curator_model.ainvoke(prompt)
        return {"messages": [response]}

    workflow = StateGraph(MetadataCuratorState)

    workflow.add_node("metadata_curator", metadata_curator_node)
    workflow.add_node("tools", ToolNode(tools=_CURATOR_TOOLS))

    workflow.add_edge(START, "metadata_curator")
    workflow.add_conditional_edges("metadata_curator", tools_condition)
    workflow.add_edge("tools", "metadata_curator")

    return workflow.compile(name="MetadataCuratorGraph")



# ---------------------------------------------------------------------------
# Helper: construir el mensaje de entrada al agente
# ---------------------------------------------------------------------------

def construir_mensaje_curacion(filas_sospechosas: list[dict]) -> HumanMessage:
    """
    Construye el HumanMessage de entrada al agente curador con las filas
    sospechosas serializadas en JSON compacto.

    Incluye solo los campos relevantes para minimizar el uso de la ventana
    de contexto: se excluyen campos con valor vacío y se mantienen los
    metadatos de curación (_curation) para que el agente sepa qué buscar.

    Args:
        filas_sospechosas: Lista de dicts con anomalías pre-clasificadas.

    Returns:
        HumanMessage con las filas en formato JSON compacto.
    """
    # Serializar compacto: excluir campos vacíos para ahorrar tokens
    filas_compactas = []
    for fila in filas_sospechosas:
        fila_compacta = {
            k: v for k, v in fila.items()
            if v not in (None, "", [], {}) or k in ("id", "_curation", "_corrections_applied")
        }
        filas_compactas.append(fila_compacta)

    contenido = (
        f"Curá las siguientes {len(filas_compactas)} filas de metadatos.\n"
        f"Cada fila incluye `_curation.anomalias` con las anomalías detectadas "
        f"y `_corrections_applied` con las correcciones ya aplicadas automáticamente.\n\n"
        f"```json\n{json.dumps(filas_compactas, ensure_ascii=False, indent=2)}\n```"
    )

    return HumanMessage(content=contenido)


# ---------------------------------------------------------------------------
# Helper: parsear la respuesta del agente
# ---------------------------------------------------------------------------

def parsear_respuesta_agente(respuesta_texto: str) -> list[dict]:
    """
    Extrae el JSON de correcciones de la respuesta textual del agente.

    El agente puede devolver el JSON dentro de un bloque ```json ... ```
    o directamente como texto. Se intenta parsear de ambas formas.

    Args:
        respuesta_texto: Texto de la respuesta final del agente.

    Returns:
        Lista de dicts con las correcciones por ítem. Lista vacía si
        no se pudo parsear la respuesta.
    """
    # Intentar extraer bloque JSON de markdown
    import re
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", respuesta_texto, re.DOTALL)
    if match:
        try:
            resultado = json.loads(match.group(1))
            if isinstance(resultado, list):
                return resultado
            if isinstance(resultado, dict):
                return [resultado]
        except json.JSONDecodeError:
            pass

    # Intentar parsear directamente
    try:
        resultado = json.loads(respuesta_texto.strip())
        if isinstance(resultado, list):
            return resultado
        if isinstance(resultado, dict):
            return [resultado]
    except json.JSONDecodeError:
        pass

    logger.warning(
        "[MetadataCuratorAgent] No se pudo parsear la respuesta del agente como JSON. "
        "Respuesta (primeros 200 chars): %s",
        respuesta_texto[:200],
    )
    return []
