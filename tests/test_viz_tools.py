"""Unit tests for src/tools/viz_tools.py."""

import json

import pytest
from langchain_core.tools import ToolException

from src.tools.viz_tools import create_historical_chart, create_sightings_map
from src.utils.state import VizBuffer, clear_viz_buffer

# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_OBS = [
    {
        "comName": "American Robin",
        "sciName": "Turdus migratorius",
        "speciesCode": "amerob",
        "howMany": 3,
        "lat": 48.85,
        "lng": 2.35,
        "obsDt": "2024-05-01 09:00",
        "locName": "Central Park",
        "locId": "L123",
    },
    {
        "comName": "House Sparrow",
        "sciName": "Passer domesticus",
        "speciesCode": "houspa",
        "howMany": 5,
        "lat": 48.86,
        "lng": 2.36,
        "obsDt": "2024-05-02 10:00",
        "locName": "Tuileries Garden",
        "locId": "L124",
    },
]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_viz_buffer():
    """Clear VizBuffer before and after every test."""
    clear_viz_buffer()
    yield
    clear_viz_buffer()


# ---------------------------------------------------------------------------
# create_sightings_map
# ---------------------------------------------------------------------------


class TestCreateSightingsMap:
    def test_sets_vizbuffer_type_to_map(self):
        create_sightings_map.invoke({"observations_json": json.dumps(SAMPLE_OBS)})
        assert VizBuffer["type"] == "map"

    def test_vizbuffer_data_is_html_string(self):
        create_sightings_map.invoke({"observations_json": json.dumps(SAMPLE_OBS)})
        assert isinstance(VizBuffer["data"], str)
        assert len(VizBuffer["data"]) > 0

    def test_vizbuffer_html_contains_leaflet(self):
        create_sightings_map.invoke({"observations_json": json.dumps(SAMPLE_OBS)})
        # folium maps always embed Leaflet.js
        assert "leaflet" in VizBuffer["data"].lower()

    def test_vizbuffer_title_set(self):
        create_sightings_map.invoke({"observations_json": json.dumps(SAMPLE_OBS)})
        assert VizBuffer["title"] == "Bird Sightings Map"

    def test_returns_count_confirmation(self):
        result = create_sightings_map.invoke({"observations_json": json.dumps(SAMPLE_OBS)})
        assert "2" in result
        assert "sightings" in result.lower()

    def test_no_coordinates_raises_tool_exception(self):
        obs = [{"comName": "Robin", "lat": None, "lng": None}]
        with pytest.raises(ToolException, match="coordinate"):
            create_sightings_map.invoke({"observations_json": json.dumps(obs)})

    def test_missing_lat_lng_keys_raises_tool_exception(self):
        obs = [{"comName": "Robin"}]  # no lat/lng keys
        with pytest.raises(ToolException, match="coordinate"):
            create_sightings_map.invoke({"observations_json": json.dumps(obs)})

    def test_invalid_json_raises_tool_exception(self):
        with pytest.raises(ToolException, match="not valid JSON"):
            create_sightings_map.invoke({"observations_json": "not{valid"})

    def test_non_list_json_raises_tool_exception(self):
        with pytest.raises(ToolException, match="JSON array"):
            create_sightings_map.invoke({"observations_json": '{"key": "value"}'})

    def test_single_observation_works(self):
        single = [SAMPLE_OBS[0]]
        result = create_sightings_map.invoke({"observations_json": json.dumps(single)})
        assert "1" in result
        assert VizBuffer["type"] == "map"

    def test_vizbuffer_cleared_before_new_call(self):
        # First call
        create_sightings_map.invoke({"observations_json": json.dumps(SAMPLE_OBS)})
        first_data = VizBuffer["data"]
        # Second call with different (single) observation
        create_sightings_map.invoke(
            {"observations_json": json.dumps([SAMPLE_OBS[0]])}
        )
        assert VizBuffer["data"] != first_data


# ---------------------------------------------------------------------------
# create_historical_chart
# ---------------------------------------------------------------------------


class TestCreateHistoricalChart:
    def test_bar_chart_sets_vizbuffer_type(self):
        create_historical_chart.invoke(
            {"observations_json": json.dumps(SAMPLE_OBS), "chart_type": "bar"}
        )
        assert VizBuffer["type"] == "chart"

    def test_bar_chart_data_is_dict(self):
        create_historical_chart.invoke(
            {"observations_json": json.dumps(SAMPLE_OBS), "chart_type": "bar"}
        )
        assert isinstance(VizBuffer["data"], dict)

    def test_bar_chart_title_set(self):
        create_historical_chart.invoke(
            {"observations_json": json.dumps(SAMPLE_OBS), "chart_type": "bar"}
        )
        assert VizBuffer["title"] is not None
        assert len(VizBuffer["title"]) > 0

    def test_bar_chart_returns_confirmation(self):
        result = create_historical_chart.invoke(
            {"observations_json": json.dumps(SAMPLE_OBS)}
        )
        assert "chart" in result.lower()
        assert "2" in result

    def test_line_chart_sets_vizbuffer_type(self):
        create_historical_chart.invoke(
            {"observations_json": json.dumps(SAMPLE_OBS), "chart_type": "line"}
        )
        assert VizBuffer["type"] == "chart"

    def test_line_chart_data_is_dict(self):
        create_historical_chart.invoke(
            {"observations_json": json.dumps(SAMPLE_OBS), "chart_type": "line"}
        )
        assert isinstance(VizBuffer["data"], dict)

    def test_default_chart_type_is_bar(self):
        # No chart_type specified — should default to bar and not raise
        create_historical_chart.invoke({"observations_json": json.dumps(SAMPLE_OBS)})
        assert VizBuffer["type"] == "chart"

    def test_top_n_species_limits_bar_chart(self):
        # top_n_species=1 should still produce a chart
        create_historical_chart.invoke(
            {
                "observations_json": json.dumps(SAMPLE_OBS),
                "chart_type": "bar",
                "top_n_species": 1,
            }
        )
        assert VizBuffer["type"] == "chart"

    def test_howmany_none_treated_as_one(self):
        obs = [
            {
                "comName": "Warbler",
                "howMany": None,
                "lat": 48.85,
                "lng": 2.35,
                "obsDt": "2024-05-01 09:00",
            }
        ]
        result = create_historical_chart.invoke({"observations_json": json.dumps(obs)})
        assert "chart" in result.lower()

    def test_empty_observations_raises_tool_exception(self):
        with pytest.raises(ToolException, match="empty"):
            create_historical_chart.invoke({"observations_json": "[]"})

    def test_invalid_json_raises_tool_exception(self):
        with pytest.raises(ToolException, match="not valid JSON"):
            create_historical_chart.invoke({"observations_json": "bad json"})

    def test_non_list_json_raises_tool_exception(self):
        with pytest.raises(ToolException, match="JSON array"):
            create_historical_chart.invoke({"observations_json": '"just a string"'})

    def test_missing_com_name_raises_tool_exception(self):
        obs = [{"speciesCode": "amerob", "howMany": 3, "lat": 48.85, "lng": 2.35}]
        with pytest.raises(ToolException, match="comName"):
            create_historical_chart.invoke({"observations_json": json.dumps(obs)})

    def test_line_chart_missing_obs_dt_raises_tool_exception(self):
        obs = [{"comName": "Robin", "howMany": 3}]
        with pytest.raises(ToolException, match="obsDt"):
            create_historical_chart.invoke(
                {"observations_json": json.dumps(obs), "chart_type": "line"}
            )
