from typing import TypedDict, Annotated, List
from langchain_core.messages import BaseMessage, SystemMessage
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.graph import END, START, StateGraph
from core.utils.config import config
from core.utils.get_local_model import FallbackLLM
from core.utils.prompt_loader import load_agent_prompt


class OpenAlexState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]

async def build_openalex_workflow(tools):
    """Agent node that decides which tools to use"""


    async def openalex_node(state: OpenAlexState):
        openalex_model = FallbackLLM(groq_model=config.OPENALEX_MODEL, openrouter_model=config.OPENALEX_MODEL).resolve_with_tools(tools=tools)
        sys_msg = SystemMessage(content=load_agent_prompt("openalex_agent"))
        prompt = [sys_msg] + state["messages"]
        response = await openalex_model.ainvoke(prompt)
        return {"messages": [response]}

    # Nodes    
    openalex_graph = StateGraph(OpenAlexState)
    openalex_graph.add_node("openalex_node", openalex_node)
    openalex_graph.add_node("tools", ToolNode(tools))
    
    # Edges    
    openalex_graph.add_edge(START, "openalex_node")
    openalex_graph.add_conditional_edges("openalex_node", tools_condition)
    openalex_graph.add_edge("tools", "openalex_node")

    return openalex_graph.compile(name="openalex_graph")