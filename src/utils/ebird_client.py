"""
ebird_client.py — Thin HTTP client for the eBird API v2.

Reads EBIRD_API_KEY from the environment and injects it as the
x-ebirdapitoken request header for every call.
"""

import os
from typing import Any

import requests


BASE_URL = "https://api.ebird.org/v2"


class EBirdError(Exception):
    """Raised when the eBird API returns a non-2xx response."""


class EBirdClient:
    def __init__(self) -> None:
        api_key = os.environ.get("EBIRD_API_KEY", "")
        if not api_key:
            raise EnvironmentError(
                "EBIRD_API_KEY is not set. "
                "Copy .env.example to .env and fill in your key."
            )
        self._session = requests.Session()
        self._session.headers.update({"x-ebirdapitoken": api_key})

    # ------------------------------------------------------------------
    # Low-level helper
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{BASE_URL}{path}"
        response = self._session.get(url, params=params, timeout=15)
        if not response.ok:
            raise EBirdError(
                f"eBird API error {response.status_code} for {url}: {response.text}"
            )
        return response.json()

    # ------------------------------------------------------------------
    # Observations
    # ------------------------------------------------------------------

    def recent_observations_by_location(
        self,
        lat: float,
        lng: float,
        dist: int = 25,
        back: int = 7,
        species_code: str | None = None,
    ) -> list[dict]:
        """Recent observations near a lat/lng point."""
        if species_code:
            path = f"/data/obs/geo/recent/{species_code}"
        else:
            path = "/data/obs/geo/recent"
        params: dict[str, Any] = {"lat": lat, "lng": lng, "dist": dist, "back": back}
        return self._get(path, params)

    def recent_observations_by_region(
        self,
        region_code: str,
        back: int = 7,
        species_code: str | None = None,
    ) -> list[dict]:
        """Recent observations in a region (e.g. 'US-NY')."""
        if species_code:
            path = f"/data/obs/{region_code}/recent/{species_code}"
        else:
            path = f"/data/obs/{region_code}/recent"
        return self._get(path, {"back": back})

    def historic_observations(
        self,
        region_code: str,
        year: int,
        month: int,
        day: int,
    ) -> list[dict]:
        """All observations recorded in a region on a specific date."""
        path = f"/data/obs/{region_code}/historic/{year}/{month:02d}/{day:02d}"
        return self._get(path)

    def notable_observations_by_location(
        self,
        lat: float,
        lng: float,
        dist: int = 25,
        back: int = 7,
    ) -> list[dict]:
        """Rare / notable observations near a lat/lng point."""
        params: dict[str, Any] = {"lat": lat, "lng": lng, "dist": dist, "back": back}
        return self._get("/data/obs/geo/recent/notable", params)

    # ------------------------------------------------------------------
    # Hotspots
    # ------------------------------------------------------------------

    def nearby_hotspots(
        self,
        lat: float,
        lng: float,
        dist: int = 25,
    ) -> list[dict]:
        """eBird hotspots within dist km of a point."""
        params: dict[str, Any] = {"lat": lat, "lng": lng, "dist": dist}
        return self._get("/ref/hotspot/geo", params)

    # ------------------------------------------------------------------
    # Regions
    # ------------------------------------------------------------------

    def region_list(
        self,
        region_type: str,
        parent_region_code: str,
    ) -> list[dict]:
        """Sub-regions of a parent region.

        region_type: 'country' | 'subnational1' | 'subnational2'
        parent_region_code: e.g. 'world', 'US', 'US-NY'
        """
        path = f"/ref/region/list/{region_type}/{parent_region_code}"
        return self._get(path)
