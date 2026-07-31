"""
Bots Agent: Specialized agent for bot attack analysis
Connects via MCP (Model Context Protocol) with the Bots server

System prompt is loaded at runtime from agent_prompts/bots_agent.md.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from langchain_core.messages import SystemMessage, BaseMessage, AIMessage
from langchain_groq import ChatGroq
from typing import TypedDict, Annotated
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.graph.message import add_messages
from core.utils.config import config
from core.utils.prompt_loader import load_agent_prompt
from langchain_openai import ChatOpenAI

class BotsAgentState(TypedDict):
    """State for the Bots agent"""
    messages: Annotated[list[BaseMessage], add_messages]

async def get_bot_model_with_tools(bot_tools):
    import httpx
    import openai

    orchestrator_api_key = config.ORCHESTRATOR_LOCAL_API_KEY

    if not orchestrator_api_key:
        raise RuntimeError("Missing ORCHESTRATOR_API_KEY_LOCAL environment variable")

    auth_headers = {"X-API-Key": orchestrator_api_key}
    timeout = 60.0

    # HTTP sync client
    sync_httpx_client = httpx.Client(headers=auth_headers, timeout=timeout)

    # HTTP async client
    async_httpx_client = httpx.AsyncClient(headers=auth_headers, timeout=timeout)
    async_openai_client = openai.AsyncOpenAI(
        base_url=config.ORCHESTRATOR_BASE_URL_LOCAL,
        api_key="dummy",
        http_client=async_httpx_client,
    )

    bot_model = ChatOpenAI(
        model="qwen3:30b",
        temperature=0,
        base_url=config.ORCHESTRATOR_BASE_URL_LOCAL,
        api_key="dummy",
        http_client=sync_httpx_client,                  # used by .invoke()
        async_client=async_openai_client.chat.completions, # used by .ainvoke()
    ).bind_tools(tools=bot_tools)

    return bot_model

async def build_bots_workflow(bot_tools):
    """
    Builds the Bots agent graph
    
    Args:
        bot_tools: List of tools loaded from MCP
        
    Returns:
        Compiled Bots agent graph
    """
    bot_model = ChatGroq(model=config.BOTS_MODEL, temperature=0).bind_tools(tools=bot_tools)
    #bot_model = await get_bot_model_with_tools(bot_tools)


    async def bot_node(state: BotsAgentState):
        """Agent node that decides which tools to use"""
        sys_msg = SystemMessage(content=load_agent_prompt("bots_agent"))
        prompt = [sys_msg] + state["messages"]
        response = await bot_model.ainvoke(prompt)

        # Normalize response to AIMessage format for consistent state updates
        if isinstance(response, dict):
            content = response.get("content") or response.get("text") or str(response)
            response_msg = AIMessage(content=content)
        elif isinstance(response, BaseMessage):
            response_msg = response
        else:
            response_msg = AIMessage(content=str(response))

        return {"messages": [response_msg]}

    workflow = StateGraph(BotsAgentState)
    
    # Nodes
    workflow.add_node("agent", bot_node)
    workflow.add_node("tools", ToolNode(bot_tools))
    
    # Edges
    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", tools_condition)
    workflow.add_edge("tools", "agent")
    
    return workflow.compile(name="BotsGraph")
