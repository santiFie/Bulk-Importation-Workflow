from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages
from core.utils.config import Config
from core.utils.prompt_loader import load_agent_prompt
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage
from langchain_nvidia_ai_endpoints import ChatNVIDIA
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import BaseModel, Field

config = Config()

class RouterOutput(BaseModel):
    """Tools selected by the router to be used in the current step."""
    selected_tool_names: list[str] = Field(
        description="List of tool names that the router has selected to be used, in the order they should be called. If no tools are needed, return an empty list."
    )

class DspaceAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    tools_to_use: list[str] 

async def build_dspace_agent_workflow(tools):
    """Builds the DspaceAgent workflow"""

    async def router_node(state: DspaceAgentState) -> dict:
        """Router node that decides which tools to use based on the conversation messages."""

        tools_description = "\n".join(f"- {t.name}: {t.description}" for t in tools)
        
        sys_msg = SystemMessage(content=f"""
            You are a router that receives messages from the DSpaceAgent and routes them to the appropriate tools.
            You have access to the following tools:
            {tools_description}

            When you receive a message, determine which tool(s) are needed to respond to the user's request.
            Return a list of tool names that should be invoked, in the order they should be invoked.
            Only include the tools that are necessary to fulfill the user's request. If multiple tools are needed, list them in the order they should be called.
            If you cannot determine any relevant tool from the message, return an empty list.   
        """)

        prompt = [sys_msg] + state["messages"]
        model = ChatNVIDIA(
            model="meta/llama-4-maverick-17b-128e-instruct",
            api_key = config.NVIDIA_API_KEY, 
            temperature=0.1,
        )

        structured_model = model.with_structured_output(RouterOutput)
        response = await structured_model.ainvoke(prompt)

        return {"tools_to_use": response.selected_tool_names}

    async def dspace_agent_node(state: DspaceAgentState):
        """Agent node that decides which tools to use"""

        filetered_tools_list = [t for t in tools if t.name in state["tools_to_use"]]
        filetered_tools = [t for t in filetered_tools_list]

        if not filetered_tools:
            # Fallback
            filetered_tools = tools

        dspace_model = ChatNVIDIA(
            model=config.DSPACE_MODEL,
            temperature=0.1,
            top_p=0.9,    
        ).bind_tools(tools=filetered_tools)

        sys_msg = SystemMessage(content=load_agent_prompt("dspace_agent"))
        prompt = [sys_msg] + state["messages"]
        response = await dspace_model.ainvoke(prompt)

        return {"messages": [response]}
    
    workflow = StateGraph(DspaceAgentState)
    workflow.add_node("router", router_node)
    workflow.add_node("agent", dspace_agent_node)
    workflow.add_node("tools", ToolNode(tools=tools))

    workflow.add_edge(START, "router")
    workflow.add_edge("router", "agent")

    workflow.add_conditional_edges("agent", tools_condition)
    workflow.add_edge("tools", "agent")

    return workflow.compile(name="DspaceAgentGraph")