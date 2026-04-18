"""
test_agent_consistency.py

Verifies the consistency between what an eBird tool returns (the API call)
and what ends up rendered in a map or chart (the VizBuffer).

Three failure modes are caught here:
  1. Data pipeline mutation — the JSON flowing from an eBird tool to a viz
     tool is silently altered (records dropped, fields changed).
  2. Date/semantic mismatch — a "recent observations" response is backed by
     data outside the requested window; or a "historic" response shows the
     wrong date.
  3. Count mismatch — the record count the LLM reports doesn't match what
     the viz tool actually rendered.

The tests do NOT call a real LLM or the real eBird API.  They mock the API
client and simulate the tool-call sequence the agent would execute, then
assert that VizBuffer ends up in a state consistent with that sequence.
"""

import datetime
import json
import re
from unittest.mock import MagicMock, patch

import pytest

import src.tools.ebird_tools as ebird_tools_module
import src.utils.region_cache as region_cache_module
from src.tools.ebird_tools import (
    get_historic_observations,
    get_notable_observations_by_location,
    get_recent_observations_by_location,
    get_recent_observations_by_region,
)
from src.tools.viz_tools import create_historical_chart, create_sightings_map
from src.utils.state import VizBuffer, clear_viz_buffer

# ---------------------------------------------------------------------------
# Fixed test dates so assertions are deterministic
# ---------------------------------------------------------------------------

_TODAY = datetime.date.today()
_YESTERDAY = _TODAY - datetime.timedelta(days=1)
_THREE_DAYS_AGO = _TODAY - datetime.timedelta(days=3)

# ---------------------------------------------------------------------------
# Sample API responses
# ---------------------------------------------------------------------------

RECENT_OBS = [
    {
        "comName": "American Robin",
        "sciName": "Turdus migratorius",
        "speciesCode": "amerob",
        "howMany": 3,
        "lat": 40.78,
        "lng": -73.97,
        "obsDt": _YESTERDAY.strftime("%Y-%m-%d 09:00"),
        "locName": "Central Park",
        "locId": "L123",
    },
    {
        "comName": "House Sparrow",
        "sciName": "Passer domesticus",
        "speciesCode": "houspa",
        "howMany": 7,
        "lat": 40.79,
        "lng": -73.96,
        "obsDt": _THREE_DAYS_AGO.strftime("%Y-%m-%d 10:30"),
        "locName": "Riverside Park",
        "locId": "L124",
    },
]

HISTORIC_OBS = [
    {
        "comName": "Northern Cardinal",
        "sciName": "Cardinalis cardinalis",
        "speciesCode": "norcar",
        "howMany": 2,
        "lat": 42.35,
        "lng": -71.06,
        "obsDt": "2024-05-01 08:00",
        "locName": "Boston Common",
        "locId": "L200",
    },
    {
        "comName": "Blue Jay",
        "sciName": "Cyanocitta cristata",
        "speciesCode": "blujay",
        "howMany": 4,
        "lat": 42.36,
        "lng": -71.07,
        "obsDt": "2024-05-01 09:30",
        "locName": "Fenway Park",
        "locId": "L201",
    },
]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_state():
    """Reset the module-level eBird client, region cache, and VizBuffer."""
    ebird_tools_module._client = None
    original_codes = region_cache_module._known_codes.copy()
    original_loaded = region_cache_module._cache_loaded
    region_cache_module._known_codes.clear()
    region_cache_module._cache_loaded = True  # skip disk load
    clear_viz_buffer()
    yield
    ebird_tools_module._client = None
    region_cache_module._known_codes.clear()
    region_cache_module._known_codes.update(original_codes)
    region_cache_module._cache_loaded = original_loaded
    clear_viz_buffer()


@pytest.fixture
def mock_client():
    """Patch _get_client so the real HTTP client is never called."""
    with patch("src.tools.ebird_tools._get_client") as mock_get:
        client = MagicMock()
        mock_get.return_value = client
        yield client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _map_species(table: list[dict]) -> set[str]:
    """Set of 'Species' values from a VizBuffer table."""
    return {row["Species"] for row in table}


def _map_dates(table: list[dict]) -> list[datetime.date]:
    """Parse all Date strings from a VizBuffer table into datetime.date objects."""
    dates = []
    for row in table:
        raw = row.get("Date", "")
        if raw:
            dates.append(datetime.date.fromisoformat(raw[:10]))
    return dates


def _chart_species(vizbuffer_data: dict) -> set[str]:
    """Extract species names from the Plotly bar-chart figure dict in VizBuffer.

    px.bar(color='comName') creates one trace per species; each trace carries
    its species name in the 'name' field rather than across a shared x-axis.
    """
    traces = vizbuffer_data.get("data", [])
    names: set[str] = set()
    for trace in traces:
        name = trace.get("name")
        if name:
            names.add(name)
    return names


def _chart_record_count(return_value: str) -> int | None:
    """Parse the integer record count from a viz-tool return string."""
    m = re.search(r"(\d+)\s+records?", return_value, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _vizbuffer_dates_all_within(days_back: int) -> bool:
    """True if every date in VizBuffer['table'] is within days_back of today."""
    cutoff = _TODAY - datetime.timedelta(days=days_back)
    for d in _map_dates(VizBuffer.get("table") or []):
        if d < cutoff:
            return False
    return True


def _vizbuffer_dates_all_equal(year: int, month: int, day: int) -> bool:
    """True if every date in VizBuffer['table'] matches the given date."""
    expected = datetime.date(year, month, day)
    dates = _map_dates(VizBuffer.get("table") or [])
    return bool(dates) and all(d == expected for d in dates)


# ---------------------------------------------------------------------------
# 1. Data pipeline integrity
#    The observations returned by an eBird tool must be the same data that
#    ends up in VizBuffer — nothing dropped, nothing fabricated.
# ---------------------------------------------------------------------------


class TestDataPipelineIntegrity:
    """eBird tool output ↔ VizBuffer must be identical."""

    def test_recent_location_to_map_species_match(self, mock_client):
        """Every species from the API response must appear in the map table."""
        mock_client.recent_observations_by_location.return_value = RECENT_OBS

        obs_json = get_recent_observations_by_location.invoke({"lat": 40.78, "lng": -73.97})
        create_sightings_map.invoke({"observations_json": obs_json})

        assert _map_species(VizBuffer["table"]) == {o["comName"] for o in RECENT_OBS}

    def test_recent_location_to_map_no_extra_records(self, mock_client):
        """Map must not fabricate records beyond what the API returned."""
        mock_client.recent_observations_by_location.return_value = RECENT_OBS

        obs_json = get_recent_observations_by_location.invoke({"lat": 40.78, "lng": -73.97})
        create_sightings_map.invoke({"observations_json": obs_json})

        assert len(VizBuffer["table"]) <= len(RECENT_OBS)

    def test_recent_region_to_chart_species_match(self, mock_client):
        """Every species from the API response must appear in the chart figure."""
        mock_client.recent_observations_by_region.return_value = RECENT_OBS

        obs_json = get_recent_observations_by_region.invoke({"region_code": "US-NY"})
        result = create_historical_chart.invoke({"observations_json": obs_json})

        chart_species = _chart_species(VizBuffer["data"])
        api_species = {o["comName"] for o in RECENT_OBS}
        assert api_species == chart_species

    def test_historic_to_map_species_match(self, mock_client):
        """Historic observations must all appear in the map table."""
        mock_client.historic_observations.return_value = HISTORIC_OBS

        obs_json = get_historic_observations.invoke(
            {"region_code": "US-MA", "year": 2024, "month": 5, "day": 1}
        )
        create_sightings_map.invoke({"observations_json": obs_json})

        assert _map_species(VizBuffer["table"]) == {o["comName"] for o in HISTORIC_OBS}

    def test_historic_to_chart_species_match(self, mock_client):
        """Historic observations must all appear in the chart figure."""
        mock_client.historic_observations.return_value = HISTORIC_OBS

        obs_json = get_historic_observations.invoke(
            {"region_code": "US-MA", "year": 2024, "month": 5, "day": 1}
        )
        create_historical_chart.invoke({"observations_json": obs_json})

        chart_species = _chart_species(VizBuffer["data"])
        api_species = {o["comName"] for o in HISTORIC_OBS}
        assert api_species == chart_species

    def test_viz_tool_receives_unmodified_ebird_json(self, mock_client):
        """The JSON the viz tool receives must decode to the exact API response."""
        mock_client.recent_observations_by_region.return_value = RECENT_OBS

        captured: dict = {}

        import src.tools.viz_tools as viz_module
        original_parse = viz_module._parse_obs

        def capturing_parse(obs_json: str) -> list:
            captured["obs_json"] = obs_json
            return original_parse(obs_json)

        with patch("src.tools.viz_tools._parse_obs", side_effect=capturing_parse):
            obs_json = get_recent_observations_by_region.invoke({"region_code": "US-NY"})
            create_sightings_map.invoke({"observations_json": obs_json})

        assert captured["obs_json"] == obs_json, (
            "observations_json passed to viz tool differs from the eBird tool output."
        )
        assert json.loads(captured["obs_json"]) == RECENT_OBS, (
            "Decoded observations_json does not match the original API response."
        )

    def test_notable_obs_to_map_species_match(self, mock_client):
        """Notable (rare) observations flow correctly to the map."""
        mock_client.notable_observations_by_location.return_value = RECENT_OBS

        obs_json = get_notable_observations_by_location.invoke(
            {"lat": 40.78, "lng": -73.97}
        )
        create_sightings_map.invoke({"observations_json": obs_json})

        assert _map_species(VizBuffer["table"]) == {o["comName"] for o in RECENT_OBS}


# ---------------------------------------------------------------------------
# 2. Date/semantic consistency
#    Dates in VizBuffer must match the semantics of the tool that was called.
# ---------------------------------------------------------------------------


class TestDateConsistency:
    """Dates in VizBuffer must match what the eBird tool was asked to fetch."""

    def test_recent_obs_map_dates_within_window(self, mock_client):
        """Map data from a recent-observations tool must all fall within days_back."""
        days_back = 7
        mock_client.recent_observations_by_location.return_value = RECENT_OBS

        obs_json = get_recent_observations_by_location.invoke(
            {"lat": 40.78, "lng": -73.97, "days_back": days_back}
        )
        create_sightings_map.invoke({"observations_json": obs_json})

        assert _vizbuffer_dates_all_within(days_back), (
            f"Map contains dates outside the {days_back}-day recent window. "
            "If the LLM claims 'recent sightings', the data must match."
        )

    def test_historic_obs_map_dates_match_request(self, mock_client):
        """Map data from a historic-observations tool must all be from the requested date."""
        mock_client.historic_observations.return_value = HISTORIC_OBS

        obs_json = get_historic_observations.invoke(
            {"region_code": "US-MA", "year": 2024, "month": 5, "day": 1}
        )
        create_sightings_map.invoke({"observations_json": obs_json})

        assert _vizbuffer_dates_all_equal(2024, 5, 1), (
            "Map contains dates other than 2024-05-01. "
            "If the LLM says 'observed on May 1 2024', the data must match."
        )

    def test_historic_obs_chart_dates_match_request(self, mock_client):
        """Chart data from a historic-observations tool must be from the requested date."""
        mock_client.historic_observations.return_value = HISTORIC_OBS

        obs_json = get_historic_observations.invoke(
            {"region_code": "US-MA", "year": 2024, "month": 5, "day": 1}
        )
        create_historical_chart.invoke(
            {"observations_json": obs_json, "chart_type": "line"}
        )

        # The line chart encodes dates; verify them from VizBuffer data directly.
        traces = VizBuffer["data"].get("data", [])
        all_dates: list[datetime.date] = []
        for trace in traces:
            for raw_date in trace.get("x", []):
                all_dates.append(datetime.date.fromisoformat(str(raw_date)[:10]))

        assert all_dates, "No date data found in chart traces."
        assert all(d == datetime.date(2024, 5, 1) for d in all_dates), (
            "Chart contains dates other than 2024-05-01 for a historic request."
        )

    def test_recent_region_obs_map_dates_within_window(self, mock_client):
        """Map from recent-region tool must have dates within the requested window."""
        days_back = 14
        mock_client.recent_observations_by_region.return_value = RECENT_OBS

        obs_json = get_recent_observations_by_region.invoke(
            {"region_code": "US-NY", "days_back": days_back}
        )
        create_sightings_map.invoke({"observations_json": obs_json})

        assert _vizbuffer_dates_all_within(days_back), (
            f"Map contains dates outside the {days_back}-day recent window."
        )

    def test_stale_data_from_recent_tool_is_detected(self, mock_client):
        """If a recent-observations tool mistakenly returns old data, the date
        check must fail — ensuring the LLM cannot claim 'recent' for stale data."""
        stale_obs = [{**RECENT_OBS[0], "obsDt": "2010-01-01 09:00"}]
        mock_client.recent_observations_by_location.return_value = stale_obs

        obs_json = get_recent_observations_by_location.invoke({"lat": 40.78, "lng": -73.97})
        create_sightings_map.invoke({"observations_json": obs_json})

        # The date check should report the data is NOT within a 30-day window.
        assert not _vizbuffer_dates_all_within(days_back=30), (
            "Expected stale date (2010-01-01) to fail the recent-data check, "
            "but it passed — the LLM could falsely claim these are recent sightings."
        )


# ---------------------------------------------------------------------------
# 3. Count consistency
#    The record count in the viz-tool return value must match what was rendered.
# ---------------------------------------------------------------------------


class TestCountConsistency:
    """The count reported by the viz tool must match VizBuffer content."""

    def test_map_return_value_count_matches_api_records(self, mock_client):
        """'Map created with N sightings' must equal the number of API records."""
        mock_client.recent_observations_by_location.return_value = RECENT_OBS

        obs_json = get_recent_observations_by_location.invoke({"lat": 40.78, "lng": -73.97})
        result = create_sightings_map.invoke({"observations_json": obs_json})

        m = re.search(r"(\d+)\s+sightings?", result, re.IGNORECASE)
        assert m, f"Could not parse sighting count from: {result!r}"
        reported_count = int(m.group(1))
        assert reported_count == len(RECENT_OBS), (
            f"Map reports {reported_count} sightings but the API returned {len(RECENT_OBS)} records."
        )

    def test_chart_return_value_count_matches_api_records(self, mock_client):
        """'Chart created with N records' must equal the number of API records."""
        mock_client.historic_observations.return_value = HISTORIC_OBS

        obs_json = get_historic_observations.invoke(
            {"region_code": "US-MA", "year": 2024, "month": 5, "day": 1}
        )
        result = create_historical_chart.invoke({"observations_json": obs_json})

        count = _chart_record_count(result)
        assert count is not None, f"Could not parse record count from: {result!r}"
        assert count == len(HISTORIC_OBS), (
            f"Chart reports {count} records but the API returned {len(HISTORIC_OBS)}."
        )

    def test_map_table_count_does_not_exceed_api_records(self, mock_client):
        """VizBuffer table must not contain more rows than the API returned."""
        mock_client.recent_observations_by_region.return_value = RECENT_OBS

        obs_json = get_recent_observations_by_region.invoke({"region_code": "US-NY"})
        create_sightings_map.invoke({"observations_json": obs_json})

        assert len(VizBuffer["table"]) <= len(RECENT_OBS)

    def test_chart_species_count_does_not_exceed_api_species(self, mock_client):
        """Chart must not show more species than the API returned."""
        mock_client.recent_observations_by_region.return_value = RECENT_OBS

        obs_json = get_recent_observations_by_region.invoke({"region_code": "US-NY"})
        create_historical_chart.invoke({"observations_json": obs_json})

        api_species_count = len({o["comName"] for o in RECENT_OBS})
        chart_species_count = len(_chart_species(VizBuffer["data"]))
        assert chart_species_count <= api_species_count

    def test_multi_observation_counts_aggregated_correctly_in_chart(self, mock_client):
        """Two records for the same species must be collapsed into one chart bar."""
        # Two records for the same species — the chart must deduplicate them.
        duplicate_species_obs = [
            {**RECENT_OBS[0], "howMany": 3},
            {**RECENT_OBS[0], "howMany": 5, "locId": "L999", "locName": "Other Park"},
        ]
        mock_client.recent_observations_by_region.return_value = duplicate_species_obs

        obs_json = get_recent_observations_by_region.invoke({"region_code": "US-NY"})
        result = create_historical_chart.invoke({"observations_json": obs_json})

        # The return value must report both source records.
        count = _chart_record_count(result)
        assert count == 2, (
            f"Expected 2 source records reported but got {count}."
        )

        # The chart figure must contain exactly 1 bar (one unique species).
        chart_species = _chart_species(VizBuffer["data"])
        assert len(chart_species) == 1, (
            f"Expected 1 aggregated species bar but got {len(chart_species)}: {chart_species}."
        )
        assert RECENT_OBS[0]["comName"] in chart_species
