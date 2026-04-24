"""Unit tests for src/utils/ebird_client.py."""

from unittest.mock import MagicMock, patch

import pytest

from src.utils.ebird_client import EBirdClient, EBirdError


class TestEBirdClientInit:
    def test_raises_when_api_key_missing(self, monkeypatch):
        monkeypatch.delenv("EBIRD_API_KEY", raising=False)
        with pytest.raises(EnvironmentError, match="EBIRD_API_KEY"):
            EBirdClient()

    def test_raises_when_api_key_empty(self, monkeypatch):
        monkeypatch.setenv("EBIRD_API_KEY", "")
        with pytest.raises(EnvironmentError, match="EBIRD_API_KEY"):
            EBirdClient()

    def test_sets_auth_header(self, monkeypatch):
        monkeypatch.setenv("EBIRD_API_KEY", "abc123")
        client = EBirdClient()
        assert client._session.headers["x-ebirdapitoken"] == "abc123"


class TestEBirdClientGet:
    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setenv("EBIRD_API_KEY", "testkey")
        return EBirdClient()

    def _ok(self, json_data):
        mock = MagicMock()
        mock.ok = True
        mock.json.return_value = json_data
        mock.text = "non-empty"  # non-empty so the empty-body guard is not triggered
        return mock

    def _empty(self):
        """Simulate a 200 OK with an empty body (eBird's way of saying 'no results')."""
        mock = MagicMock()
        mock.ok = True
        mock.text = ""
        return mock

    def _err(self, status_code=404):
        mock = MagicMock()
        mock.ok = False
        mock.status_code = status_code
        mock.text = "Not Found"
        return mock

    # ------------------------------------------------------------------
    # Low-level _get
    # ------------------------------------------------------------------

    def test_raises_ebird_error_on_non_ok_response(self, client):
        with patch.object(client._session, "get", return_value=self._err(404)):
            with pytest.raises(EBirdError, match="404"):
                client._get("/data/obs/geo/recent")

    def test_raises_ebird_error_on_500(self, client):
        with patch.object(client._session, "get", return_value=self._err(500)):
            with pytest.raises(EBirdError, match="500"):
                client._get("/data/obs/geo/recent")

    def test_empty_body_returns_empty_list(self, client):
        """eBird returns HTTP 200 with an empty body when there are no results."""
        with patch.object(client._session, "get", return_value=self._empty()):
            result = client._get("/data/obs/geo/recent")
        assert result == []

    def test_whitespace_only_body_returns_empty_list(self, client):
        mock = MagicMock()
        mock.ok = True
        mock.text = "   "
        with patch.object(client._session, "get", return_value=mock):
            result = client._get("/data/obs/geo/recent")
        assert result == []

    # ------------------------------------------------------------------
    # recent_observations_by_location
    # ------------------------------------------------------------------

    def test_recent_obs_by_location_no_species(self, client):
        records = [{"comName": "Robin", "lat": 48.85, "lng": 2.35}]
        with patch.object(client._session, "get", return_value=self._ok(records)) as m:
            result = client.recent_observations_by_location(48.85, 2.35, dist=10, back=3)
        assert result == records
        url = m.call_args[0][0]
        assert url.endswith("/data/obs/geo/recent")

    def test_recent_obs_by_location_with_species(self, client):
        records = [{"comName": "Northern Cardinal"}]
        with patch.object(client._session, "get", return_value=self._ok(records)) as m:
            result = client.recent_observations_by_location(40.71, -74.01, species_code="norcar")
        assert result == records
        url = m.call_args[0][0]
        assert "norcar" in url

    def test_recent_obs_by_location_passes_lat_lng_params(self, client):
        with patch.object(client._session, "get", return_value=self._ok([])) as m:
            client.recent_observations_by_location(51.5, -0.12, dist=5, back=2)
        params = m.call_args[1]["params"]
        assert params["lat"] == 51.5
        assert params["lng"] == -0.12
        assert params["dist"] == 5
        assert params["back"] == 2

    # ------------------------------------------------------------------
    # recent_observations_by_region
    # ------------------------------------------------------------------

    def test_recent_obs_by_region_no_species(self, client):
        records = [{"comName": "Starling"}]
        with patch.object(client._session, "get", return_value=self._ok(records)) as m:
            result = client.recent_observations_by_region("US-NY", back=5)
        assert result == records
        url = m.call_args[0][0]
        assert url.endswith("/data/obs/US-NY/recent")

    def test_recent_obs_by_region_with_species(self, client):
        with patch.object(client._session, "get", return_value=self._ok([])) as m:
            client.recent_observations_by_region("US-NY", species_code="bkcchi")
        url = m.call_args[0][0]
        assert "bkcchi" in url

    # ------------------------------------------------------------------
    # historic_observations
    # ------------------------------------------------------------------

    def test_historic_observations_url(self, client):
        records = [{"comName": "Yellow Warbler"}]
        with patch.object(client._session, "get", return_value=self._ok(records)) as m:
            result = client.historic_observations("CA-ON", year=2024, month=5, day=1)
        assert result == records
        url = m.call_args[0][0]
        assert "CA-ON/historic/2024/05/01" in url

    def test_historic_observations_zero_pads_month_and_day(self, client):
        with patch.object(client._session, "get", return_value=self._ok([])) as m:
            client.historic_observations("US-NY", year=2023, month=3, day=7)
        url = m.call_args[0][0]
        assert "2023/03/07" in url

    # ------------------------------------------------------------------
    # notable_observations_by_location
    # ------------------------------------------------------------------

    def test_notable_observations_by_location(self, client):
        records = [{"comName": "Rare Bird"}]
        with patch.object(client._session, "get", return_value=self._ok(records)) as m:
            result = client.notable_observations_by_location(51.5, -0.12, dist=10, back=14)
        assert result == records
        url = m.call_args[0][0]
        assert "notable" in url
        params = m.call_args[1]["params"]
        assert params["dist"] == 10
        assert params["back"] == 14

    # ------------------------------------------------------------------
    # nearby_hotspots
    # ------------------------------------------------------------------

    def test_nearby_hotspots(self, client):
        hotspots = [{"locId": "L123456", "locName": "Central Park"}]
        with patch.object(client._session, "get", return_value=self._ok(hotspots)) as m:
            result = client.nearby_hotspots(40.78, -73.97, dist=5)
        assert result == hotspots
        url = m.call_args[0][0]
        assert "hotspot/geo" in url
        assert m.call_args[1]["params"]["dist"] == 5

    # ------------------------------------------------------------------
    # region_list
    # ------------------------------------------------------------------

    def test_region_list(self, client):
        regions = [{"code": "US-NY", "name": "New York"}]
        with patch.object(client._session, "get", return_value=self._ok(regions)) as m:
            result = client.region_list("US", "subnational1")
        assert result == regions
        url = m.call_args[0][0]
        assert "subnational1/US" in url


# ---------------------------------------------------------------------------
# Curl logging
# ---------------------------------------------------------------------------


class TestCurlLogging:
    """_get() must emit a redacted curl command via logger.info for every live request."""

    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setenv("EBIRD_API_KEY", "testkey")
        return EBirdClient()

    def _resp(self, json_data, url: str):
        """Fake response whose .request.url mimics a real PreparedRequest."""
        mock = MagicMock()
        mock.ok = True
        mock.json.return_value = json_data
        mock.text = "non-empty"
        mock.request.url = url
        return mock

    def _assert_curl(self, mock_logger, expected_url: str):
        """Assert a single INFO call containing a redacted curl for *expected_url*."""
        mock_logger.info.assert_called_once()
        fmt, url_arg = mock_logger.info.call_args[0]
        full_msg = fmt % url_arg
        assert "curl" in full_msg
        assert "x-ebirdapitoken" in full_msg
        assert "<REDACTED>" in full_msg
        assert expected_url in full_msg

    # ------------------------------------------------------------------

    def test_recent_obs_by_location(self, client):
        url = "https://api.ebird.org/v2/data/obs/geo/recent?lat=48.85&lng=2.35&dist=10&back=3"
        with patch.object(client._session, "get", return_value=self._resp([{}], url)), \
             patch("src.utils.ebird_client.logger") as mock_logger:
            client.recent_observations_by_location(48.85, 2.35, dist=10, back=3)
        self._assert_curl(mock_logger, url)

    def test_recent_obs_by_location_with_species(self, client):
        url = "https://api.ebird.org/v2/data/obs/geo/recent/norcar?lat=40.71&lng=-74.01&dist=25&back=7"
        with patch.object(client._session, "get", return_value=self._resp([{}], url)), \
             patch("src.utils.ebird_client.logger") as mock_logger:
            client.recent_observations_by_location(40.71, -74.01, species_code="norcar")
        self._assert_curl(mock_logger, url)

    def test_recent_obs_by_region(self, client):
        url = "https://api.ebird.org/v2/data/obs/US-NY/recent?back=7"
        with patch.object(client._session, "get", return_value=self._resp([{}], url)), \
             patch("src.utils.ebird_client.logger") as mock_logger:
            client.recent_observations_by_region("US-NY", back=7)
        self._assert_curl(mock_logger, url)

    def test_recent_obs_by_region_with_species(self, client):
        url = "https://api.ebird.org/v2/data/obs/US-NY/recent/norcar?back=7"
        with patch.object(client._session, "get", return_value=self._resp([{}], url)), \
             patch("src.utils.ebird_client.logger") as mock_logger:
            client.recent_observations_by_region("US-NY", back=7, species_code="norcar")
        self._assert_curl(mock_logger, url)

    def test_historic_observations(self, client):
        url = "https://api.ebird.org/v2/data/obs/CA-ON/historic/2024/05/01"
        with patch.object(client._session, "get", return_value=self._resp([{}], url)), \
             patch("src.utils.ebird_client.logger") as mock_logger:
            client.historic_observations("CA-ON", year=2024, month=5, day=1)
        self._assert_curl(mock_logger, url)

    def test_notable_obs_by_location(self, client):
        url = "https://api.ebird.org/v2/data/obs/geo/recent/notable?lat=51.5&lng=-0.12&dist=10&back=7"
        with patch.object(client._session, "get", return_value=self._resp([{}], url)), \
             patch("src.utils.ebird_client.logger") as mock_logger:
            client.notable_observations_by_location(51.5, -0.12, dist=10, back=7)
        self._assert_curl(mock_logger, url)

    def test_nearby_hotspots(self, client):
        url = "https://api.ebird.org/v2/ref/hotspot/geo?lat=40.78&lng=-73.97&dist=25"
        with patch.object(client._session, "get", return_value=self._resp([{}], url)), \
             patch("src.utils.ebird_client.logger") as mock_logger:
            client.nearby_hotspots(40.78, -73.97, dist=25)
        self._assert_curl(mock_logger, url)

    def test_region_list(self, client):
        url = "https://api.ebird.org/v2/ref/region/list/subnational1/US"
        with patch.object(client._session, "get", return_value=self._resp([{}], url)), \
             patch("src.utils.ebird_client.logger") as mock_logger:
            client.region_list("US", "subnational1")
        self._assert_curl(mock_logger, url)

    def test_region_info(self, client):
        url = "https://api.ebird.org/v2/ref/region/info/US-NY"
        with patch.object(client._session, "get", return_value=self._resp({}, url)), \
             patch("src.utils.ebird_client.logger") as mock_logger:
            client.region_info("US-NY")
        self._assert_curl(mock_logger, url)

    def test_no_curl_on_cache_hit(self, client):
        """Second identical request must use the cache — no HTTP call, no curl log."""
        url = "https://api.ebird.org/v2/data/obs/geo/recent?lat=48.85&lng=2.35&dist=25&back=7"
        with patch.object(client._session, "get", return_value=self._resp([{}], url)), \
             patch("src.utils.ebird_client.logger") as mock_logger:
            client.recent_observations_by_location(48.85, 2.35)
            client.recent_observations_by_location(48.85, 2.35)  # cache hit
        assert mock_logger.info.call_count == 1
