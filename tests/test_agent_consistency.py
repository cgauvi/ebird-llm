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
from pathlib import Path
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
import pandas as pd

from src.tools.viz_tools import create_historical_chart, create_sightings_map, show_observations_table
from src.utils.state import VizBuffer, clear_viz_buffer, get_last_obs_file

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

# Species-filtered observations that mirror the exact failure the user reported:
# get_recent_observations_by_location(species_code='y00678') returned records
# whose dates were months in the past, while the map correctly used fresh data.
# These dates are intentionally far outside any reasonable days_back window so
# that every recency assertion that should fail actually does fail.
_STALE_DATE_1 = datetime.date(2024, 4, 18)
_STALE_DATE_2 = datetime.date(2024, 4, 20)
_STALE_DATE_3 = datetime.date(2024, 4, 22)

SPECIES_FILTERED_STALE_OBS = [
    {
         "speciesCode": "y00678",
        "comName": "Crested Caracara",
        "sciName": "Caracara plancus",
        "howMany": 1,
        "lat": 46.78,
        "lng": -71.35,
        "obsDt": _STALE_DATE_1.strftime("%Y-%m-%d 08:00"),
        "locName": "Parc de la Cité",
        "locId": "L600",
    },
    {
         "speciesCode": "y00678",
        "comName": "Crested Caracara",
        "sciName": "Caracara plancus",
        "howMany": 1,
        "lat": 46.85,
        "lng": -71.00,
        "obsDt": _STALE_DATE_2.strftime("%Y-%m-%d 09:30"),
        "locName": "Île d'Orléans",
        "locId": "L601",
    },
    {
         "speciesCode": "y00678",
        "comName": "Crested Caracara",
        "sciName": "Caracara plancus",
        "howMany": 1,
        "lat": 45.52,
        "lng": -73.59,
        "obsDt": _STALE_DATE_3.strftime("%Y-%m-%d 10:15"),
        "locName": "Mont-Royal",
        "locId": "L602",
    },
]

# Snow Goose sightings near Quebec City — mirrors the fixed LLM response in
# TestLLMOutputMapConsistency.  Only goose species are present so the
# "goose-only" assertion must pass.
QUEBEC_GOOSE_OBS = [
    {
        "comName": "Snow Goose",
        "sciName": "Anser caerulescens",
        "speciesCode": "snogoo",
        "howMany": 12,
        "lat": 46.82,
        "lng": -71.20,
        "obsDt": _YESTERDAY.strftime("%Y-%m-%d 07:30"),
        "locName": "Beauport Shore",
        "locId": "L500",
    },
    {
        "comName": "Snow Goose",
        "sciName": "Anser caerulescens",
        "speciesCode": "snogoo",
        "howMany": 5,
        "lat": 46.80,
        "lng": -71.22,
        "obsDt": _YESTERDAY.strftime("%Y-%m-%d 08:15"),
        "locName": "Cap Tourmente",
        "locId": "L501",
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
    original_fetched = region_cache_module._fully_fetched.copy()
    original_loaded = region_cache_module._cache_loaded
    region_cache_module._known_codes.clear()
    region_cache_module._fully_fetched.clear()
    region_cache_module._cache_loaded = True  # skip disk load
    clear_viz_buffer()
    yield
    ebird_tools_module._client = None
    region_cache_module._known_codes.clear()
    region_cache_module._known_codes.update(original_codes)
    region_cache_module._fully_fetched.clear()
    region_cache_module._fully_fetched.update(original_fetched)
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
        create_sightings_map.invoke({})

        assert _map_species(VizBuffer["table"]) == {o["comName"] for o in RECENT_OBS}

    def test_recent_location_to_map_no_extra_records(self, mock_client):
        """Map must not fabricate records beyond what the API returned."""
        mock_client.recent_observations_by_location.return_value = RECENT_OBS

        obs_json = get_recent_observations_by_location.invoke({"lat": 40.78, "lng": -73.97})
        create_sightings_map.invoke({})

        assert len(VizBuffer["table"]) <= len(RECENT_OBS)

    def test_recent_region_to_chart_species_match(self, mock_client):
        """Every species from the API response must appear in the chart figure."""
        mock_client.recent_observations_by_region.return_value = RECENT_OBS

        obs_json = get_recent_observations_by_region.invoke({"region_code": "US-NY"})
        result = create_historical_chart.invoke({})

        chart_species = _chart_species(VizBuffer["data"])
        api_species = {o["comName"] for o in RECENT_OBS}
        assert api_species == chart_species

    def test_historic_to_map_species_match(self, mock_client):
        """Historic observations must all appear in the map table."""
        mock_client.historic_observations.return_value = HISTORIC_OBS

        obs_json = get_historic_observations.invoke(
            {"region_code": "US-MA", "year": 2024, "month": 5, "day": 1}
        )
        create_sightings_map.invoke({})

        assert _map_species(VizBuffer["table"]) == {o["comName"] for o in HISTORIC_OBS}

    def test_historic_to_chart_species_match(self, mock_client):
        """Historic observations must all appear in the chart figure."""
        mock_client.historic_observations.return_value = HISTORIC_OBS

        obs_json = get_historic_observations.invoke(
            {"region_code": "US-MA", "year": 2024, "month": 5, "day": 1}
        )
        create_historical_chart.invoke({})

        chart_species = _chart_species(VizBuffer["data"])
        api_species = {o["comName"] for o in HISTORIC_OBS}
        assert api_species == chart_species

    def test_viz_tool_receives_unmodified_ebird_json(self, mock_client):
        """The JSON written to the temp file by the eBird tool must match the API response."""
        mock_client.recent_observations_by_region.return_value = RECENT_OBS

        get_recent_observations_by_region.invoke({"region_code": "US-NY"})

        obs_file = get_last_obs_file()
        assert obs_file is not None, "Expected _return_obs to set a last-obs file path."
        saved = json.loads(Path(obs_file).read_text(encoding="utf-8"))
        assert saved == RECENT_OBS, (
            "Observations saved to temp file do not match the original API response."
        )

        create_sightings_map.invoke({})
        assert _map_species(VizBuffer["table"]) == {o["comName"] for o in RECENT_OBS}

    def test_notable_obs_to_map_species_match(self, mock_client):
        """Notable (rare) observations flow correctly to the map."""
        mock_client.notable_observations_by_location.return_value = RECENT_OBS

        obs_json = get_notable_observations_by_location.invoke(
            {"lat": 40.78, "lng": -73.97}
        )
        create_sightings_map.invoke({})

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
        create_sightings_map.invoke({})

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
        create_sightings_map.invoke({})

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
        create_historical_chart.invoke({"chart_type": "line"})

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
        create_sightings_map.invoke({})

        assert _vizbuffer_dates_all_within(days_back), (
            f"Map contains dates outside the {days_back}-day recent window."
        )

    def test_stale_data_from_recent_tool_is_detected(self, mock_client):
        """If a recent-observations tool mistakenly returns old data, the date
        check must fail — ensuring the LLM cannot claim 'recent' for stale data."""
        stale_obs = [{**RECENT_OBS[0], "obsDt": "2010-01-01 09:00"}]
        mock_client.recent_observations_by_location.return_value = stale_obs

        obs_json = get_recent_observations_by_location.invoke({"lat": 40.78, "lng": -73.97})
        create_sightings_map.invoke({})

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
        result = create_sightings_map.invoke({})

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
        result = create_historical_chart.invoke({})

        count = _chart_record_count(result)
        assert count is not None, f"Could not parse record count from: {result!r}"
        assert count == len(HISTORIC_OBS), (
            f"Chart reports {count} records but the API returned {len(HISTORIC_OBS)}."
        )

    def test_map_table_count_does_not_exceed_api_records(self, mock_client):
        """VizBuffer table must not contain more rows than the API returned."""
        mock_client.recent_observations_by_region.return_value = RECENT_OBS

        obs_json = get_recent_observations_by_region.invoke({"region_code": "US-NY"})
        create_sightings_map.invoke({})

        assert len(VizBuffer["table"]) <= len(RECENT_OBS)

    def test_chart_species_count_does_not_exceed_api_species(self, mock_client):
        """Chart must not show more species than the API returned."""
        mock_client.recent_observations_by_region.return_value = RECENT_OBS

        obs_json = get_recent_observations_by_region.invoke({"region_code": "US-NY"})
        create_historical_chart.invoke({})

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
        result = create_historical_chart.invoke({})

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


# ---------------------------------------------------------------------------
# 4. LLM output ↔ map/dataframe consistency
#    The species described in the LLM response must match what was actually
#    rendered in the VizBuffer map table and dataframe.
#
#    Scenario: the LLM says —
#      "Here's a map of the recent Snow Goose sightings (the most common
#       'goose' species) within a 25 km radius of Quebec City
#       (lat 46.8139, lng −71.208)."
#    The API returned only Snow Goose records, so the map must reflect that.
# ---------------------------------------------------------------------------


class TestLLMOutputMapConsistency:
    """Species named in the LLM response must agree with VizBuffer content.

    None of these tests call a real LLM or the real eBird API.  The LLM
    response is a fixed string; the API is mocked.
    """

    # Fixed LLM output — no real LLM is invoked.
    _LLM_RESPONSE = (
        "Here's a map of the recent Snow Goose sightings (the most common "
        '"goose" species) within a 25 km radius of Quebec City '
        "(lat 46.8139, lng \u221271.208)."
    )

    def test_llm_claimed_species_present_in_map_table(self, mock_client):
        """Species explicitly named in the LLM response must appear in VizBuffer['table']."""
        mock_client.recent_observations_by_location.return_value = QUEBEC_GOOSE_OBS

        get_recent_observations_by_location.invoke(
            {"lat": 46.8139, "lng": -71.208, "dist_km": 25}
        )
        create_sightings_map.invoke({})

        table_species = _map_species(VizBuffer["table"])
        assert "Snow Goose" in table_species, (
            f"LLM claimed 'Snow Goose' sightings but VizBuffer table contains: {table_species}"
        )

    def test_map_table_contains_only_goose_species(self, mock_client):
        """When the LLM describes goose sightings, the dataframe must contain
        only goose species — no incidental non-goose records."""
        mock_client.recent_observations_by_location.return_value = QUEBEC_GOOSE_OBS

        get_recent_observations_by_location.invoke(
            {"lat": 46.8139, "lng": -71.208, "dist_km": 25}
        )
        create_sightings_map.invoke({})

        non_goose = [
            row["Species"]
            for row in VizBuffer["table"]
            if "goose" not in row["Species"].lower()
        ]
        assert not non_goose, (
            "LLM described goose sightings but map table contains non-goose "
            f"species: {non_goose}"
        )

    def test_no_species_fabricated_beyond_api_response(self, mock_client):
        """The map table must not contain species absent from the API response."""
        mock_client.recent_observations_by_location.return_value = QUEBEC_GOOSE_OBS

        get_recent_observations_by_location.invoke(
            {"lat": 46.8139, "lng": -71.208, "dist_km": 25}
        )
        create_sightings_map.invoke({})

        api_species = {o["comName"] for o in QUEBEC_GOOSE_OBS}
        table_species = _map_species(VizBuffer["table"])
        fabricated = table_species - api_species
        assert not fabricated, (
            f"Map table contains species not returned by the API: {fabricated}"
        )

    def test_mixed_response_non_goose_records_violate_goose_only_assertion(
        self, mock_client
    ):
        """Guard test: if the API returns non-goose records alongside geese, the
        goose-only check must fail — confirming the assertion is meaningful."""
        mixed_obs = QUEBEC_GOOSE_OBS + [
            {
                "comName": "American Robin",
                "sciName": "Turdus migratorius",
                "speciesCode": "amerob",
                "howMany": 1,
                "lat": 46.81,
                "lng": -71.21,
                "obsDt": _YESTERDAY.strftime("%Y-%m-%d 09:00"),
                "locName": "Quebec City Park",
                "locId": "L502",
            }
        ]
        mock_client.recent_observations_by_location.return_value = mixed_obs

        get_recent_observations_by_location.invoke(
            {"lat": 46.8139, "lng": -71.208, "dist_km": 25}
        )
        create_sightings_map.invoke({})

        non_goose = [
            row["Species"]
            for row in VizBuffer["table"]
            if "goose" not in row["Species"].lower()
        ]
        assert non_goose, (
            "Expected non-goose species in mixed data but found none — "
            "the goose-only assertion guard is broken."
        )


# ---------------------------------------------------------------------------
# 5. show_observations_table consistency
#    The dataframe rendered in VizBuffer must faithfully reflect the API data:
#    no rows dropped, no species fabricated, counts correct, columns renamed.
# ---------------------------------------------------------------------------


def _df_species(vizbuffer_data: list[dict]) -> set[str]:
    """Set of 'Species' values from VizBuffer['data'] (dataframe records)."""
    return {r["Species"] for r in vizbuffer_data}


def _df_record_count(return_value: str) -> int | None:
    """Parse the integer observation count from a show_observations_table return string."""
    m = re.search(r"(\d+)\s+observations?", return_value, re.IGNORECASE)
    return int(m.group(1)) if m else None


class TestDataframeConsistency:
    """show_observations_table output ↔ eBird API data must be identical."""

    # ── Species coverage ────────────────────────────────────────────────────

    def test_recent_location_to_table_species_match(self, mock_client):
        """Every species from the API must appear in the rendered dataframe."""
        mock_client.recent_observations_by_location.return_value = RECENT_OBS

        get_recent_observations_by_location.invoke({"lat": 40.78, "lng": -73.97})
        show_observations_table.invoke({})

        assert _df_species(VizBuffer["data"]) == {o["comName"] for o in RECENT_OBS}

    def test_recent_region_to_table_species_match(self, mock_client):
        mock_client.recent_observations_by_region.return_value = RECENT_OBS

        get_recent_observations_by_region.invoke({"region_code": "US-NY"})
        show_observations_table.invoke({})

        assert _df_species(VizBuffer["data"]) == {o["comName"] for o in RECENT_OBS}

    def test_historic_to_table_species_match(self, mock_client):
        mock_client.historic_observations.return_value = HISTORIC_OBS

        get_historic_observations.invoke(
            {"region_code": "US-MA", "year": 2024, "month": 5, "day": 1}
        )
        show_observations_table.invoke({})

        assert _df_species(VizBuffer["data"]) == {o["comName"] for o in HISTORIC_OBS}

    def test_notable_obs_to_table_species_match(self, mock_client):
        mock_client.notable_observations_by_location.return_value = RECENT_OBS

        get_notable_observations_by_location.invoke({"lat": 40.78, "lng": -73.97})
        show_observations_table.invoke({})

        assert _df_species(VizBuffer["data"]) == {o["comName"] for o in RECENT_OBS}

    # ── Row-count integrity ──────────────────────────────────────────────────

    def test_table_row_count_equals_api_record_count(self, mock_client):
        """Unlike the map (capped at 10), the table must show all API records."""
        mock_client.recent_observations_by_location.return_value = RECENT_OBS

        get_recent_observations_by_location.invoke({"lat": 40.78, "lng": -73.97})
        result = show_observations_table.invoke({})

        assert len(VizBuffer["data"]) == len(RECENT_OBS)
        reported = _df_record_count(result)
        assert reported == len(RECENT_OBS)

    def test_table_not_capped_at_ten_rows(self, mock_client):
        """Twelve records must all appear — map caps at 10 but table must not."""
        twelve_obs = [
            {**RECENT_OBS[i % 2], "comName": f"Species {i}", "locId": f"L{i}"}
            for i in range(12)
        ]
        mock_client.recent_observations_by_location.return_value = twelve_obs

        get_recent_observations_by_location.invoke({"lat": 40.78, "lng": -73.97})
        show_observations_table.invoke({})

        assert len(VizBuffer["data"]) == 12, (
            "Table must not be capped at 10; all 12 records should appear."
        )

    def test_no_extra_records_fabricated(self, mock_client):
        """Table must not contain records beyond what the API returned."""
        mock_client.historic_observations.return_value = HISTORIC_OBS

        get_historic_observations.invoke(
            {"region_code": "US-MA", "year": 2024, "month": 5, "day": 1}
        )
        show_observations_table.invoke({})

        api_species = {o["comName"] for o in HISTORIC_OBS}
        fabricated = _df_species(VizBuffer["data"]) - api_species
        assert not fabricated, (
            f"Dataframe contains species not returned by the API: {fabricated}"
        )

    # ── Column labelling ─────────────────────────────────────────────────────

    def test_table_has_friendly_column_names(self, mock_client):
        mock_client.recent_observations_by_location.return_value = RECENT_OBS

        get_recent_observations_by_location.invoke({"lat": 40.78, "lng": -73.97})
        show_observations_table.invoke({})

        row = VizBuffer["data"][0]
        for col in ("Species", "Count", "Location", "Date"):
            assert col in row, f"Expected friendly column '{col}' not found in table row"

    def test_table_has_no_raw_ebird_column_names(self, mock_client):
        mock_client.recent_observations_by_location.return_value = RECENT_OBS

        get_recent_observations_by_location.invoke({"lat": 40.78, "lng": -73.97})
        show_observations_table.invoke({})

        row = VizBuffer["data"][0]
        for raw in ("comName", "sciName", "howMany", "obsDt", "locName"):
            assert raw not in row, (
                f"Raw eBird column '{raw}' should have been renamed in the table"
            )

    # ── VizBuffer state ──────────────────────────────────────────────────────

    def test_vizbuffer_type_is_dataframe(self, mock_client):
        mock_client.recent_observations_by_location.return_value = RECENT_OBS

        get_recent_observations_by_location.invoke({"lat": 40.78, "lng": -73.97})
        show_observations_table.invoke({})

        assert VizBuffer["type"] == "dataframe"

    def test_vizbuffer_data_is_renderable_as_dataframe(self, mock_client):
        """VizBuffer['data'] must be a list of dicts that pandas can read."""
        mock_client.recent_observations_by_location.return_value = RECENT_OBS

        get_recent_observations_by_location.invoke({"lat": 40.78, "lng": -73.97})
        show_observations_table.invoke({})

        df = pd.DataFrame(VizBuffer["data"])
        assert not df.empty
        assert len(df) == len(RECENT_OBS)

    def test_vizbuffer_table_key_is_none(self, mock_client):
        """VizBuffer['table'] is only used by the map tool; must be None here."""
        mock_client.recent_observations_by_location.return_value = RECENT_OBS

        get_recent_observations_by_location.invoke({"lat": 40.78, "lng": -73.97})
        show_observations_table.invoke({})

        assert VizBuffer["table"] is None

    def test_vizbuffer_title_contains_record_count(self, mock_client):
        mock_client.recent_observations_by_location.return_value = RECENT_OBS

        get_recent_observations_by_location.invoke({"lat": 40.78, "lng": -73.97})
        show_observations_table.invoke({})

        assert str(len(RECENT_OBS)) in VizBuffer["title"]

    # ── Date consistency ─────────────────────────────────────────────────────

    def test_table_dates_match_historic_request(self, mock_client):
        """Every date in the table must equal the requested historic date."""
        mock_client.historic_observations.return_value = HISTORIC_OBS

        get_historic_observations.invoke(
            {"region_code": "US-MA", "year": 2024, "month": 5, "day": 1}
        )
        show_observations_table.invoke({})

        for row in VizBuffer["data"]:
            raw_date = row.get("Date", "")
            assert raw_date.startswith("2024-05-01"), (
                f"Expected date 2024-05-01 but found '{raw_date}' in dataframe row"
            )

    def test_table_dates_within_recent_window(self, mock_client):
        """Dates in the table from a recent-obs call must fall within days_back."""
        days_back = 7
        mock_client.recent_observations_by_location.return_value = RECENT_OBS

        get_recent_observations_by_location.invoke(
            {"lat": 40.78, "lng": -73.97, "days_back": days_back}
        )
        show_observations_table.invoke({})

        cutoff = _TODAY - datetime.timedelta(days=days_back)
        for row in VizBuffer["data"]:
            d = datetime.date.fromisoformat(row["Date"][:10])
            assert d >= cutoff, (
                f"Dataframe contains date {d} which is outside the {days_back}-day window"
            )

    # ── Map ↔ table consistency (same underlying data) ────────────────────────

    def test_table_and_map_show_same_species(self, mock_client):
        """show_observations_table and create_sightings_map must reflect the
        same set of species when called after the same eBird tool."""
        mock_client.recent_observations_by_location.return_value = RECENT_OBS

        get_recent_observations_by_location.invoke({"lat": 40.78, "lng": -73.97})

        show_observations_table.invoke({})
        table_species = _df_species(VizBuffer["data"])

        create_sightings_map.invoke({})
        map_species = _map_species(VizBuffer["table"])

        assert table_species == map_species, (
            f"Table species {table_species} differ from map species {map_species}"
        )

    def test_table_row_count_gte_map_table_row_count(self, mock_client):
        """The dataframe (uncapped) must have at least as many rows as the map
        table (which is capped at 10)."""
        many_obs = [
            {**RECENT_OBS[i % 2], "comName": f"Species {i}", "locId": f"L{i}"}
            for i in range(12)
        ]
        mock_client.recent_observations_by_location.return_value = many_obs

        get_recent_observations_by_location.invoke({"lat": 40.78, "lng": -73.97})

        show_observations_table.invoke({})
        table_count = len(VizBuffer["data"])

        create_sightings_map.invoke({})
        map_table_count = len(VizBuffer["table"])

        assert table_count >= map_table_count, (
            f"Dataframe ({table_count} rows) should have at least as many rows "
            f"as the map table ({map_table_count} rows)"
        )


# ---------------------------------------------------------------------------
# Helper shared by sections 6 and 7
# ---------------------------------------------------------------------------


def _table_date_set(vizbuffer_data: list[dict]) -> set[datetime.date]:
    """Set of dates from VizBuffer['data'] (show_observations_table output)."""
    dates: set[datetime.date] = set()
    for row in vizbuffer_data:
        raw = row.get("Date", "")
        if raw:
            dates.add(datetime.date.fromisoformat(raw[:10]))
    return dates


# ---------------------------------------------------------------------------
# 6. Recent observations by-location with species_code filter
#    When filtering by species code, both the table and the map must honour
#    the days_back recency window — and both surfaces must agree on dates.
#
#    Root cause of the reported bug:
#      get_recent_observations_by_location(species_code='y00678') returned
#      records with dates from April 2024 (well outside any reasonable
#      days_back window), yet the map showed "correct" data because it
#      happened to read the same cached response.  The table echoed the
#      stale dates verbatim, giving the impression only the table was wrong.
#
#    These tests confirm that a date-recency guard catches this failure on
#    both surfaces, and that passing genuinely fresh records clears the guard.
# ---------------------------------------------------------------------------


class TestRecentObsWithSpeciesFilterConsistency:
    """Table and map both surface stale dates when a species-filtered
    recent-obs call returns out-of-window records."""

    def test_stale_species_filtered_obs_fail_date_check_in_table(self, mock_client):
        """Stale dates from a species-filtered recent-obs call must be detectable
        in the table — confirming the recency guard covers the species-code path."""
        days_back = 7
        mock_client.recent_observations_by_location.return_value = SPECIES_FILTERED_STALE_OBS

        get_recent_observations_by_location.invoke(
            {"lat": 46.81, "lng": -71.21, "days_back": days_back, "species_code": "y00678"}
        )
        show_observations_table.invoke({})

        cutoff = _TODAY - datetime.timedelta(days=days_back)
        stale_rows = [
            row for row in VizBuffer["data"]
            if datetime.date.fromisoformat(row["Date"][:10]) < cutoff
        ]
        assert stale_rows, (
            "Expected stale dates (April 2024) to appear in the table for "
            "a species-filtered recent-obs call — the recency guard is not "
            "catching this path."
        )

    def test_stale_species_filtered_obs_fail_date_check_in_map(self, mock_client):
        """Stale dates from a species-filtered recent-obs call must also be
        detectable in the map, confirming both surfaces are equally vulnerable."""
        days_back = 7
        mock_client.recent_observations_by_location.return_value = SPECIES_FILTERED_STALE_OBS

        get_recent_observations_by_location.invoke(
            {"lat": 46.81, "lng": -71.21, "days_back": days_back, "species_code": "y00678"}
        )
        create_sightings_map.invoke({})

        assert not _vizbuffer_dates_all_within(days_back), (
            "Expected stale dates to fail the map recency check for a "
            "species-filtered recent-obs call, but _vizbuffer_dates_all_within "
            "returned True unexpectedly."
        )

    def test_fresh_species_filtered_obs_pass_date_check_in_table(self, mock_client):
        """Genuinely recent records filtered by species_code must pass the
        table recency check — ensuring the guard does not produce false positives."""
        days_back = 7
        recent_species_obs = [
            {**SPECIES_FILTERED_STALE_OBS[0], "obsDt": _YESTERDAY.strftime("%Y-%m-%d 08:00")},
            {**SPECIES_FILTERED_STALE_OBS[1], "obsDt": _THREE_DAYS_AGO.strftime("%Y-%m-%d 09:30")},
        ]
        mock_client.recent_observations_by_location.return_value = recent_species_obs

        get_recent_observations_by_location.invoke(
            {"lat": 46.81, "lng": -71.21, "days_back": days_back, "species_code": "y00678"}
        )
        show_observations_table.invoke({})

        cutoff = _TODAY - datetime.timedelta(days=days_back)
        stale_rows = [
            row for row in VizBuffer["data"]
            if datetime.date.fromisoformat(row["Date"][:10]) < cutoff
        ]
        assert not stale_rows, (
            "Fresh species-filtered observations should all pass the recency check "
            f"but found out-of-window rows: {stale_rows}"
        )

    def test_fresh_species_filtered_obs_pass_date_check_in_map(self, mock_client):
        """Genuinely recent records filtered by species_code must pass the
        map recency check."""
        days_back = 7
        recent_species_obs = [
            {**SPECIES_FILTERED_STALE_OBS[0], "obsDt": _YESTERDAY.strftime("%Y-%m-%d 08:00")},
            {**SPECIES_FILTERED_STALE_OBS[1], "obsDt": _THREE_DAYS_AGO.strftime("%Y-%m-%d 09:30")},
        ]
        mock_client.recent_observations_by_location.return_value = recent_species_obs

        get_recent_observations_by_location.invoke(
            {"lat": 46.81, "lng": -71.21, "days_back": days_back, "species_code": "y00678"}
        )
        create_sightings_map.invoke({})

        assert _vizbuffer_dates_all_within(days_back), (
            f"Map dates for fresh species-filtered observations should all be "
            f"within the {days_back}-day window."
        )


# ---------------------------------------------------------------------------
# 7. Table ↔ map date parity
#    The set of dates rendered by show_observations_table must equal the set
#    rendered by create_sightings_map when both are called after the same
#    eBird tool invocation.
#
#    The critical failure mode: the LLM injects stale JSON directly into
#    show_observations_table (bypassing the session cache) while
#    create_sightings_map loads from the cache and shows fresh dates.  This
#    produces a visible mismatch between the table and the map — the exact
#    symptom described in the bug report.
# ---------------------------------------------------------------------------


class TestTableMapDateParity:
    """show_observations_table and create_sightings_map must show the same dates."""

    def test_table_and_map_date_sets_are_identical(self, mock_client):
        """Dates in the table must exactly equal dates in the map table."""
        mock_client.recent_observations_by_location.return_value = RECENT_OBS

        get_recent_observations_by_location.invoke({"lat": 40.78, "lng": -73.97})

        show_observations_table.invoke({})
        table_dates = _table_date_set(VizBuffer["data"])

        create_sightings_map.invoke({})
        map_dates = {d for d in _map_dates(VizBuffer["table"])}

        assert table_dates == map_dates, (
            f"Table dates {table_dates} differ from map dates {map_dates}. "
            "Both surfaces must reflect the same underlying observations."
        )

    def test_stale_json_injection_causes_table_map_date_mismatch(self, mock_client):
        """Guard test: when the LLM injects stale JSON into show_observations_table,
        the table dates diverge from the map dates — proving the mismatch is
        detectable.  This is the exact failure mode in the bug report."""
        days_back = 7
        mock_client.recent_observations_by_location.return_value = RECENT_OBS

        # Populate the session cache with fresh data.
        get_recent_observations_by_location.invoke(
            {"lat": 40.78, "lng": -73.97, "days_back": days_back}
        )

        # LLM explicitly injects stale JSON (April 2024 dates) into the table.
        stale_json = json.dumps(SPECIES_FILTERED_STALE_OBS)
        show_observations_table.invoke({"observations_json": stale_json})
        table_dates = _table_date_set(VizBuffer["data"])

        # Map loads from the session cache → fresh dates.
        create_sightings_map.invoke({})
        map_dates = {d for d in _map_dates(VizBuffer["table"])}

        assert table_dates != map_dates, (
            "Expected table dates (stale April 2024) to differ from map dates "
            "(fresh session-cache data), but they matched — the mismatch guard "
            "is broken."
        )

    def test_stale_json_injection_fails_recency_check_in_table(self, mock_client):
        """When the LLM bypasses the cache and passes stale JSON to
        show_observations_table, the dates must fail the recency check."""
        days_back = 7
        mock_client.recent_observations_by_location.return_value = RECENT_OBS

        get_recent_observations_by_location.invoke(
            {"lat": 40.78, "lng": -73.97, "days_back": days_back}
        )

        stale_json = json.dumps(SPECIES_FILTERED_STALE_OBS)
        show_observations_table.invoke({"observations_json": stale_json})

        cutoff = _TODAY - datetime.timedelta(days=days_back)
        stale_rows = [
            row for row in VizBuffer["data"]
            if datetime.date.fromisoformat(row["Date"][:10]) < cutoff
        ]
        assert stale_rows, (
            "Expected stale rows after LLM injected old JSON into "
            "show_observations_table, but none were detected — the recency "
            "guard is not catching this injection path."
        )

    def test_table_and_map_dates_agree_with_species_filter(self, mock_client):
        """After a species-code-filtered recent-obs call, table and map dates
        must match — ruling out cache-vs-injection divergence for this path."""
        recent_species_obs = [
            {**SPECIES_FILTERED_STALE_OBS[0], "obsDt": _YESTERDAY.strftime("%Y-%m-%d 08:00")},
            {**SPECIES_FILTERED_STALE_OBS[1], "obsDt": _THREE_DAYS_AGO.strftime("%Y-%m-%d 09:30")},
        ]
        mock_client.recent_observations_by_location.return_value = recent_species_obs

        get_recent_observations_by_location.invoke(
            {"lat": 46.81, "lng": -71.21, "days_back": 7, "species_code": "y00678"}
        )

        show_observations_table.invoke({})
        table_dates = _table_date_set(VizBuffer["data"])

        create_sightings_map.invoke({})
        map_dates = {d for d in _map_dates(VizBuffer["table"])}

        assert table_dates == map_dates, (
            f"After a species-filtered call, table dates {table_dates} differ "
            f"from map dates {map_dates}. Both tools read from the same cache."
        )

    def test_table_location_set_matches_map_location_set(self, mock_client):
        """Locations in the table must equal locations in the map for the same
        observations (using a dataset small enough to be uncapped by the map)."""
        mock_client.recent_observations_by_location.return_value = RECENT_OBS

        get_recent_observations_by_location.invoke({"lat": 40.78, "lng": -73.97})

        show_observations_table.invoke({})
        table_locs = {row.get("Location") for row in VizBuffer["data"]}

        create_sightings_map.invoke({})
        map_locs = {row.get("Location") for row in VizBuffer["table"]}

        assert table_locs == map_locs, (
            f"Table locations {table_locs} differ from map locations {map_locs}."
        )
