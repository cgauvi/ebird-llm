"""
state.py — Shared in-process side-channel between LangChain viz tools and the
Streamlit UI.  Tools write here; app.py reads and renders the payload.

Structure of VizBuffer:
    {
        "type": "map" | "chart" | None,
        "data": <folium.Map object>  |  <plotly fig dict>  |  None,
        "title": str | None,
        "table": list[dict] | None,   # top-10 rows shown below the map
    }
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

VizBuffer: dict = {
    "type": None,
    "data": None,
    "title": None,
    "table": None,  # list[dict] | None — top rows shown below the map
}


def clear_viz_buffer() -> None:
    """Reset VizBuffer so stale visuals are not re-rendered on the next turn."""
    VizBuffer["type"] = None
    VizBuffer["data"] = None
    VizBuffer["title"] = None
    VizBuffer["table"] = None


# ---------------------------------------------------------------------------
# Last-observations cache — stores the most recent eBird observation JSON so
# viz tools can recover when the LLM passes a malformed/truncated string.
# ---------------------------------------------------------------------------

_last_observations_json: str | None = None


def get_last_observations() -> str | None:
    """Return the raw JSON string from the most recent eBird observation tool call."""
    return _last_observations_json


def set_last_observations(json_str: str) -> None:
    """Persist the raw JSON returned by an eBird observation tool."""
    global _last_observations_json
    _last_observations_json = json_str


# ---------------------------------------------------------------------------
# Observations DataFrame cache — stores the most recent eBird observations as
# a pandas DataFrame so viz tools can read it without the LLM re-passing data.
# ---------------------------------------------------------------------------

_obs_dataframe: "pd.DataFrame | None" = None


def get_obs_dataframe() -> "pd.DataFrame | None":
    """Return the DataFrame built from the most recent eBird observation tool call."""
    return _obs_dataframe


def set_obs_dataframe(df: "pd.DataFrame") -> None:
    """Persist a DataFrame of observations for viz tool consumption."""
    global _obs_dataframe
    _obs_dataframe = df


# ---------------------------------------------------------------------------
# Last-observations file cache — stores the path to the JSON file written by
# the most recent eBird observation tool call.
# ---------------------------------------------------------------------------

_last_obs_file: str | None = None


def get_last_obs_file() -> str | None:
    """Return the path to the JSON file from the most recent eBird observation tool call."""
    return _last_obs_file


def set_last_obs_file(path: str) -> None:
    """Persist the path to the observations JSON file written by an eBird tool."""
    global _last_obs_file
    _last_obs_file = path


# ---------------------------------------------------------------------------
# Known-species cache — species records extracted from the last set of
# observations so that validate_species can resolve names and codes without
# a round-trip to the API.
# ---------------------------------------------------------------------------

_known_species: list[dict] = []


def get_known_species() -> list[dict]:
    """Return species records (speciesCode, comName, sciName) from the last observations."""
    return _known_species


def set_known_species(records: list[dict]) -> None:
    """Extract and cache unique species entries from an observation record list."""
    global _known_species
    seen: set[str] = set()
    species: list[dict] = []
    for r in records:
        code = r.get("speciesCode", "")
        if code and code not in seen:
            seen.add(code)
            species.append({
                "speciesCode": code,
                "comName": r.get("comName", ""),
                "sciName": r.get("sciName", ""),
            })
    _known_species = species
