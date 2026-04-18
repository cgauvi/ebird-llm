"""
agent.py — LangChain agent wiring for the eBird birding assistant.

LLM:    Resolved at runtime via src.config.build_llm().
        Default model: qwen2.5-72b (HuggingFace Inference API).
Tools:  6 eBird API tools + 2 visualization tools + 1 summarizer tool.
Memory: Conversation history is passed explicitly on every call from
        st.session_state.messages (maintained by the Streamlit UI).
        The agent itself is stateless — no in-process checkpointer.

Public API
----------
run_agent(user_input: str, history: list[dict] | None = None) -> str
    Submit a message (plus prior history) and return the agent's final answer.

reset_agent()
    Discard the cached agent/LLM so the next call rebuilds with the current
    model selection (called from the Streamlit sidebar on model change).
"""

import json
import logging
import re

from langgraph.prebuilt import create_react_agent
from langchain_core.messages import AIMessage, HumanMessage

from langchain_core.tools import StructuredTool

from src.config import build_llm
from src.tools.ebird_tools import EBIRD_TOOLS
from src.tools.viz_tools import VIZ_TOOLS
from src.tools.summarizer_tool import SUMMARIZER_TOOLS
from src.utils.logging_config import add_log_entry
from src.utils.state import VizBuffer, get_last_obs_file, get_last_observations
from src.utils.summarizer import MAX_TOOL_OUTPUT_CHARS, summarize_text

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool auto-summarizer wrapper
# ---------------------------------------------------------------------------


def _wrap_with_summarizer(t: StructuredTool) -> StructuredTool:
    """Return a new StructuredTool that auto-summarizes large string outputs.

    When the wrapped tool returns a string whose length exceeds
    MAX_TOOL_OUTPUT_CHARS the full text is saved to a temp file and a compact
    Markdown summary is returned instead, keeping the LLM context small.
    """
    original_func = getattr(t, "func", None)
    if original_func is None:
        return t

    def _summarizing_func(*args, **kwargs):
        result = original_func(*args, **kwargs)
        if isinstance(result, str) and len(result) > MAX_TOOL_OUTPUT_CHARS:
            return summarize_text(result, title=t.name)
        return result

    return StructuredTool.from_function(
        func=_summarizing_func,
        name=t.name,
        description=t.description,
        args_schema=t.args_schema,
    )


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
• get_region_info                     — metadata and bounding box for a region code
• get_top100_contributors             — top eBirders in a region for a specific date
• get_species_list                    — all species ever recorded in a region
• get_region_stats                    — checklist/contributor stats for a region on a date
• validate_species                    — verify a species name/code before using it in a tool
• create_sightings_map                — draw an interactive map of sightings
• create_historical_chart             — draw a bar or line chart of observations
• summarize_output                    — save a large text output to a file and return a compact summary

Observation data workflow (IMPORTANT — reduces token usage):
1. When an eBird data tool returns results, the output includes:
   - The total record count and top species
   - A file path: "JSON file: /tmp/ebird_summaries/observations_<timestamp>.json"
   - A prompt: Call create_sightings_map or create_historical_chart with observations_file="<path>"
2. Use the compact summary for reasoning and your text response to the user.
3. When calling create_sightings_map or create_historical_chart, pass the file path
   from step 1 as the observations_file argument. Do NOT pass raw JSON or omit the path
   when you have it — file-based loading is faster and avoids token limits.

Workflow guidelines:
1. When a user asks about sightings, ALWAYS fetch data first with an eBird tool.
2. After fetching observation data, proactively offer to (or automatically) call
   create_sightings_map if the user wants to *see* locations, or
   create_historical_chart if the user wants to *compare* or *analyse* counts.
   Pass the observations_file path from the eBird tool output.
3. Present a concise text summary alongside the visualization tool call.
4. If a query is ambiguous (e.g. a city name without coordinates), ask the user
   to provide a latitude/longitude or a known eBird region code.
5. Never fabricate species counts or locations — always use tool results.

Region code rules (CRITICAL — follow exactly):
- NEVER guess or invent a region code. If you are not certain of the exact code,
  call get_region_list first to retrieve the valid codes for that area.
- Country codes are exactly 2 uppercase letters: 'US', 'CA', 'FR', 'GB', etc.
- State/province codes are 'CC-XX': 'US-NY', 'CA-QC', 'FR-75'.
- County/district codes are 'CC-XX-YYY': 'US-NY-061', 'CA-QC-ABI'.

Species validation rules (CRITICAL — follow exactly):
- NEVER invent or guess a species code. Before passing a species_code to any tool,
  call validate_species first.
- validate_species checks the species against recent observations fetched this session.
  If the session has no observation cache, pass the current region_code so it can
  check the regional species list instead.
- Use only the species_code returned by validate_species in subsequent tool calls.
- If validate_species returns 'found: false', show the suggestions to the user and
  ask them to confirm the correct species before proceeding.
- All letters in a region code are UPPERCASE. Lowercase is never valid.
- When a tool returns an error saying a code was not found, call get_region_list
  with the appropriate region_type and parent_region_code to find the correct code,
  then retry with the exact code from the list.

Species code rules:
- Species codes are 2–10 lowercase letters/digits (e.g. 'norcar', 'amerob', 'y00478').
- NEVER pass a common name ('Northern Cardinal') or scientific name as a species_code.
- If you do not know the exact species code, omit the parameter and filter the results
  yourself from the full observation list.

Coordinates rules:
- lat must be between -90 and 90 (negative = south). lng must be between -180 and 180
  (negative = west). Do not swap lat and lng.
- Do not fabricate coordinates. If the user gives a city name, ask for coordinates or
  use well-known approximate coordinates (e.g. Paris ≈ lat 48.85, lng 2.35).

Date rules:
- Dates must be in the past; eBird has no future observations.
- year must be ≥ 1800. month must be 1–12. day must be valid for that month.
"""

# ---------------------------------------------------------------------------
# Agent — built lazily on first call; stateless (no checkpointer).
# reset_agent() discards it so the next call rebuilds with the current model.
# ---------------------------------------------------------------------------

_agent = None


def _get_agent():
    global _agent
    if _agent is not None:
        return _agent

    # Wrap eBird + viz tools so outputs exceeding MAX_TOOL_OUTPUT_CHARS are
    # automatically saved to a temp file and replaced with a compact summary.
    wrapped_tools = [_wrap_with_summarizer(t) for t in EBIRD_TOOLS + VIZ_TOOLS]
    all_tools = wrapped_tools + SUMMARIZER_TOOLS
    llm = build_llm()

    _agent = create_react_agent(
        model=llm,
        tools=all_tools,
        prompt=_SYSTEM_PROMPT,
    )
    return _agent


# ---------------------------------------------------------------------------
# History helpers
# ---------------------------------------------------------------------------


def _build_messages(
    user_input: str,
    history: list[dict] | None = None,
) -> list:
    """Build the full LangChain message list from prior history + new input.

    history entries are dicts with keys 'role' ('user'/'assistant') and 'content'.
    """
    messages: list = []
    for msg in (history or []):
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))
    messages.append(HumanMessage(content=user_input))
    return messages


def run_agent(user_input: str, history: list[dict] | None = None) -> str:
    """Run the agent and return its final text response."""
    from langchain_core.messages import ToolMessage

    logger.info("run_agent called | input: %s", user_input[:200])
    add_log_entry("INFO", "src.agent", f"User: {user_input}")

    agent = _get_agent()
    result = agent.invoke(
        {"messages": _build_messages(user_input, history)},
    )
    messages = result["messages"]

    tool_messages = [m for m in messages if isinstance(m, ToolMessage)]

    # Walk backwards to find the last AIMessage with real text content,
    # skipping any that are just echoed tool-call markup (start with "[").
    for msg in reversed(messages):
        if not isinstance(msg, AIMessage):
            continue
        content = msg.content if isinstance(msg.content, str) else ""
        if content.strip() and not content.strip().startswith("["):
            add_log_entry("LLM_OUT", "src.agent", content)
            logger.info("Agent response: %s", content[:500])
            return content

    # Fall back to tool output if the LLM produced no summary
    if tool_messages:
        return " ".join(
            m.content for m in tool_messages if isinstance(m.content, str)
        )

    return str(messages[-1].content)


# Human-readable labels for each tool, shown in the progress panel.
_TOOL_LABELS: dict[str, str] = {
    "get_recent_observations_by_location": "Fetching recent sightings near location…",
    "get_recent_observations_by_region":   "Fetching recent sightings for region…",
    "get_historic_observations":           "Fetching historic observations…",
    "get_nearby_hotspots":                 "Finding nearby hotspots…",
    "get_region_list":                     "Fetching region list…",
    "get_notable_observations_by_location": "Fetching notable/rare sightings…",
    "get_region_info":                     "Fetching region info…",
    "get_top100_contributors":             "Fetching top contributors…",
    "get_species_list":                    "Fetching species list for region…",
    "get_region_stats":                    "Fetching region statistics…",
    "create_sightings_map":                "Rendering sightings map…",
    "create_historical_chart":             "Building observations chart…",
    "summarize_output":                    "Summarizing large output…",
}


def stream_agent(user_input: str, history: list[dict] | None = None):
    """Stream agent execution, yielding step events for UI progress display.

    history is the list of prior {role, content} dicts from st.session_state.messages.

    Yields dicts with one of three shapes:
      {"type": "tool_start", "name": "<tool_name>", "label": "<friendly label>"}
      {"type": "tool_end",   "name": "<tool_name>", "output": "<result text>"}
      {"type": "final",      "content": "<response text>"}

    Fallback handling
    -----------------
    Some HuggingFace models emit tool calls as JSON text in message content
    instead of using the structured tool_calls attribute.  Two formats are
    detected and executed directly so VizBuffer is always populated:
      • Format A (array):  '[{"name": "...", "arguments": {...}}]'
      • Format B (object): '{"name": "...", "parameters": {...}}'
    """
    agent = _get_agent()
    _tool_map = {t.name: t for t in EBIRD_TOOLS + VIZ_TOOLS + SUMMARIZER_TOOLS}
    logger.info("stream_agent called | input: %s", user_input[:200])
    add_log_entry("INFO", "src.agent", f"User: {user_input}")

    def _stream_invoke():
        return list(
            agent.stream(
                {"messages": _build_messages(user_input, history)},
                stream_mode="updates",
            )
        )

    chunks = _stream_invoke()
    for chunk in chunks:
        if "agent" in chunk:
            for msg in chunk["agent"]["messages"]:
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        name = tc["name"]
                        args_str = json.dumps(tc.get("args", {}), default=str)
                        add_log_entry("TOOL_IN", "src.agent", f"→ {name}({args_str})")
                        logger.debug("Tool call: %s | args: %s", name, args_str)
                        yield {
                            "type": "tool_start",
                            "name": name,
                            "label": _TOOL_LABELS.get(name, f"Running {name}…"),
                        }
                elif hasattr(msg, "content") and msg.content:
                    content = msg.content if isinstance(msg.content, str) else ""
                    stripped = content.strip()
                    # Detect text-based tool-call envelopes that some HuggingFace
                    # models emit instead of proper tool_calls entries.
                    # Format A: '[{"name": ..., "arguments": {...}}]'
                    # Format B: '{"name": ..., "parameters": {...}}'
                    if stripped.startswith("[") or (
                        stripped.startswith("{") and '"name"' in stripped
                    ):
                        try:
                            data = json.loads(stripped)
                            if isinstance(data, list):
                                data = data[0] if data else {}
                            tool_name = data.get("name")
                            tool_args = (
                                data.get("parameters")
                                or data.get("arguments")
                                or data.get("args")
                                or {}
                            )
                            if tool_name and tool_name in _tool_map:
                                add_log_entry(
                                    "INFO", "src.agent",
                                    f"Text-based tool call detected: {tool_name}"
                                )
                                logger.info(
                                    "Fallback: executing text-based tool call '%s'",
                                    tool_name,
                                )
                                yield {
                                    "type": "tool_start",
                                    "name": tool_name,
                                    "label": _TOOL_LABELS.get(tool_name, f"Running {tool_name}…"),
                                }
                                result = _tool_map[tool_name].invoke(tool_args)
                                yield {
                                    "type": "tool_end",
                                    "name": tool_name,
                                    "output": str(result),
                                }
                                yield {"type": "final", "content": str(result)}
                        except Exception:
                            pass
                    elif stripped:
                        add_log_entry("LLM_OUT", "src.agent", content)
                        logger.info("LLM response: %s", content[:500])
                        # Detect when the LLM hallucinates a viz tool return value
                        # instead of invoking the tool.  Auto-invoke with cached data.
                        if VizBuffer["type"] is None:
                            cached = get_last_observations()
                            if cached:
                                _fake_tool: str | None = None
                                if re.search(
                                    r"map created with \d+", stripped, re.IGNORECASE
                                ):
                                    _fake_tool = "create_sightings_map"
                                elif re.search(
                                    r"chart created with \d+", stripped, re.IGNORECASE
                                ):
                                    _fake_tool = "create_historical_chart"
                                if _fake_tool and _fake_tool in _tool_map:
                                    add_log_entry(
                                        "INFO", "src.agent",
                                        f"Auto-fallback: invoking {_fake_tool} "
                                        "(LLM faked the tool output)",
                                    )
                                    logger.info(
                                        "Auto-fallback: invoking '%s' with cached obs",
                                        _fake_tool,
                                    )
                                    yield {
                                        "type": "tool_start",
                                        "name": _fake_tool,
                                        "label": _TOOL_LABELS.get(
                                            _fake_tool, f"Running {_fake_tool}…"
                                        ),
                                    }
                                    try:
                                        obs_file = get_last_obs_file()
                                        invoke_args = (
                                            {"observations_file": obs_file}
                                            if obs_file
                                            else {}
                                        )
                                        _result = _tool_map[_fake_tool].invoke(
                                            invoke_args
                                        )
                                        yield {
                                            "type": "tool_end",
                                            "name": _fake_tool,
                                            "output": str(_result),
                                        }
                                    except Exception as _exc:
                                        logger.warning(
                                            "Auto-fallback '%s' failed: %s",
                                            _fake_tool, _exc,
                                        )
                        yield {"type": "final", "content": content}
        if "tools" in chunk:
            for msg in chunk["tools"]["messages"]:
                tool_name = getattr(msg, "name", "tool")
                output = msg.content if isinstance(msg.content, str) else str(msg.content)
                # Truncate very large tool outputs in the log to stay readable
                log_output = output if len(output) <= 800 else output[:800] + "… [truncated]"
                add_log_entry("TOOL_OUT", "src.agent", f"← {tool_name}: {log_output}")
                logger.debug("Tool result: %s | %s", tool_name, log_output)
                yield {
                    "type": "tool_end",
                    "name": tool_name,
                    "output": output,
                }


def reset_agent() -> None:
    """Discard the cached agent so it is rebuilt with the current model on next use."""
    global _agent
    _agent = None

