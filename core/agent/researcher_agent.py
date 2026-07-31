"""
Researcher Agent: Intelligent search with RAG and web search
Includes response validation through an academic reviewer
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from langchain_core.messages import SystemMessage, BaseMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field
from typing import TypedDict, Annotated, Literal
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.graph.message import add_messages
from core.tools.tavily import generate_tavily
from core.utils.config import config
from core.utils.prompt_loader import load_agent_prompt


class SearcherAgentState(TypedDict):
    """State for the searcher graph"""
    messages: Annotated[list[BaseMessage], add_messages]
    binary_score: str
    critics_feedback: str


class GradeDocuments(BaseModel):
    """Structure for grading responses"""
    score: Literal["yes", "no"] = Field(
        description="Binary score where 'yes' means complete and 'no' means incomplete."
    )
    critique: str = Field(
        description="Detailed critique and recommendations for improvement."
    )


def should_continue(state: SearcherAgentState) -> Literal["end", "repeat"]:
    """Determine whether to repeat or end the workflow"""
    # Allow up to 3 iterations (6 messages)
    if len(state["messages"]) < 6 and state.get("binary_score") == "no":
        return "repeat"
    return "end"


def build_searcher_graph():
    """
    Builds the search graph with validation
    Flow: Author → Tools → Reviewer → (Repeat or End)
    """
    tavily_tool = generate_tavily()

    author_llm = ChatGroq(model=config.SEARCHER_MODEL, temperature=0)
    reviewer_llm = ChatGroq(model=config.SEARCHER_MODEL, temperature=0)

    async def author_node(state: SearcherAgentState) -> dict:
        """
        Uses tavily_tool for general information
        """
        AUTHOR_PROMPT = SystemMessage(content=load_agent_prompt("searcher_agent"))

        prompt = [AUTHOR_PROMPT] + state["messages"]

        feedback = state.get("critics_feedback", "")
        if feedback:
            prompt.append(SystemMessage(content=f"Reviewer feedback: {feedback}\nAdjust your previous answer according to these recommendations."))

        llm_with_tools = author_llm.bind_tools(tools=[tavily_tool])
        response = await llm_with_tools.ainvoke(prompt)

        # Normalize response to AIMessage format for consistent state updates
        if isinstance(response, dict):
            content = response.get("content") or response.get("text") or str(response)
            response_msg = AIMessage(content=content)
        elif isinstance(response, BaseMessage):
            response_msg = response
        else:
            response_msg = AIMessage(content=str(response))

        return {"messages": [response_msg]}

    async def reflection_node(state: SearcherAgentState) -> dict:
        """
        Reflection Node: Validates answer quality
        Uses academic criteria: precision, completeness, clarity
        """
        reflection_prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                """You are an elite Academic Reviewer. Your objective is to ensure the Author's answer is perfect.
                Evaluate the response based on the message history.

                EVALUATION CRITERIA:
                - Technical precision (Is it correct about Deep Learning?)
                - Completeness (Does it answer everything asked?)
                - Clarity and structure

                If the answer is excellent, mark score: "yes"
                If something is missing or can be improved, mark score: "no" and detail necessary changes in 'critique'.""",
            ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        messages_to_grade = reflection_prompt.format_messages(messages=state["messages"])
        response = await reviewer_llm.with_structured_output(GradeDocuments).ainvoke(messages_to_grade)

        return {
            "binary_score": response.score,
            "critics_feedback": response.critique,
        }

    workflow = StateGraph(SearcherAgentState)
    
    # Nodes
    workflow.add_node("generator", author_node)
    workflow.add_node("tools_node", ToolNode(tools=[retriever_tool, tavily_tool]))
    workflow.add_node("revisor", reflection_node)
    
    # Edges
    workflow.add_edge(START, "generator")
    
    workflow.add_conditional_edges(
        "generator",
        tools_condition,
        {
            "tools": "tools_node",
            END: "revisor"
        }
    )
    
    workflow.add_edge("tools_node", "generator")
    
    workflow.add_conditional_edges(
        "revisor",
        should_continue,
        {
            "end": END,
            "repeat": "generator"
        }
    )
    
    return workflow.compile(name="SearcherGraph")