"""UI integration tests for app.py using Streamlit AppTest.

The LLM is never called. stream_agent is patched with generators that
invoke the REAL viz tools so the VizBuffer write path is fully exercised,
including the tool_end snapshot capture in app.py.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from src.tools.viz_tools import create_historical_chart, create_sightings_map
from src.utils.state import VizBuffer, clear_viz_buffer

APP_PATH = str(Path(__file__).parent.parent / "app.py")

# ---------------------------------------------------------------------------
# Sample observations
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
# Mock stream generators — invoke the REAL viz tools so VizBuffer is written
# by the actual tool code, not manually by the test.
# ---------------------------------------------------------------------------


def _stream_with_map(user_input: str, history=None):
    """Simulates: fetch observations → create map."""
    yield {"type": "tool_start", "name": "get_recent_observations_by_location",
           "label": "Fetching recent sightings near location…"}
    yield {"type": "tool_end",   "name": "get_recent_observations_by_location",
           "output": OBS_JSON}

    yield {"type": "tool_start", "name": "create_sightings_map",
           "label": "Rendering sightings map…"}
    result = create_sightings_map.invoke({"observations_json": OBS_JSON})  # writes VizBuffer
    yield {"type": "tool_end",   "name": "create_sightings_map", "output": result}

    yield {"type": "final", "content": "Map created with 2 sightings."}


def _stream_with_bar_chart(user_input: str, history=None):
    """Simulates: fetch observations → create bar chart."""
    yield {"type": "tool_start", "name": "get_historic_observations",
           "label": "Fetching historic observations…"}
    yield {"type": "tool_end",   "name": "get_historic_observations", "output": OBS_JSON}

    yield {"type": "tool_start", "name": "create_historical_chart",
           "label": "Building observations chart…"}
    result = create_historical_chart.invoke(
        {"observations_json": OBS_JSON, "chart_type": "bar"}
    )  # writes VizBuffer
    yield {"type": "tool_end",   "name": "create_historical_chart", "output": result}

    yield {"type": "final", "content": "Bar chart created with 2 records."}


def _stream_text_only(user_input: str, history=None):
    """Simulates a query that produces only text (no viz tool)."""
    yield {"type": "tool_start", "name": "get_region_list",
           "label": "Fetching region list…"}
    yield {"type": "tool_end",   "name": "get_region_list",
           "output": '[{"code": "US-NY", "name": "New York"}, {"code": "US-CA", "name": "California"}]'}
    yield {"type": "final", "content": "The US states are: US-NY, US-CA, …"}


def _stream_raises(user_input: str, history=None):
    """Simulates a stream that raises mid-way (before any tool completes)."""
    yield {"type": "tool_start", "name": "get_recent_observations_by_location",
           "label": "Fetching recent sightings near location…"}
    raise RuntimeError("Simulated LLM failure")
    yield  # unreachable — silences generator lint warning


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fake_api_keys(monkeypatch):
    monkeypatch.setenv("EBIRD_API_KEY", "fake_ebird_key")
    monkeypatch.setenv("HUGGINGFACE_API_TOKEN", "fake_hf_token")


@pytest.fixture(autouse=True)
def reset_buffer():
    clear_viz_buffer()
    yield
    clear_viz_buffer()


# ---------------------------------------------------------------------------
# Helper: build AppTest, submit one query, return at
# ---------------------------------------------------------------------------

def _submit(stream_fn, query: str) -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    with patch("src.agent.stream_agent", side_effect=stream_fn):
        at.run()
        at.chat_input[0].set_value(query).run()
    return at


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


class TestInitialState:
    def test_viz_snapshot_type_is_none_on_load(self):
        at = AppTest.from_file(APP_PATH, default_timeout=30)
        at.run()
        assert at.session_state["viz_snapshot"]["type"] is None

    def test_no_messages_on_load(self):
        at = AppTest.from_file(APP_PATH, default_timeout=30)
        at.run()
        assert at.session_state["messages"] == []

    def test_viz_panel_debug_caption_shows_none(self):
        at = AppTest.from_file(APP_PATH, default_timeout=30)
        at.run()
        captions = [c.value for c in at.caption]
        assert any("none" in c.lower() for c in captions)

    def test_viz_snapshot_table_is_none_on_load(self):
        at = AppTest.from_file(APP_PATH, default_timeout=30)
        at.run()
        assert at.session_state["viz_snapshot"]["table"] is None


# ---------------------------------------------------------------------------
# Map creation — exercises real create_sightings_map tool
# ---------------------------------------------------------------------------


class TestMapCreation:
    def test_viz_snapshot_type_is_map(self):
        at = _submit(_stream_with_map, "Show birds near lat 46.8, lng -71.4")
        assert at.session_state["viz_snapshot"]["type"] == "map"

    def test_viz_snapshot_data_is_folium_map(self):
        """data field holds a folium.Map object (rendered by st_folium in app.py)."""
        import folium as _folium
        at = _submit(_stream_with_map, "Map sightings")
        assert isinstance(at.session_state["viz_snapshot"]["data"], _folium.Map)

    def test_viz_snapshot_title(self):
        at = _submit(_stream_with_map, "Map sightings")
        assert at.session_state["viz_snapshot"]["title"] == "Bird Sightings Map"

    def test_debug_caption_shows_map(self):
        at = _submit(_stream_with_map, "Map sightings")
        captions = [c.value for c in at.caption]
        assert any("map" in c.lower() for c in captions)

    def test_assistant_message_content(self):
        at = _submit(_stream_with_map, "Show me a map")
        assistant = [m for m in at.session_state["messages"] if m["role"] == "assistant"]
        assert len(assistant) == 1
        assert "map" in assistant[0]["content"].lower()

    def test_snapshot_captured_at_tool_end_without_final_event(self):
        """VizBuffer must be snapshotted at tool_end.
        A stream with no 'final' event should still produce a viz_snapshot."""
        def _no_final(user_input, history=None):
            yield {"type": "tool_start", "name": "create_sightings_map",
                   "label": "Rendering sightings map…"}
            result = create_sightings_map.invoke({"observations_json": OBS_JSON})
            yield {"type": "tool_end", "name": "create_sightings_map", "output": result}
            # intentionally no final event

        at = _submit(_no_final, "Map birds")
        assert at.session_state["viz_snapshot"]["type"] == "map"
    def test_viz_snapshot_table_populated(self):
        at = _submit(_stream_with_map, "Map sightings")
        table = at.session_state["viz_snapshot"]["table"]
        assert isinstance(table, list) and len(table) > 0

    def test_viz_snapshot_table_has_expected_columns(self):
        at = _submit(_stream_with_map, "Map sightings")
        row = at.session_state["viz_snapshot"]["table"][0]
        for col in ("Species", "Count", "Location", "Date"):
            assert col in row, f"Missing column: {col}"

# ---------------------------------------------------------------------------
# Chart creation — exercises real create_historical_chart tool
# ---------------------------------------------------------------------------


class TestChartCreation:
    def test_viz_snapshot_type_is_chart(self):
        at = _submit(_stream_with_bar_chart, "Chart observations for US-NY")
        assert at.session_state["viz_snapshot"]["type"] == "chart"

    def test_viz_snapshot_data_is_plotly_dict(self):
        at = _submit(_stream_with_bar_chart, "Chart observations")
        data = at.session_state["viz_snapshot"]["data"]
        assert isinstance(data, dict)
        assert "data" in data  # plotly figure dicts always have a 'data' key

    def test_viz_snapshot_title_set(self):
        at = _submit(_stream_with_bar_chart, "Plot species counts")
        title = at.session_state["viz_snapshot"]["title"]
        assert title and len(title) > 0

    def test_debug_caption_shows_chart(self):
        at = _submit(_stream_with_bar_chart, "Chart observations")
        captions = [c.value for c in at.caption]
        assert any("chart" in c.lower() for c in captions)

    def test_assistant_message_content(self):
        at = _submit(_stream_with_bar_chart, "Plot me a chart")
        assistant = [m for m in at.session_state["messages"] if m["role"] == "assistant"]
        assert len(assistant) == 1
        assert "chart" in assistant[0]["content"].lower()


# ---------------------------------------------------------------------------
# Text-only response (no viz tool called)
# ---------------------------------------------------------------------------


class TestTextOnlyResponse:
    def test_viz_snapshot_stays_none(self):
        at = _submit(_stream_text_only, "List US states")
        assert at.session_state["viz_snapshot"]["type"] is None

    def test_viz_snapshot_table_stays_none(self):
        at = _submit(_stream_text_only, "List US states")
        assert at.session_state["viz_snapshot"]["table"] is None

    def test_assistant_message_added(self):
        at = _submit(_stream_text_only, "List US states")
        assistant = [m for m in at.session_state["messages"] if m["role"] == "assistant"]
        assert len(assistant) == 1
        assert "us" in assistant[0]["content"].lower()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_error_message_shown_in_chat(self):
        at = _submit(_stream_raises, "Show me birds")
        assistant = [m for m in at.session_state["messages"] if m["role"] == "assistant"]
        assert len(assistant) == 1
        assert "error" in assistant[0]["content"].lower()

    def test_viz_snapshot_not_set_when_tool_errors(self):
        """Tools never completed so viz_snapshot should remain empty."""
        at = _submit(_stream_raises, "Show me birds")
        assert at.session_state["viz_snapshot"]["type"] is None


# ---------------------------------------------------------------------------
# Conversation flow (multi-turn)
# ---------------------------------------------------------------------------


class TestConversationFlow:
    def test_messages_accumulate_across_turns(self):
        at = AppTest.from_file(APP_PATH, default_timeout=30)
        with patch("src.agent.stream_agent", side_effect=_stream_with_map):
            at.run()
            at.chat_input[0].set_value("Show birds near me").run()
        with patch("src.agent.stream_agent", side_effect=_stream_with_bar_chart):
            at.chat_input[0].set_value("Now chart those").run()

        user_msgs = [m for m in at.session_state["messages"] if m["role"] == "user"]
        assert len(user_msgs) == 2

    def test_viz_snapshot_updates_to_latest_viz(self):
        """After map then chart query, snapshot should reflect the chart."""
        at = AppTest.from_file(APP_PATH, default_timeout=30)
        with patch("src.agent.stream_agent", side_effect=_stream_with_map):
            at.run()
            at.chat_input[0].set_value("Show birds near me").run()
        assert at.session_state["viz_snapshot"]["type"] == "map"

        with patch("src.agent.stream_agent", side_effect=_stream_with_bar_chart):
            at.chat_input[0].set_value("Now chart those").run()
        assert at.session_state["viz_snapshot"]["type"] == "chart"

    def test_no_error_text_in_normal_responses(self):
        at = _submit(_stream_with_map, "Show birds near me")
        for msg in at.session_state["messages"]:
            assert "error occurred" not in msg["content"].lower()
