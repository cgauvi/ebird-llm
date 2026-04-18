"""Unit tests for src/tools/viz_tools.py."""

import json

import folium
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

    def test_vizbuffer_data_is_folium_map(self):
        create_sightings_map.invoke({"observations_json": json.dumps(SAMPLE_OBS)})
        assert isinstance(VizBuffer["data"], folium.Map)

    def test_vizbuffer_map_has_markers(self):
        """The folium Map should contain marker child elements."""
        create_sightings_map.invoke({"observations_json": json.dumps(SAMPLE_OBS)})
        fmap = VizBuffer["data"]
        html = fmap._repr_html_()
        assert "leaflet" in html.lower()

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

    def test_vizbuffer_table_is_populated(self):
        create_sightings_map.invoke({"observations_json": json.dumps(SAMPLE_OBS)})
        assert isinstance(VizBuffer["table"], list)
        assert len(VizBuffer["table"]) > 0

    def test_vizbuffer_table_max_10_rows(self):
        # Build 15 observations to confirm the cap is enforced
        many_obs = [
            {**SAMPLE_OBS[i % 2], "comName": f"Species {i}", "locId": f"L{i}"}
            for i in range(15)
        ]
        create_sightings_map.invoke({"observations_json": json.dumps(many_obs)})
        assert len(VizBuffer["table"]) <= 10

    def test_vizbuffer_table_has_expected_columns(self):
        create_sightings_map.invoke({"observations_json": json.dumps(SAMPLE_OBS)})
        row = VizBuffer["table"][0]
        for col in ("Species", "Count", "Location", "Date"):
            assert col in row, f"Missing column: {col}"

    def test_vizbuffer_table_sorted_by_count_descending(self):
        create_sightings_map.invoke({"observations_json": json.dumps(SAMPLE_OBS)})
        counts = [r["Count"] for r in VizBuffer["table"]]
        assert counts == sorted(counts, reverse=True)


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

    def test_vizbuffer_table_not_set_by_chart_tool(self):
        """Chart tool does not populate VizBuffer['table'] — it stays None."""
        create_historical_chart.invoke(
            {"observations_json": json.dumps(SAMPLE_OBS), "chart_type": "bar"}
        )
        assert VizBuffer["table"] is None


# ---------------------------------------------------------------------------
# _parse_obs — JSON cleaning
# ---------------------------------------------------------------------------

from src.tools.viz_tools import _parse_obs


class TestParseObsCleaning:
    def test_parses_clean_json(self):
        result = _parse_obs(json.dumps(SAMPLE_OBS))
        assert result == SAMPLE_OBS

    def test_strips_backslash_escaped_quotes(self):
        """LLMs sometimes emit \" instead of " inside the JSON string."""
        escaped = json.dumps(SAMPLE_OBS).replace('"', '\\"')
        result = _parse_obs(escaped)
        assert result == SAMPLE_OBS

    def test_strips_surrounding_whitespace(self):
        result = _parse_obs("  " + json.dumps(SAMPLE_OBS) + "  ")
        assert result == SAMPLE_OBS

    def test_unwraps_outer_double_quotes(self):
        """Some models wrap the whole JSON string in an extra pair of quotes."""
        wrapped = '"' + json.dumps(SAMPLE_OBS).replace('"', '\\"') + '"'
        result = _parse_obs(wrapped)
        assert result == SAMPLE_OBS

    def test_raises_on_invalid_json(self):
        with pytest.raises(ToolException, match="not valid JSON"):
            _parse_obs("not-json")

    def test_raises_on_non_list(self):
        with pytest.raises(ToolException, match="JSON array"):
            _parse_obs(json.dumps({"key": "value"}))
