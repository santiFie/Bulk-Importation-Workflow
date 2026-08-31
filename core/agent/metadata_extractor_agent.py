"""
Agente de Extracción de Metadatos.

Conecta con el MCP del orquestador de extracción (proyecto dockerizado externo)
para procesar documentos académicos (PDF, DOCX, ODS) y retornar sus metadatos
estructurados.

Flujo:
    START → metadata_extractor_node → tools (upload_document) → metadata_extractor_node → END
"""

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from core.utils.config import config
from core.utils.get_local_model import FallbackLLM
from core.utils.prompt_loader import load_agent_prompt


class MetadataExtractorState(TypedDict):
    """Estado del agente extractor de metadatos."""
    messages: Annotated[list[BaseMessage], add_messages]


async def build_metadata_extractor_workflow(tools: list):
    """
    Construye el grafo del agente extractor de metadatos.

    Recibe la lista de herramientas cargadas desde el MCP del orquestador
    (tipicamente: `upload_document`) y arma el workflow de LangGraph.

    Args:
        tools: Lista de herramientas MCP provenientes del servidor orquestador.

    Returns:
        Grafo compilado listo para ser invocado.
    """

    extractor_model = FallbackLLM(
        groq_model=config.METADATA_EXTRACTOR_MODEL,
        openrouter_model=config.METADATA_EXTRACTOR_MODEL,
    ).resolve_with_tools(tools=tools)

    async def metadata_extractor_node(state: MetadataExtractorState) -> dict:
        """
        Nodo principal del agente.

        Recibe el estado con los mensajes del usuario, construye el prompt
        con las instrucciones del sistema y llama al modelo con las herramientas
        del MCP disponibles.
        """
        sys_msg = SystemMessage(content=load_agent_prompt("metadata_extractor_agent"))
        prompt = [sys_msg] + state["messages"]
        response = await extractor_model.ainvoke(prompt)
        return {"messages": [response]}

    workflow = StateGraph(MetadataExtractorState)

    # Nodos
    workflow.add_node("metadata_extractor", metadata_extractor_node)
    workflow.add_node("tools", ToolNode(tools=tools))

    # Aristas
    workflow.add_edge(START, "metadata_extractor")
    workflow.add_conditional_edges("metadata_extractor", tools_condition)
    workflow.add_edge("tools", "metadata_extractor")

    return workflow.compile(name="MetadataExtractorGraph")
