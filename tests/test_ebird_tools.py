"""Unit tests for src/tools/ebird_tools.py."""

import json
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.tools import ToolException

import src.tools.ebird_tools as ebird_tools_module
import src.utils.region_cache as region_cache_module
from src.tools.ebird_tools import (
    get_historic_observations,
    get_nearby_hotspots,
    get_notable_observations_by_location,
    get_recent_observations_by_location,
    get_recent_observations_by_region,
    get_region_list,
)
from src.utils.ebird_client import EBirdError

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
    }
]

SAMPLE_HOTSPOTS = [
    {"locId": "L123456", "locName": "Riverside Park", "lat": 40.8, "lng": -73.97}
]

SAMPLE_REGIONS = [{"code": "US-NY", "name": "New York"}]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_ebird_client():
    """Reset the module-level singleton and region cache before and after each test."""
    ebird_tools_module._client = None
    # Clear the in-memory region cache so Tier-2 checks are skipped (cold cache).
    original_codes = region_cache_module._known_codes.copy()
    original_loaded = region_cache_module._cache_loaded
    region_cache_module._known_codes.clear()
    region_cache_module._cache_loaded = True  # mark loaded so disk isn't re-read
    yield
    ebird_tools_module._client = None
    region_cache_module._known_codes.clear()
    region_cache_module._known_codes.update(original_codes)
    region_cache_module._cache_loaded = original_loaded


@pytest.fixture
def mock_client():
    """Patch _get_client to return a controllable MagicMock."""
    with patch("src.tools.ebird_tools._get_client") as mock_get:
        client = MagicMock()
        mock_get.return_value = client
        yield client


# ---------------------------------------------------------------------------
# get_recent_observations_by_location
# ---------------------------------------------------------------------------


class TestGetRecentObservationsByLocation:
    def test_returns_json_string(self, mock_client):
        mock_client.recent_observations_by_location.return_value = SAMPLE_OBS
        result = get_recent_observations_by_location.invoke({"lat": 48.85, "lng": 2.35})
        assert json.loads(result) == SAMPLE_OBS

    def test_passes_all_params_to_client(self, mock_client):
        mock_client.recent_observations_by_location.return_value = SAMPLE_OBS
        get_recent_observations_by_location.invoke(
            {
                "lat": 48.85,
                "lng": 2.35,
                "dist_km": 10,
                "days_back": 14,
                "species_code": "norcar",
            }
        )
        mock_client.recent_observations_by_location.assert_called_once_with(
            lat=48.85, lng=2.35, dist=10, back=14, species_code="norcar"
        )

    def test_raises_tool_exception_on_ebird_error(self, mock_client):
        mock_client.recent_observations_by_location.side_effect = EBirdError("API error")
        with pytest.raises(ToolException, match="API error"):
            get_recent_observations_by_location.invoke({"lat": 48.85, "lng": 2.35})

    def test_raises_tool_exception_when_no_results(self, mock_client):
        mock_client.recent_observations_by_location.return_value = []
        with pytest.raises(ToolException, match="no results"):
            get_recent_observations_by_location.invoke({"lat": 0.0, "lng": 0.0})


# ---------------------------------------------------------------------------
# get_recent_observations_by_region
# ---------------------------------------------------------------------------


class TestGetRecentObservationsByRegion:
    def test_returns_json_string(self, mock_client):
        mock_client.recent_observations_by_region.return_value = SAMPLE_OBS
        result = get_recent_observations_by_region.invoke({"region_code": "US-NY"})
        assert json.loads(result) == SAMPLE_OBS

    def test_strips_whitespace_from_region_code(self, mock_client):
        mock_client.recent_observations_by_region.return_value = SAMPLE_OBS
        get_recent_observations_by_region.invoke({"region_code": "  US-NY  "})
        mock_client.recent_observations_by_region.assert_called_once_with(
            region_code="US-NY", back=7, species_code=None
        )

    def test_passes_optional_params(self, mock_client):
        mock_client.recent_observations_by_region.return_value = SAMPLE_OBS
        get_recent_observations_by_region.invoke(
            {"region_code": "FR", "days_back": 3, "species_code": "gyrfal"}
        )
        mock_client.recent_observations_by_region.assert_called_once_with(
            region_code="FR", back=3, species_code="gyrfal"
        )

    def test_raises_tool_exception_on_ebird_error(self, mock_client):
        mock_client.recent_observations_by_region.side_effect = EBirdError("not found")
        with pytest.raises(ToolException):
            get_recent_observations_by_region.invoke({"region_code": "US-NY"})


# ---------------------------------------------------------------------------
# get_historic_observations
# ---------------------------------------------------------------------------


class TestGetHistoricObservations:
    def test_returns_json_string(self, mock_client):
        mock_client.historic_observations.return_value = SAMPLE_OBS
        result = get_historic_observations.invoke(
            {"region_code": "CA-ON", "year": 2024, "month": 5, "day": 1}
        )
        assert json.loads(result) == SAMPLE_OBS

    def test_strips_whitespace_from_region_code(self, mock_client):
        mock_client.historic_observations.return_value = SAMPLE_OBS
        get_historic_observations.invoke(
            {"region_code": " CA-ON ", "year": 2024, "month": 5, "day": 1}
        )
        mock_client.historic_observations.assert_called_once_with(
            region_code="CA-ON", year=2024, month=5, day=1
        )

    def test_raises_tool_exception_on_ebird_error(self, mock_client):
        mock_client.historic_observations.side_effect = EBirdError("error")
        with pytest.raises(ToolException):
            get_historic_observations.invoke(
                {"region_code": "CA-ON", "year": 2024, "month": 5, "day": 1}
            )


# ---------------------------------------------------------------------------
# get_nearby_hotspots
# ---------------------------------------------------------------------------


class TestGetNearbyHotspots:
    def test_returns_json_string(self, mock_client):
        mock_client.nearby_hotspots.return_value = SAMPLE_HOTSPOTS
        result = get_nearby_hotspots.invoke({"lat": 40.71, "lng": -74.01})
        assert json.loads(result) == SAMPLE_HOTSPOTS

    def test_passes_dist_km_to_client(self, mock_client):
        mock_client.nearby_hotspots.return_value = SAMPLE_HOTSPOTS
        get_nearby_hotspots.invoke({"lat": 40.71, "lng": -74.01, "dist_km": 5})
        mock_client.nearby_hotspots.assert_called_once_with(lat=40.71, lng=-74.01, dist=5)

    def test_raises_tool_exception_on_ebird_error(self, mock_client):
        mock_client.nearby_hotspots.side_effect = EBirdError("error")
        with pytest.raises(ToolException):
            get_nearby_hotspots.invoke({"lat": 40.71, "lng": -74.01})


# ---------------------------------------------------------------------------
# get_region_list
# ---------------------------------------------------------------------------


class TestGetRegionList:
    def test_returns_json_string(self, mock_client):
        mock_client.region_list.return_value = SAMPLE_REGIONS
        result = get_region_list.invoke(
            {"region_type": "subnational1", "parent_region_code": "US"}
        )
        assert json.loads(result) == SAMPLE_REGIONS

    def test_strips_whitespace_from_parent_code(self, mock_client):
        mock_client.region_list.return_value = []
        get_region_list.invoke(
            {"region_type": "subnational1", "parent_region_code": " US "}
        )
        mock_client.region_list.assert_called_once_with(
            region_type="subnational1", parent_region_code="US"
        )

    @pytest.mark.parametrize(
        "region_type", ["country", "subnational1", "subnational2"]
    )
    def test_valid_region_types_accepted(self, mock_client, region_type):
        mock_client.region_list.return_value = []
        # Should not raise
        get_region_list.invoke(
            {"region_type": region_type, "parent_region_code": "world"}
        )

    def test_invalid_region_type_raises_tool_exception(self, mock_client):
        with pytest.raises(ToolException, match="region_type"):
            get_region_list.invoke(
                {"region_type": "province", "parent_region_code": "US"}
            )

    def test_raises_tool_exception_on_ebird_error(self, mock_client):
        mock_client.region_list.side_effect = EBirdError("error")
        with pytest.raises(ToolException):
            get_region_list.invoke(
                {"region_type": "country", "parent_region_code": "world"}
            )


# ---------------------------------------------------------------------------
# get_notable_observations_by_location
# ---------------------------------------------------------------------------


class TestGetNotableObservationsByLocation:
    def test_returns_json_string(self, mock_client):
        mock_client.notable_observations_by_location.return_value = SAMPLE_OBS
        result = get_notable_observations_by_location.invoke(
            {"lat": 51.5, "lng": -0.12}
        )
        assert json.loads(result) == SAMPLE_OBS

    def test_passes_optional_params(self, mock_client):
        mock_client.notable_observations_by_location.return_value = SAMPLE_OBS
        get_notable_observations_by_location.invoke(
            {"lat": 51.5, "lng": -0.12, "dist_km": 10, "days_back": 14}
        )
        mock_client.notable_observations_by_location.assert_called_once_with(
            lat=51.5, lng=-0.12, dist=10, back=14
        )

    def test_raises_tool_exception_on_ebird_error(self, mock_client):
        mock_client.notable_observations_by_location.side_effect = EBirdError("error")
        with pytest.raises(ToolException):
            get_notable_observations_by_location.invoke({"lat": 51.5, "lng": -0.12})


# ---------------------------------------------------------------------------
# _autocorrect_subregion
# ---------------------------------------------------------------------------

from src.tools.ebird_tools import _autocorrect_subregion

SAMPLE_SUBREGIONS = [
    {"code": "CA-QC-ABI", "name": "Abitibi-Ouest"},
    {"code": "CA-QC-CAP", "name": "Capitale-Nationale"},
    {"code": "CA-QC-LAU", "name": "Laurentides"},
]


class TestAutocorrectSubregion:
    def test_returns_best_match_by_code_suffix(self, mock_client):
        """CA-QC-CAP has suffix CAP; querying CA-QC-CAP should self-match."""
        mock_client.region_list.return_value = SAMPLE_SUBREGIONS
        result = _autocorrect_subregion("CA-QC-CAP")
        assert result is not None
        assert result[0] == "CA-QC-CAP"

    def test_returns_closest_match_for_bad_suffix(self, mock_client):
        """CA-QC-LAL is close to CA-QC-LAU (Laurentides)."""
        mock_client.region_list.return_value = SAMPLE_SUBREGIONS
        result = _autocorrect_subregion("CA-QC-LAL")
        assert result is not None
        corrected_code, corrected_name = result
        assert corrected_code == "CA-QC-LAU"
        assert corrected_name == "Laurentides"

    def test_uses_subnational2_for_three_part_code(self, mock_client):
        mock_client.region_list.return_value = SAMPLE_SUBREGIONS
        _autocorrect_subregion("CA-QC-XXX")
        mock_client.region_list.assert_called_once_with(
            parent_region_code="CA-QC", region_type="subnational2"
        )

    def test_uses_subnational1_for_two_part_code(self, mock_client):
        mock_client.region_list.return_value = [{"code": "CA-QC", "name": "Québec"}]
        _autocorrect_subregion("CA-QX")
        mock_client.region_list.assert_called_once_with(
            parent_region_code="CA", region_type="subnational1"
        )

    def test_returns_none_when_region_list_raises(self, mock_client):
        mock_client.region_list.side_effect = EBirdError("not found")
        result = _autocorrect_subregion("CA-QC-XXX")
        assert result is None

    def test_returns_none_for_single_part_code(self, mock_client):
        result = _autocorrect_subregion("ZZ")
        assert result is None
        mock_client.region_list.assert_not_called()

    def test_returns_none_when_region_list_empty(self, mock_client):
        mock_client.region_list.return_value = []
        result = _autocorrect_subregion("CA-QC-XXX")
        assert result is None


# ---------------------------------------------------------------------------
# Auto-correction integration in get_recent_observations_by_region
# ---------------------------------------------------------------------------


class TestAutocorrectInRecentObsByRegion:
    def test_autocorrects_bad_subregion_code(self, mock_client):
        """When cache has CA-QC-LAU but not CA-QC-LAL, tool auto-corrects."""
        region_cache_module._known_codes.add("CA-QC-LAU")
        mock_client.region_list.return_value = [
            {"code": "CA-QC-LAU", "name": "Laurentides"}
        ]
        mock_client.recent_observations_by_region.return_value = SAMPLE_OBS

        result_json = get_recent_observations_by_region.invoke({"region_code": "CA-QC-LAL"})
        result = json.loads(result_json)

        # Should be wrapped with a correction note
        assert isinstance(result, dict)
        assert "CA-QC-LAL" in result["_note"]
        assert "CA-QC-LAU" in result["_note"]
        assert result["observations"] == SAMPLE_OBS

        # Client called with the corrected code
        mock_client.recent_observations_by_region.assert_called_once_with(
            region_code="CA-QC-LAU", back=7, species_code=None
        )

    def test_no_wrapping_when_code_is_valid(self, mock_client):
        """Valid code → plain JSON array, no envelope."""
        mock_client.recent_observations_by_region.return_value = SAMPLE_OBS
        result = json.loads(
            get_recent_observations_by_region.invoke({"region_code": "US-NY"})
        )
        assert isinstance(result, list)

    def test_raises_when_autocorrect_also_fails(self, mock_client):
        """If the region list lookup fails too, raise ToolException."""
        region_cache_module._known_codes.add("CA-QC-LAU")
        mock_client.region_list.side_effect = EBirdError("lookup failed")

        with pytest.raises(ToolException):
            get_recent_observations_by_region.invoke({"region_code": "CA-QC-LAL"})


# ---------------------------------------------------------------------------
# Auto-correction integration in get_historic_observations
# ---------------------------------------------------------------------------


class TestAutocorrectInHistoricObs:
    def test_autocorrects_bad_subregion_code(self, mock_client):
        region_cache_module._known_codes.add("CA-QC-LAU")
        mock_client.region_list.return_value = [
            {"code": "CA-QC-LAU", "name": "Laurentides"}
        ]
        mock_client.historic_observations.return_value = SAMPLE_OBS

        result_json = get_historic_observations.invoke(
            {"region_code": "CA-QC-LAL", "year": 2024, "month": 5, "day": 1}
        )
        result = json.loads(result_json)

        assert isinstance(result, dict)
        assert "CA-QC-LAL" in result["_note"]
        assert "CA-QC-LAU" in result["_note"]
        assert result["observations"] == SAMPLE_OBS

        mock_client.historic_observations.assert_called_once_with(
            region_code="CA-QC-LAU", year=2024, month=5, day=1
        )
