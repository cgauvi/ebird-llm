"""Unit tests for src/tools/ebird_tools.py."""

import json
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.tools import ToolException

import src.tools.ebird_tools as ebird_tools_module
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
    """Reset the module-level singleton before and after each test."""
    ebird_tools_module._client = None
    yield
    ebird_tools_module._client = None


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
        mock_client.recent_observations_by_location.return_value = []
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

    def test_returns_empty_list_json_when_no_results(self, mock_client):
        mock_client.recent_observations_by_location.return_value = []
        result = get_recent_observations_by_location.invoke({"lat": 0.0, "lng": 0.0})
        assert json.loads(result) == []


# ---------------------------------------------------------------------------
# get_recent_observations_by_region
# ---------------------------------------------------------------------------


class TestGetRecentObservationsByRegion:
    def test_returns_json_string(self, mock_client):
        mock_client.recent_observations_by_region.return_value = SAMPLE_OBS
        result = get_recent_observations_by_region.invoke({"region_code": "US-NY"})
        assert json.loads(result) == SAMPLE_OBS

    def test_strips_whitespace_from_region_code(self, mock_client):
        mock_client.recent_observations_by_region.return_value = []
        get_recent_observations_by_region.invoke({"region_code": "  US-NY  "})
        mock_client.recent_observations_by_region.assert_called_once_with(
            region_code="US-NY", back=7, species_code=None
        )

    def test_passes_optional_params(self, mock_client):
        mock_client.recent_observations_by_region.return_value = []
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
        mock_client.historic_observations.return_value = []
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
        mock_client.nearby_hotspots.return_value = []
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
        mock_client.notable_observations_by_location.return_value = []
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
