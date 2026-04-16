"""
ebird_tools.py — LangChain tools that wrap eBird API endpoints.

Each tool:
  • Is decorated with @tool so LangChain can discover and call it.
  • Validates inputs via Pydantic models (tool_input_schema).
  • Returns a JSON string the agent can read and optionally pass to a viz tool.
  • Raises ToolException (surfaced as a user-visible error) on API failures.
"""

import json
from typing import Optional

from langchain.tools import tool
from langchain_core.tools import ToolException

from src.utils.ebird_client import EBirdClient, EBirdError


# A single shared client instance — created lazily so that the import does not
# fail when the env var is missing at import time.
_client: Optional[EBirdClient] = None


def _get_client() -> EBirdClient:
    global _client
    if _client is None:
        _client = EBirdClient()
    return _client


# ---------------------------------------------------------------------------
# Tool 1 — Recent observations near a geographic point
# ---------------------------------------------------------------------------


@tool
def get_recent_observations_by_location(
    lat: float,
    lng: float,
    dist_km: int = 25,
    days_back: int = 7,
    species_code: Optional[str] = None,
) -> str:
    """Return recent bird observations near a latitude/longitude coordinate.

    Args:
        lat: Latitude of the center point (decimal degrees, e.g. 48.85).
        lng: Longitude of the center point (decimal degrees, e.g. 2.35).
        dist_km: Search radius in kilometres (1–50, default 25).
        days_back: How many days back to search (1–30, default 7).
        species_code: Optional eBird species code to filter results
            (e.g. 'norcar' for Northern Cardinal).

    Returns:
        JSON array of observation records.  Key fields per record:
          comName, sciName, speciesCode, howMany, lat, lng,
          obsDt, locName, locId.
    """
    try:
        records = _get_client().recent_observations_by_location(
            lat=lat,
            lng=lng,
            dist=dist_km,
            back=days_back,
            species_code=species_code,
        )
        return json.dumps(records)
    except EBirdError as exc:
        raise ToolException(str(exc)) from exc


# ---------------------------------------------------------------------------
# Tool 2 — Recent observations in a named region
# ---------------------------------------------------------------------------


@tool
def get_recent_observations_by_region(
    region_code: str,
    days_back: int = 7,
    species_code: Optional[str] = None,
) -> str:
    """Return recent bird observations for an eBird region code.

    Args:
        region_code: eBird region code (e.g. 'US-NY' for New York,
            'FR-75' for Paris, 'US' for the whole United States).
        days_back: How many days back to search (1–30, default 7).
        species_code: Optional eBird species code to filter results.

    Returns:
        JSON array of observation records. Key fields: comName, sciName,
        speciesCode, howMany, lat, lng, obsDt, locName, locId.
    """
    try:
        records = _get_client().recent_observations_by_region(
            region_code=region_code.strip(),
            back=days_back,
            species_code=species_code,
        )
        return json.dumps(records)
    except EBirdError as exc:
        raise ToolException(str(exc)) from exc


# ---------------------------------------------------------------------------
# Tool 3 — Historic observations on a specific date
# ---------------------------------------------------------------------------


@tool
def get_historic_observations(
    region_code: str,
    year: int,
    month: int,
    day: int,
) -> str:
    """Return all bird observations recorded in a region on a specific date.

    Useful for understanding seasonal patterns or reviewing past big-day lists.

    Args:
        region_code: eBird region code (e.g. 'US-NY', 'CA-ON', 'MX').
        year: 4-digit year (e.g. 2024).
        month: Month number 1–12.
        day: Day number 1–31.

    Returns:
        JSON array of observation records. Key fields: comName, sciName,
        speciesCode, howMany, lat, lng, obsDt, locName, locId.
    """
    try:
        records = _get_client().historic_observations(
            region_code=region_code.strip(),
            year=year,
            month=month,
            day=day,
        )
        return json.dumps(records)
    except EBirdError as exc:
        raise ToolException(str(exc)) from exc


# ---------------------------------------------------------------------------
# Tool 4 — Nearby hotspots
# ---------------------------------------------------------------------------


@tool
def get_nearby_hotspots(
    lat: float,
    lng: float,
    dist_km: int = 25,
) -> str:
    """Return eBird birding hotspots within a given radius of a coordinate.

    Args:
        lat: Latitude of the center point (decimal degrees).
        lng: Longitude of the center point (decimal degrees).
        dist_km: Search radius in kilometres (1–50, default 25).

    Returns:
        JSON array of hotspot records. Key fields: locId, locName,
        lat, lng, latestObsDt, numSpeciesAllTime.
    """
    try:
        records = _get_client().nearby_hotspots(lat=lat, lng=lng, dist=dist_km)
        return json.dumps(records)
    except EBirdError as exc:
        raise ToolException(str(exc)) from exc


# ---------------------------------------------------------------------------
# Tool 5 — Sub-region listing
# ---------------------------------------------------------------------------


@tool
def get_region_list(
    region_type: str,
    parent_region_code: str,
) -> str:
    """Return the list of sub-regions inside a parent eBird region.

    Args:
        region_type: Hierarchy level of the child regions to return.
            One of: 'country', 'subnational1', 'subnational2'.
            Example: 'subnational1' returns states/provinces inside a country.
        parent_region_code: eBird code of the parent region.
            Examples: 'world' (all countries), 'US' (US states), 'US-NY' (NY counties).

    Returns:
        JSON array of region records. Key fields: code, name.
    """
    valid_types = {"country", "subnational1", "subnational2"}
    if region_type not in valid_types:
        raise ToolException(
            f"region_type must be one of {valid_types}, got '{region_type}'"
        )
    try:
        records = _get_client().region_list(
            region_type=region_type,
            parent_region_code=parent_region_code.strip(),
        )
        return json.dumps(records)
    except EBirdError as exc:
        raise ToolException(str(exc)) from exc


# ---------------------------------------------------------------------------
# Tool 6 — Notable / rare observations near a point
# ---------------------------------------------------------------------------


@tool
def get_notable_observations_by_location(
    lat: float,
    lng: float,
    dist_km: int = 25,
    days_back: int = 7,
) -> str:
    """Return rare or notable bird sightings near a coordinate.

    Notable means the observation was flagged as unusual for the location or time.

    Args:
        lat: Latitude (decimal degrees).
        lng: Longitude (decimal degrees).
        dist_km: Search radius in kilometres (1–50, default 25).
        days_back: How many days back to search (1–30, default 7).

    Returns:
        JSON array of observation records. Key fields: comName, sciName,
        speciesCode, howMany, lat, lng, obsDt, locName, locId.
    """
    try:
        records = _get_client().notable_observations_by_location(
            lat=lat,
            lng=lng,
            dist=dist_km,
            back=days_back,
        )
        return json.dumps(records)
    except EBirdError as exc:
        raise ToolException(str(exc)) from exc


# ---------------------------------------------------------------------------
# Public list used by agent.py
# ---------------------------------------------------------------------------

EBIRD_TOOLS = [
    get_recent_observations_by_location,
    get_recent_observations_by_region,
    get_historic_observations,
    get_nearby_hotspots,
    get_region_list,
    get_notable_observations_by_location,
]
