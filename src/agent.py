"""
agent.py — LangChain agent wiring for the eBird birding assistant.

LLM:    Resolved at runtime via src.config.build_llm().
        Default model: qwen2.5-72b (HuggingFace Inference API).
Tools:  6 eBird API tools + 2 visualization tools.
Memory: LangGraph MemorySaver (single conversation thread, full history kept).

Public API
----------
run_agent(user_input: str) -> str
    Submit a message and return the agent's final answer string.

reset_agent()
    Clear conversation memory (called from the Streamlit sidebar).
"""

from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from src.config import build_llm
from src.tools.ebird_tools import EBIRD_TOOLS
from src.tools.viz_tools import VIZ_TOOLS

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an expert birding assistant powered by the eBird database.
You help birdwatchers discover recent and historic bird sightings, explore hotspots,
and understand regional bird populations.

You have access to the following tools:
• get_recent_observations_by_location — recent sightings near a coordinate
• get_recent_observations_by_region   — recent sightings in a named region
• get_historic_observations           — all sightings in a region on a past date
• get_nearby_hotspots                 — birding hotspot locations near a coordinate
• get_region_list                     — list countries/states/counties within a region
• get_notable_observations_by_location — rare/unusual sightings near a coordinate
• create_sightings_map                — draw an interactive map of sightings
• create_historical_chart             — draw a bar or line chart of observations

Workflow guidelines:
1. When a user asks about sightings, ALWAYS fetch data first with an eBird tool.
2. After fetching observation data, proactively offer to (or automatically) call
   create_sightings_map if the user wants to *see* locations, or
   create_historical_chart if the user wants to *compare* or *analyse* counts.
3. Present a concise text summary alongside the visualization tool call.
4. If a query is ambiguous (e.g. a city name without coordinates), ask the user
   to provide a latitude/longitude or a known eBird region code.
5. Never fabricate species counts or locations — always use tool results.
"""

# ---------------------------------------------------------------------------
# Agent — built lazily on first call
# Thread ID is fixed to a single session; reset_agent() resets the checkpointer.
# ---------------------------------------------------------------------------

_THREAD_ID = "ebird-session"
_agent = None
_checkpointer: MemorySaver | None = None


def _get_agent():
    global _agent, _checkpointer
    if _agent is not None:
        return _agent

    all_tools = EBIRD_TOOLS + VIZ_TOOLS
    llm = build_llm()
    _checkpointer = MemorySaver()

    _agent = create_react_agent(
        model=llm,
        tools=all_tools,
        prompt=_SYSTEM_PROMPT,
        checkpointer=_checkpointer,
    )
    return _agent


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

_config = {"configurable": {"thread_id": _THREAD_ID}}


def run_agent(user_input: str) -> str:
    """Run the agent and return its final text response."""
    agent = _get_agent()
    result = agent.invoke(
        {"messages": [("human", user_input)]},
        config=_config,
    )
    return result["messages"][-1].content


def stream_agent(user_input: str):
    """Stream agent execution, yielding step events for UI progress display.

    Yields dicts with one of three shapes:
      {"type": "tool_start", "name": "<tool_name>"}
      {"type": "tool_end",   "name": "<tool_name>"}
      {"type": "final",      "content": "<response text>"}
    """
    agent = _get_agent()
    for chunk in agent.stream(
        {"messages": [("human", user_input)]},
        config=_config,
        stream_mode="updates",
    ):
        if "agent" in chunk:
            for msg in chunk["agent"]["messages"]:
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        yield {"type": "tool_start", "name": tc["name"]}
                elif hasattr(msg, "content") and msg.content:
                    yield {"type": "final", "content": msg.content}
        if "tools" in chunk:
            for msg in chunk["tools"]["messages"]:
                yield {"type": "tool_end", "name": getattr(msg, "name", "tool")}


def reset_agent() -> None:
    """Clear conversation memory by replacing the checkpointer."""
    global _agent, _checkpointer
    if _checkpointer is not None:
        # MemorySaver has no clear() — rebuild the agent with a fresh one
        _agent = None
        _checkpointer = None

