import os
from typing import Annotated, TypedDict
from langchain_core.messages import BaseMessage, SystemMessage, AIMessage
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.graph.message import add_messages
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from core.utils.config import config
from core.utils.prompt_loader import load_agent_prompt

DOWNLOADS_DIR = config.DOWNLOADS_DIR

class MinioAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

def get_minio_model_with_tools(minio_tools):
    import httpx

    orchestrator_api_key = config.ORCHESTRATOR_LOCAL_API_KEY

    if not orchestrator_api_key:
        raise RuntimeError("Missing ORCHESTRATOR_API_KEY_LOCAL environment variable")

    def create_custom_http_client():
        """Cliente HTTP con autenticación via header X-API-Key"""
        return httpx.Client(
            headers={"X-API-Key": orchestrator_api_key},
            timeout=60.0,
        )
    
    minio_model = ChatOpenAI(
        model="qwen3:30b", 
        temperature=0,
        base_url=config.ORCHESTRATOR_BASE_URL_LOCAL,
        api_key="dummy",  # Not used, apy key is sent via custom header 
        http_client=create_custom_http_client(),
    ).bind_tools(tools=minio_tools)    

    return minio_model


@tool
def get_host_downloads_dir() -> str:
    """Returns the path to the shared downloads directory on the host machine. This is where the MinIO tools read/write files from."""
    return DOWNLOADS_DIR

async def build_minio_workflow(tools):

    tools.append(get_host_downloads_dir)
    minio_model = ChatGroq(model=config.MINIO_MODEL, temperature=0).bind_tools(tools=tools)
    #minio_model = get_minio_model_with_tools(tools)

    async def minio_node(state):
        """Node function for Minio operations"""
        messages = state["messages"]

        system_message = SystemMessage(content=load_agent_prompt("minio_agent"))
        
        prompt = [system_message] + messages
        response = await minio_model.ainvoke(prompt)

        return {"messages": [response]}
    
    workflow = StateGraph(MinioAgentState)

    workflow.add_node("minio_agent", minio_node)
    workflow.add_node("tools", ToolNode(tools))

    workflow.add_edge(START, "minio_agent")
    workflow.add_conditional_edges("minio_agent", tools_condition)
    workflow.add_edge("tools", "minio_agent")

    return workflow.compile(name="MinioAgentWorkflow")