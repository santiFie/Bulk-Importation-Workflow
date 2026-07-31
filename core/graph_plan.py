import os
import operator
import asyncio
from typing import Annotated, Any, TypedDict, List, Tuple, Literal, cast
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_groq import ChatGroq
from langchain_nvidia_ai_endpoints import ChatNVIDIA
from langgraph.graph import END, START, StateGraph
from core.agent.bots_agent import build_bots_workflow
from core.agent.github_agent import build_github_workflow
from core.agent.researcher_agent import build_searcher_graph
from core.agent.dspace_agent import build_dspace_agent_workflow
from core.agent.minio_agent import build_minio_workflow
from core.agent.openalex_agent import build_openalex_workflow
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from core.utils.config import config
from core.utils.rag_context import retrieve_planner_context
from core.utils.local_rag_context import retrieve_planner_context_local

DOWNLOADS_DIR = config.DOWNLOADS_DIR
AgentName = Literal["github", "dspace", "minio", "bots", "searcher", "openalex"]

class PlanStep(BaseModel):
    """One step in the plan."""
    task: str = Field(description="Clear and concise description of the task to be performed in this step.")
    assigned_agent: AgentName = Field(description="The agent most suitable to execute this task based on their tools.")

class PlanExecuteState(TypedDict):
    """State for the Plan and Execute graph."""
    input: str
    domain_context: str # Dynamic context 
    plan: List[PlanStep]
    past_steps: Annotated[List[Tuple[str, str]], operator.add]
    response: str



class Plan(BaseModel):
    """Plan to follow."""
    steps: List[PlanStep] = Field(description="Discrete steps to follow to achieve the goal. Each step should be a clear and concise task.")


async def get_tools() -> dict[str, list[Any]]:
    bots_client = MultiServerMCPClient(
        {
            "BotsAgent": {
                "url": config.BOTS_MCP_URL.replace("mcp", "127.0.0.1"),
                "transport": "sse",
            }
        }
    )

    system_env = dict(os.environ)
    github_env = {
        **system_env,
        "GITHUB_PERSONAL_ACCESS_TOKEN": config.GITHUB_PERSONAL_ACCESS_TOKEN or "",
    }

    github_client = MultiServerMCPClient(
        {
            "filesystem": {
                "command": "mcp-server-filesystem",
                "args": [config.WORKSPACE_PATH],
                "transport": "stdio",
                "env": system_env,
            },
            "github": {
                "command": "mcp-server-github",
                "args": [],
                "env": github_env,
                "transport": "stdio",
            },
        }
    )

    dspace_client = MultiServerMCPClient(
        {
            "DspaceMCP": {
                "url": config.DSPACE_MCP_URL,
                "transport": "sse",
            }
        }
    )

    DOWNLOADS_DIR = config.DOWNLOADS_DIR
    MINIO_ACCESS_KEY = config.MINIO_ROOT_USER
    MINIO_SECRET_KEY = config.MINIO_ROOT_PASSWORD

    minio_client = MultiServerMCPClient(
        {
            "aistor": {
                "command": "docker",
                "args": [
                    "run",
                    "-i",
                    "--rm",
                    "--network=host",
                    "-v", f"{DOWNLOADS_DIR}:/Downloads",
                    "-e", "MINIO_ENDPOINT=localhost:9003", 
                    "-e", f"MINIO_ACCESS_KEY={MINIO_ACCESS_KEY}",
                    "-e", f"MINIO_SECRET_KEY={MINIO_SECRET_KEY}",
                    "-e", "MINIO_USE_SSL=false",
                    "quay.io/minio/aistor/mcp-server-aistor:latest",
                    "--allowed-directories", "/Downloads",
                    "--allow-write",
                    "--allow-delete",
                    "--allow-admin"
                ],
                "transport": "stdio",
            },
        }
    )

    openalex_client = MultiServerMCPClient(
        {
            "openalex": {
                "command": "docker",
                "args": [
                    "run",
                    "--rm",
                    "-i",
                    "-e", f"OPENALEX_API_KEY={config.OPENALEX_API_KEY}",
                    "-e", f"OPENALEX_EMAIL={config.OPENALEX_EMAIL}",
                    "multi-servicesproject-openalex-mcp",
                ],
                "transport": "stdio",
            }
        }
    )

    bot_tools = await bots_client.get_tools()
    github_tools = await github_client.get_tools(server_name="github")
    filesystem_tools = await github_client.get_tools(server_name="filesystem")
    dspace_tools = await dspace_client.get_tools(server_name="DspaceMCP")
    minio_tools = await minio_client.get_tools(server_name="aistor")
    openalex_tools = await openalex_client.get_tools(server_name="openalex")

    tools = {
        "bots": bot_tools,
        "github": github_tools,
        "filesystem": filesystem_tools,
        "dspace": dspace_tools,
        "minio": minio_tools,
        "openalex": openalex_tools
    }

    return tools


async def create_supervisor_graph(persistence_saver):
    """
    Creates the supervisor graph that coordinates the three agents.
    Requires all MCP sessions to be active.
    """

    tools = await get_tools()

    def _build_searcher_sync():
        return build_searcher_graph()

    searcher_graph = _build_searcher_sync()
    github_graph = await cast(Any, build_github_workflow(tools["github"] + tools["filesystem"]))
    bots_graph = await build_bots_workflow(tools["bots"])
    dspace_graph = await build_dspace_agent_workflow(tools["dspace"])
    minio_graph = await build_minio_workflow(tools["minio"])
    openalex_graph = await build_openalex_workflow(tools["openalex"])

    planner_llm = ChatGroq(model=config.SUPERVISOR_MODEL, temperature=0)

    DOWNLOADS_DIR = config.DOWNLOADS_DIR
    planner_system_prompt=f"""
    You are the Master Planner for an advanced multi-agent orchestrator.
    Your objective is to analyze the user's complex request and break it down into a clear, sequential, step-by-step plan.

    Each step must represent a single, discrete task and MUST be assigned to the most appropriate specialized agent.
    If a process involves multiple distinct actions (e.g., downloading a file and then uploading it), you must split it into separate steps.

    ## Available Agents

    You must strictly assign one of these exact names to the 'assigned_agent' field:

    ### `searcher`
    Specialized in information retrieval combining real-time web search and local RAG over a PDF collection
    focused on Deep Learning and Data Mining. Uses Tavily for live web queries and a
    FAISS-based retriever for the local collection.
    Completely independent of DSpace, MinIO, GitHub, Bots, and OpenAlex.
    - INPUT: task string — e.g. "Explain how transformers work", "Find recent news about LLMs".
    - OUTPUT: Synthesized, citation-backed response from web and/or local RAG.

    ### `bots`
    Specialized in bot detection. Connects to a Bot Detection MCP server that maintains a database of IPs classified as bots.
    Exclusive agent for any query about banned IPs, bot traffic analysis, or IP reputation checks.
    Completely independent from DSpace, MinIO, GitHub, and OpenAlex.
    - INPUT: task string — e.g. "List all permanently banned IPs", "Check if IP 1.2.3.4 is a bot".
    - OUTPUT: IP status, ban reasons, active timeframes, or paginated lists of banned IPs.

    ### `github`
    Specialized in GitHub remote operations and local filesystem management. Acts as the "file bridge"
    It can: read, create, edit, move, list files on the local host filesystem; interact with GitHub
    repositories (list repos, read/write remote files, create issues and PRs).
    NEVER interacts with MinIO, DSpace, or Bots directly.
    - INPUT: task string — e.g. "Copy file report.csv to {DOWNLOADS_DIR}", "List files in /data/".
    - OUTPUT: Confirmation of operation, file contents, directory listings, or error details.

    ### `dspace`
    Administrates SEDICI, the institutional repository based on DSpace. Can create, update, import, export, and delete communities, collections, items, and manage bitstreams.
    - INPUT: task string — e.g. "Export collection UUID xxxx to CSV", "List items in collection named X".
    - OUTPUT: UUIDs, item metadata, export file path on DSpace server, import status, or structured lists.

    ### `minio`
    Manages object storage in MinIO (S3-compatible). 
    Use for: listing buckets/objects, uploading files from /Downloads/, downloading objects to /Downloads/,
    deleting objects, creating new buckets.
    - INPUT: task string — e.g. "Upload /Downloads/report.csv to bucket analytics", "List all buckets".
    - OUTPUT: Operation confirmation, object metadata, bucket/object listings, or error messages.

    ### `openalex`
    Queries the OpenAlex academic database. Retrieves information about scientific works (articles, books,
    pre-prints, datasets), authors (ORCID profiles, h-index, metrics), institutions (universities, ROR IDs,
    academic output), topics (Wikidata-based fields of study), and sources (journals with ISSN-L).
    Always resolves human-readable names (e.g. "UNLP", "MIT") to canonical OpenAlex IDs before executing
    filter queries. Read-only. Independent of all other agents.
    - INPUT: task string — e.g. "Find top-cited open access articles from Argentina in 2023".
    - OUTPUT: Structured list: **Title**, Authors (max 3 + "et al."), Year/Source, Citations, DOI.

    ## Workflow Playbooks
    The user message may include a "Workflow Playbooks" section with step-by-step patterns for common
    multi-agent workflows (e.g., DSpace export → MinIO upload, metadata editing). Use these as a blueprint
    when the user's request matches a known pattern.
    Always prioritize playbook guidance when available — it encodes tested, correct sequences.

    ## Planning Rules
    1. **Dependency Management**: If `minio` must upload a file, a prior step MUST assign `github` to
       move/copy that file into {DOWNLOADS_DIR}. `minio` will fail without this.
    2. **Clarity**: Task descriptions must be highly specific. The assigned agent must know exactly what
       to execute without needing the full goal context.
    3. **Simplicity**: Only include steps necessary to achieve the user's goal. No redundant steps.
    4. **Sequential Order**: Ensure logical sequencing, especially for file-dependency chains.
    5. **NO Presentation Steps**: NEVER create a step whose sole purpose is to "present", "format",
       "recopilar", or "display" results. That is handled automatically by the final_answer_node.
       Every step MUST perform a concrete action (search, fetch, upload, edit, filter, etc.).
    """

    planner_prompt = ChatPromptTemplate.from_messages([
        SystemMessage(content=planner_system_prompt),
        ("user",
         "## User Request\n{input}\n\n"
         "## Workflow Playbooks\n{domain_context}"
        )
    ])

    planner_chain = planner_prompt | planner_llm.with_structured_output(Plan)

    async def rag_context_node(state: PlanExecuteState) -> dict:
        """Retrieves relevant workflow playbooks and injects them into domain_context."""
        context = retrieve_planner_context(state["input"])
        #context = await retrieve_planner_context_local(state["input"])
        return {"domain_context": context}

    async def planner_node(state: PlanExecuteState):
        plan = await planner_chain.ainvoke({
            "input": state["input"],
            "domain_context": state.get("domain_context") or "",
        })
        return {"plan": plan.steps}
    
    async def replan_node(state: PlanExecuteState):
        """Evalúa el progreso, remueve las tareas completadas y devuelve las pendientes."""
        previous_plan = state["plan"]
        past_steps = state.get("past_steps", [])

        # Pre-compute pending tasks in Python — tasks not yet present in past_steps
        executed_task_descriptions = {step for step, _ in past_steps}
        pending_tasks = [
            step for step in previous_plan
            if step.task not in executed_task_descriptions
        ]

        executed_context = "\n".join(
            [f"- Task: {step}\n  Result: {result}" for step, result in past_steps]
        ) or "(none)"

        pending_context = "\n".join(
            [f"- {step.task} (Assigned to: {step.assigned_agent})" for step in pending_tasks]
        ) or "(none — all tasks have been executed)"

        replan_prompt = f"""You are the Re-planner agent for a multi-agent system.

        Original Goal: '{state['input']}'

        ## Already Executed Tasks (DO NOT repeat these):
        {executed_context}

        ## Pending Tasks (not yet executed):
        {pending_context}

        Your job is to decide what still needs to be done.

        STRICT RULES:
        1. NEVER repeat a task that already appears in the "Already Executed" section.
        2. KEEP all tasks listed under "Pending Tasks" unless they were made unnecessary by an executed result.
        3. If an executed task produced an error, add corrective steps BEFORE the remaining pending tasks.
        4. CRITICAL — Goal Completion: If "Pending Tasks" shows "(none — all tasks have been executed)"
           AND none of the executed tasks produced an unrecovered error,
           return an EMPTY list of steps immediately. Do not add any new steps.
        5. NEVER add steps whose sole purpose is to "present", "format", "recopilar", or "display" results.
           Those are handled automatically after the plan ends.

        Output ONLY the list of tasks that still need to be executed (empty list if the goal is complete):"""

        structured_llm = planner_llm.with_structured_output(Plan)
        new_plan = await structured_llm.ainvoke(replan_prompt)

        return {"plan": new_plan.steps}

    def create_agent_node(agent_graph):
        """Factory method to create a node function for each agent graph. This node will receive the current task and the context of previous steps, and will invoke the corresponding agent graph."""
        async def node(state: PlanExecuteState):
            current_step = state["plan"][0]
            current_task = current_step.task
            
            # Context
            context = "\n".join([f"Step: {step}\nResult: {result}" for step, result in state.get("past_steps", [])])
            
            agent_prompt = f"Current task to execute: {current_task}\n\nPrevious context and results:\n{context}"
            
            response = await agent_graph.ainvoke({"messages": [("user", agent_prompt)]})
            
            if isinstance(response, dict) and "messages" in response:
                agent_result = response["messages"][-1].content
            else:
                agent_result = str(response) # Fallback
                
            # Update past steps with the result of the current task
            return {
                "past_steps": [(current_task, agent_result)]
            }
            
        return node
    
    def final_answer_node(state: PlanExecuteState):
        """
        Generates the final response for the user once the plan is fully executed.
        """
        past_steps = state.get("past_steps", [])
        user_input = state["input"]
        
        # Past context
        context = "\n".join([f"Task: {step}\nResult: {result}" for step, result in past_steps])
        
        final_prompt = f"""You are a helpful AI assistant managing a multi-agent system.
        The user originally asked: '{user_input}'
        
        Here is the log of all actions taken by the specialized agents to fulfill this request:
        {context}
        
        Based ONLY on the results above, provide a clear, natural, and concise final response to the user. 
        Explain what was done and provide any final requested information or confirmation."""
        
        # Use not structured output here
        final_llm = ChatGroq(model=config.SUPERVISOR_MODEL, temperature=0.3)
        response = final_llm.invoke(final_prompt)
        
        return {"response": response.content}
    
    def route_current_task(state: PlanExecuteState) -> str:
        """Reads the current task from the plan and decides which agent to route it to based on the assigned_agent field."""
        if not state["plan"]:
            return "final_answer_node"

        current_step = state["plan"][0]
        assigned_agent = current_step.assigned_agent
        return assigned_agent
    
    workflow = StateGraph(PlanExecuteState)

    # Nodes
    workflow.add_node("rag_context", rag_context_node)
    workflow.add_node("planner", planner_node)
    workflow.add_node("replanner", replan_node)
    workflow.add_node("final_answer_node", final_answer_node)
    workflow.add_node("searcher", create_agent_node(searcher_graph))
    workflow.add_node("github", create_agent_node(github_graph))
    workflow.add_node("bots", create_agent_node(bots_graph))
    workflow.add_node("dspace", create_agent_node(dspace_graph))
    workflow.add_node("minio", create_agent_node(minio_graph))
    workflow.add_node("openalex", create_agent_node(openalex_graph))

    # Edges
    workflow.add_edge(START, "rag_context")
    workflow.add_edge("rag_context", "planner")
    workflow.add_conditional_edges("planner", route_current_task, {
        "searcher": "searcher",
        "github": "github",
        "bots": "bots",
        "dspace": "dspace",
        "minio": "minio",
        "openalex": "openalex",
        "replanner": "replanner"
    })

    for agent in ["searcher", "github", "bots", "dspace", "minio", "openalex"]:
        workflow.add_edge(agent, "replanner")

    workflow.add_conditional_edges(
        "replanner",
        route_current_task,
        {
            "searcher": "searcher",
            "github": "github",
            "bots": "bots",
            "dspace": "dspace",
            "minio": "minio",
            "openalex": "openalex",
            "final_answer_node": "final_answer_node"
        }
    )

    workflow.add_edge("final_answer_node", END)

    return workflow.compile(name="SupervisorGraph")


def get_supervisor_graph(persistence_saver=None):
    """Returns the supervisor graph for synchronous usage."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(create_supervisor_graph(persistence_saver))

def _load_supervisor_graph():
    try:
        return get_supervisor_graph(None)
    except Exception as exc:
        raise RuntimeError("Failed to create the supervisor graph", exc)


supervisor_graph = _load_supervisor_graph()