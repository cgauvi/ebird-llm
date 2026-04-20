"""
test_agent_streaming.py

Unit tests for src.agent.stream_agent, focusing on the text-based tool-call
fallback path.

Some HuggingFace models emit tool invocations as plain JSON text in the
message content rather than via the structured tool_calls attribute.
stream_agent must detect these envelopes, execute the real tool, and yield
the correct event sequence so VizBuffer is always populated.

Two envelope formats are covered:
  Format A (array):  '[{"name": "create_sightings_map", "arguments": {...}}]'
  Format B (object): '{"name": "create_sightings_map", "parameters": {...}}'

The real viz tools are invoked (not mocked) so VizBuffer writes are exercised.
The LangGraph agent.stream() call is patched to return controlled chunks.
"""

import json
from unittest.mock import MagicMock, patch

import folium
import pytest
from langchain_core.messages import AIMessage

from src.tools.viz_tools import create_sightings_map
from src.utils.state import VizBuffer, clear_viz_buffer

# ---------------------------------------------------------------------------
# Sample observations (minimal — two geo-located records)
# ---------------------------------------------------------------------------

SAMPLE_OBS = [
    {
        "comName": "Mallard",
        "sciName": "Anas platyrhynchos",
        "speciesCode": "mallar3",
        "howMany": 2,
        "lat": 46.807,
        "lng": -71.432,
        "obsDt": "2026-04-17 07:15",
        "locName": "Etang Jean-Gauvin",
        "locId": "L001",
    },
    {
        "comName": "Blue Jay",
        "sciName": "Cyanocitta cristata",
        "speciesCode": "blujay",
        "howMany": 3,
        "lat": 46.810,
        "lng": -71.435,
        "obsDt": "2026-04-17 08:00",
        "locName": "Parc de la Rivière",
        "locId": "L002",
    },
]

OBS_JSON = json.dumps(SAMPLE_OBS)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_buffer():
    clear_viz_buffer()
    yield
    clear_viz_buffer()


# ---------------------------------------------------------------------------
# Helpers — build fake LangGraph stream chunks
# ---------------------------------------------------------------------------


def _agent_chunk_with_tool_calls(tool_name: str, tool_args: dict) -> dict:
    """Chunk that carries a proper structured tool_calls attribute."""
    msg = AIMessage(content="")
    msg.tool_calls = [{"name": tool_name, "args": tool_args, "id": "tc1"}]
    return {"agent": {"messages": [msg]}}


def _agent_chunk_text_object(tool_name: str, tool_args: dict) -> dict:
    """Chunk where the model outputs a JSON object as plain text (Format B)."""
    payload = {"name": tool_name, "parameters": tool_args}
    msg = AIMessage(content=json.dumps(payload))
    msg.tool_calls = []
    return {"agent": {"messages": [msg]}}


def _agent_chunk_text_array(tool_name: str, tool_args: dict) -> dict:
    """Chunk where the model outputs a JSON array as plain text (Format A)."""
    payload = [{"name": tool_name, "arguments": tool_args}]
    msg = AIMessage(content=json.dumps(payload))
    msg.tool_calls = []
    return {"agent": {"messages": [msg]}}


def _agent_chunk_final_text(text: str) -> dict:
    """Chunk carrying a plain-text final response from the LLM."""
    msg = AIMessage(content=text)
    msg.tool_calls = []
    return {"agent": {"messages": [msg]}}


def _tools_chunk(tool_name: str, output: str) -> dict:
    """Chunk emitted by the tools node after a real tool_calls invocation."""
    msg = MagicMock()
    msg.name = tool_name
    msg.content = output
    return {"tools": {"messages": [msg]}}


# ---------------------------------------------------------------------------
# stream_agent — normal structured tool_calls path (regression guard)
# ---------------------------------------------------------------------------


class TestStreamAgentNormalPath:
    """Ensure the standard tool_calls path still works after the fallback change."""

    def test_tool_start_yielded_for_structured_call(self):
        from src.agent import stream_agent

        chunks = [
            _agent_chunk_with_tool_calls(
                "create_sightings_map", {"observations_json": OBS_JSON}
            ),
            _tools_chunk("create_sightings_map", "Map created with 2 sightings."),
            _agent_chunk_final_text("Here is your map."),
        ]

        with patch("src.agent._get_agent") as mock_get_agent:
            mock_agent = MagicMock()
            mock_agent.stream.return_value = iter(chunks)
            mock_get_agent.return_value = mock_agent

            events = list(stream_agent("show me a map"))

        types = [e["type"] for e in events]
        assert "tool_start" in types
        assert "tool_end" in types

    def test_final_text_yielded(self):
        from src.agent import stream_agent

        chunks = [
            _agent_chunk_with_tool_calls(
                "create_sightings_map", {"observations_json": OBS_JSON}
            ),
            _tools_chunk("create_sightings_map", "Map created with 2 sightings."),
            _agent_chunk_final_text("Here is your map."),
        ]

        with patch("src.agent._get_agent") as mock_get_agent:
            mock_agent = MagicMock()
            mock_agent.stream.return_value = iter(chunks)
            mock_get_agent.return_value = mock_agent

            events = list(stream_agent("show me a map"))

        final_events = [e for e in events if e["type"] == "final"]
        assert len(final_events) == 1
        assert "map" in final_events[0]["content"].lower()


# ---------------------------------------------------------------------------
# stream_agent — text-based tool call fallback (Format B: JSON object)
# ---------------------------------------------------------------------------


class TestStreamAgentFallbackObjectFormat:
    """LLM outputs '{"name": "...", "parameters": {...}}' instead of tool_calls."""

    def test_tool_start_event_yielded(self):
        from src.agent import stream_agent

        chunks = [
            _agent_chunk_text_object(
                "create_sightings_map", {"observations_json": OBS_JSON}
            ),
        ]
        with patch("src.agent._get_agent") as mock_get_agent:
            mock_agent = MagicMock()
            mock_agent.stream.return_value = iter(chunks)
            mock_get_agent.return_value = mock_agent

            events = list(stream_agent("show me a map"))

        assert any(e["type"] == "tool_start" for e in events)

    def test_tool_end_event_yielded(self):
        from src.agent import stream_agent

        chunks = [
            _agent_chunk_text_object(
                "create_sightings_map", {"observations_json": OBS_JSON}
            ),
        ]
        with patch("src.agent._get_agent") as mock_get_agent:
            mock_agent = MagicMock()
            mock_agent.stream.return_value = iter(chunks)
            mock_get_agent.return_value = mock_agent

            events = list(stream_agent("show me a map"))

        assert any(e["type"] == "tool_end" for e in events)

    def test_vizbuffer_type_is_map(self):
        from src.agent import stream_agent

        chunks = [
            _agent_chunk_text_object(
                "create_sightings_map", {"observations_json": OBS_JSON}
            ),
        ]
        with patch("src.agent._get_agent") as mock_get_agent:
            mock_agent = MagicMock()
            mock_agent.stream.return_value = iter(chunks)
            mock_get_agent.return_value = mock_agent

            list(stream_agent("show me a map"))

        assert VizBuffer["type"] == "map"

    def test_vizbuffer_data_is_folium_map(self):
        from src.agent import stream_agent

        chunks = [
            _agent_chunk_text_object(
                "create_sightings_map", {"observations_json": OBS_JSON}
            ),
        ]
        with patch("src.agent._get_agent") as mock_get_agent:
            mock_agent = MagicMock()
            mock_agent.stream.return_value = iter(chunks)
            mock_get_agent.return_value = mock_agent

            list(stream_agent("show me a map"))

        assert isinstance(VizBuffer["data"], folium.Map)

    def test_final_event_content_is_confirmation_not_json(self):
        """The 'final' event must carry the tool's confirmation string, not raw JSON."""
        from src.agent import stream_agent

        chunks = [
            _agent_chunk_text_object(
                "create_sightings_map", {"observations_json": OBS_JSON}
            ),
        ]
        with patch("src.agent._get_agent") as mock_get_agent:
            mock_agent = MagicMock()
            mock_agent.stream.return_value = iter(chunks)
            mock_get_agent.return_value = mock_agent

            events = list(stream_agent("show me a map"))

        final = [e for e in events if e["type"] == "final"]
        assert len(final) == 1
        # Must be the tool's human-readable confirmation, not the raw JSON envelope
        assert '"name"' not in final[0]["content"]
        assert "sightings" in final[0]["content"].lower()

    def test_tool_start_name_matches_tool(self):
        from src.agent import stream_agent

        chunks = [
            _agent_chunk_text_object(
                "create_sightings_map", {"observations_json": OBS_JSON}
            ),
        ]
        with patch("src.agent._get_agent") as mock_get_agent:
            mock_agent = MagicMock()
            mock_agent.stream.return_value = iter(chunks)
            mock_get_agent.return_value = mock_agent

            events = list(stream_agent("show me a map"))

        start = next(e for e in events if e["type"] == "tool_start")
        assert start["name"] == "create_sightings_map"


# ---------------------------------------------------------------------------
# stream_agent — text-based tool call fallback (Format A: JSON array)
# ---------------------------------------------------------------------------


class TestStreamAgentFallbackArrayFormat:
    """LLM outputs '[{"name": "...", "arguments": {...}}]' instead of tool_calls."""

    def test_tool_executed_and_vizbuffer_populated(self):
        from src.agent import stream_agent

        chunks = [
            _agent_chunk_text_array(
                "create_sightings_map", {"observations_json": OBS_JSON}
            ),
        ]
        with patch("src.agent._get_agent") as mock_get_agent:
            mock_agent = MagicMock()
            mock_agent.stream.return_value = iter(chunks)
            mock_get_agent.return_value = mock_agent

            events = list(stream_agent("show me a map"))

        assert VizBuffer["type"] == "map"
        assert any(e["type"] == "tool_end" for e in events)

    def test_final_event_is_confirmation_not_json(self):
        from src.agent import stream_agent

        chunks = [
            _agent_chunk_text_array(
                "create_sightings_map", {"observations_json": OBS_JSON}
            ),
        ]
        with patch("src.agent._get_agent") as mock_get_agent:
            mock_agent = MagicMock()
            mock_agent.stream.return_value = iter(chunks)
            mock_get_agent.return_value = mock_agent

            events = list(stream_agent("show me a map"))

        final = [e for e in events if e["type"] == "final"]
        assert len(final) == 1
        assert '"name"' not in final[0]["content"]


# ---------------------------------------------------------------------------
# stream_agent — unrecognised tool name in envelope is silently ignored
# ---------------------------------------------------------------------------


class TestStreamAgentFallbackUnknownTool:
    def test_unknown_tool_yields_no_events(self):
        """An envelope naming an unrecognised tool must not crash or yield events."""
        from src.agent import stream_agent

        chunks = [
            _agent_chunk_text_object("nonexistent_tool", {"foo": "bar"}),
        ]
        with patch("src.agent._get_agent") as mock_get_agent:
            mock_agent = MagicMock()
            mock_agent.stream.return_value = iter(chunks)
            mock_get_agent.return_value = mock_agent

            events = list(stream_agent("do something"))

        # Nothing should be yielded for an unknown tool
        assert events == []
        assert VizBuffer["type"] is None


# ---------------------------------------------------------------------------
# stream_agent — plain JSON that is not a tool call passes through normally
# ---------------------------------------------------------------------------


class TestStreamAgentNonToolJson:
    def test_json_without_name_key_yielded_as_final(self):
        """JSON content that has no 'name' key is a legitimate LLM response."""
        from src.agent import stream_agent

        response_json = '{"species": "Mallard", "count": 5}'
        chunks = [_agent_chunk_final_text(response_json)]

        with patch("src.agent._get_agent") as mock_get_agent:
            mock_agent = MagicMock()
            mock_agent.stream.return_value = iter(chunks)
            mock_get_agent.return_value = mock_agent

            events = list(stream_agent("what did you find?"))

        final = [e for e in events if e["type"] == "final"]
        assert len(final) == 1
        assert final[0]["content"] == response_json


# ---------------------------------------------------------------------------
# _wrap_with_summarizer — retry cap and error-string behaviour
# ---------------------------------------------------------------------------


class TestWrapWithSummarizer:
    """Unit tests for the _wrap_with_summarizer error-handling and retry cap.

    No LLM or eBird API involved — all tests are deterministic.
    """

    @pytest.fixture(autouse=True)
    def reset_error_counts(self):
        """Ensure a clean error counter before and after every test."""
        import src.agent as agent_module
        agent_module._tool_error_counts.clear()
        yield
        agent_module._tool_error_counts.clear()

    def _make_tool(self, func) -> "StructuredTool":
        """Wrap a plain callable in a StructuredTool so _wrap_with_summarizer can process it."""
        from langchain_core.tools import StructuredTool as ST
        return ST.from_function(func=func, name=func.__name__, description="test tool")

    def test_success_returns_result(self):
        """A tool that succeeds returns its value unchanged."""
        from src.agent import _wrap_with_summarizer

        def my_tool() -> str:
            return "ok"

        wrapped = _wrap_with_summarizer(self._make_tool(my_tool))
        assert wrapped.invoke({}) == "ok"

    def test_exception_returned_as_string_not_raised(self):
        """An exception from the underlying tool must be caught and returned as a
        string so the LLM can read the error and retry."""
        from src.agent import _wrap_with_summarizer

        def my_tool() -> str:
            raise ValueError("bad input")

        wrapped = _wrap_with_summarizer(self._make_tool(my_tool))
        result = wrapped.invoke({})
        assert isinstance(result, str)
        assert "bad input" in result

    def test_error_message_includes_retry_hint(self):
        """While retries remain the error string should tell the LLM to retry."""
        from src.agent import _wrap_with_summarizer

        def my_tool() -> str:
            raise RuntimeError("oops")

        wrapped = _wrap_with_summarizer(self._make_tool(my_tool))
        result = wrapped.invoke({})
        assert "retry" in result.lower() or "attempt" in result.lower()

    def test_error_counter_increments_on_each_failure(self):
        """_tool_error_counts[tool_name] must increase with every failed call."""
        import src.agent as agent_module
        from src.agent import _wrap_with_summarizer

        def my_tool() -> str:
            raise RuntimeError("fail")

        wrapped = _wrap_with_summarizer(self._make_tool(my_tool))

        wrapped.invoke({})
        assert agent_module._tool_error_counts.get("my_tool", 0) == 1
        wrapped.invoke({})
        assert agent_module._tool_error_counts.get("my_tool", 0) == 2

    def test_hard_stop_after_max_retries(self):
        """Once _MAX_TOOL_RETRIES errors have been recorded the underlying
        function must NOT be called again and the hard-stop message is returned."""
        import src.agent as agent_module
        from src.agent import _MAX_TOOL_RETRIES, _wrap_with_summarizer

        call_count = 0

        def my_tool() -> str:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("always fails")

        wrapped = _wrap_with_summarizer(self._make_tool(my_tool))

        # Exhaust the retry budget
        for _ in range(_MAX_TOOL_RETRIES):
            wrapped.invoke({})

        assert call_count == _MAX_TOOL_RETRIES

        # This call must be blocked — underlying function not invoked
        result = wrapped.invoke({})
        assert call_count == _MAX_TOOL_RETRIES  # unchanged
        assert "Do not call it again" in result or "already failed" in result

    def test_hard_stop_message_names_the_tool(self):
        """The hard-stop message must include the tool's name."""
        import src.agent as agent_module
        from src.agent import _MAX_TOOL_RETRIES, _wrap_with_summarizer

        def my_tool() -> str:
            raise RuntimeError("fail")

        wrapped = _wrap_with_summarizer(self._make_tool(my_tool))

        for _ in range(_MAX_TOOL_RETRIES):
            wrapped.invoke({})

        result = wrapped.invoke({})
        assert "my_tool" in result

    def test_reset_clears_error_counts(self):
        """_reset_tool_error_counts must zero all counters so the next run
        starts fresh (simulates a new run_agent / stream_agent call)."""
        import src.agent as agent_module
        from src.agent import _reset_tool_error_counts, _wrap_with_summarizer

        def my_tool() -> str:
            raise RuntimeError("fail")

        wrapped = _wrap_with_summarizer(self._make_tool(my_tool))
        wrapped.invoke({})
        assert agent_module._tool_error_counts.get("my_tool", 0) == 1

        _reset_tool_error_counts()
        assert agent_module._tool_error_counts == {}

    def test_successful_call_does_not_increment_counter(self):
        """A successful invocation must leave the error counter untouched."""
        import src.agent as agent_module
        from src.agent import _wrap_with_summarizer

        def my_tool() -> str:
            return "fine"

        wrapped = _wrap_with_summarizer(self._make_tool(my_tool))
        wrapped.invoke({})
        assert agent_module._tool_error_counts.get("my_tool", 0) == 0
