"""
ebird_client.py — Thin HTTP client for the eBird API v2.

Reads EBIRD_API_KEY from the environment and injects it as the
x-ebirdapitoken request header for every call.
"""

import os
import time
from typing import Any

import requests


BASE_URL = "https://api.ebird.org/v2"

# How long (seconds) to serve a cached response before hitting the API again.
_CACHE_TTL = 3600  # 1 hour
# Maximum number of distinct queries to keep in memory.
_CACHE_MAX_ENTRIES = 200


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
        # (path, sorted-params) -> (fetched_at, response_data)
        self._cache: dict[tuple, tuple[float, Any]] = {}

    # ------------------------------------------------------------------
    # Low-level helper
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        cache_key = (path, tuple(sorted((params or {}).items())))
        entry = self._cache.get(cache_key)
        if entry is not None:
            fetched_at, data = entry
            if time.time() - fetched_at < _CACHE_TTL:
                return data

        url = f"{BASE_URL}{path}"
        response = self._session.get(url, params=params, timeout=15)
        if not response.ok:
            raise EBirdError(
                f"eBird API error {response.status_code} for {url}: {response.text}"
            )
        # eBird returns an empty body (not "[]") when there are no results.
        if not response.text or not response.text.strip():
            result = []
        else:
            result = response.json()
        self._cache[cache_key] = (time.time(), result)
        # Evict the oldest entry when the cache exceeds the size limit.
        if len(self._cache) > _CACHE_MAX_ENTRIES:
            oldest = min(self._cache, key=lambda k: self._cache[k][0])
            del self._cache[oldest]
        return result

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

    @staticmethod
    def _infer_region_type(parent_region_code: str) -> str:
        """Infer the child region_type from the parent code.

        'world'        -> 'country'
        'US' / 'CA'    -> 'subnational1'  (no hyphen = country code)
        'US-NY'/'CA-QC'-> 'subnational2'  (one hyphen = state/province code)
        """
        code = parent_region_code.strip().upper()
        if code == "WORLD":
            return "country"
        if "-" in code:
            return "subnational2"
        return "subnational1"

    def region_list(
        self,
        parent_region_code: str,
        region_type: str | None = None,
    ) -> list[dict]:
        """Sub-regions of a parent region.

        region_type: 'country' | 'subnational1' | 'subnational2'.
            Inferred from parent_region_code when omitted.
        parent_region_code: e.g. 'world', 'US', 'US-NY'
        """
        if region_type is None:
            region_type = self._infer_region_type(parent_region_code)
        path = f"/ref/region/list/{region_type}/{parent_region_code}"
        return self._get(path)

    def region_info(self, region_code: str) -> dict:
        """Info for a region, including its bounding box (minX/maxX/minY/maxY).

        Useful for a point-in-box test: check whether a lat/lng falls inside
        result['bounds']['minX']/maxX/minY/maxY to confirm the point intersects
        the region.
        """
        return self._get(f"/ref/region/info/{region_code}")

    # ------------------------------------------------------------------
    # Product / stats
    # ------------------------------------------------------------------

    def top100_contributors(
        self,
        region_code: str,
        year: int,
        month: int,
        day: int,
        ranked_by: str = "spp",
        max_results: int = 100,
    ) -> list[dict]:
        """Top 100 eBirders in a region for a given date.

        ranked_by: 'spp' (species count, default) or 'cl' (checklist count).
        max_results: number of results to return (1-100).
        """
        path = f"/product/top100/{region_code}/{year}/{month:02d}/{day:02d}"
        return self._get(path, {"rankedBy": ranked_by, "maxResults": max_results})

    def species_list(self, region_code: str) -> list[str]:
        """All species ever recorded in a region, as a list of species codes."""
        return self._get(f"/product/spplist/{region_code}")

    def region_stats(
        self,
        region_code: str,
        year: int,
        month: int,
        day: int,
    ) -> dict:
        """Checklist and contributor stats for a region on a specific date."""
        path = f"/product/stats/{region_code}/{year}/{month:02d}/{day:02d}"
        return self._get(path)
