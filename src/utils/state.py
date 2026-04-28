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
# Turn tracking — lets viz tools detect when their cache fallback would
# silently render data fetched in a prior user turn.
# ---------------------------------------------------------------------------

_current_turn_id: int = 0
_obs_cache_turn_id: int = -1  # -1 = no eBird fetch has happened yet


def start_new_turn() -> int:
    """Bump the turn counter. Caches written before this point are now stale.

    Called by app.py at the start of every user turn so that viz tools can
    detect when the LLM is about to render data left over from a previous turn.
    """
    global _current_turn_id
    _current_turn_id += 1
    return _current_turn_id


def mark_obs_cache_current() -> None:
    """Tag the obs cache as written during the current turn."""
    global _obs_cache_turn_id
    _obs_cache_turn_id = _current_turn_id


def obs_cache_is_current_turn() -> bool:
    """True when the obs cache was populated during the current turn."""
    return _obs_cache_turn_id == _current_turn_id


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


# ---------------------------------------------------------------------------
# Last-search-params cache — stores the parameters used in the most recent
# eBird observation query so the agent can suggest them when the user is vague.
# ---------------------------------------------------------------------------

_last_search_params: "dict | None" = None


def get_last_search_params() -> "dict | None":
    """Return the parameters used in the most recent eBird observation query."""
    return _last_search_params


def set_last_search_params(params: dict) -> None:
    """Persist the parameters (region, date, coordinates, species, days_back) from the last query."""
    global _last_search_params
    _last_search_params = params


# ---------------------------------------------------------------------------
# Observations history — keeps recent fetches keyed by region label so the
# chart tool can render multi-region comparisons on a single figure.
# ---------------------------------------------------------------------------

_MAX_OBS_HISTORY = 10
_obs_history: list[dict] = []  # list of {"region": str, "records": list[dict]}


def append_obs_history(records: list[dict], region_label: str) -> None:
    """Add (or replace) the observation set for *region_label* in the history.

    Re-running a query for the same region replaces its prior entry so the
    history stays a clean per-region snapshot. Capped at _MAX_OBS_HISTORY.
    """
    global _obs_history
    if not records or not region_label:
        return
    _obs_history = [e for e in _obs_history if e["region"] != region_label]
    _obs_history.append({"region": region_label, "records": records})
    if len(_obs_history) > _MAX_OBS_HISTORY:
        _obs_history = _obs_history[-_MAX_OBS_HISTORY:]


def get_obs_history() -> list[dict]:
    """Return the list of region-tagged observation sets accumulated this session."""
    return _obs_history


def clear_obs_history() -> None:
    """Reset the per-region observation history (e.g. on New Conversation)."""
    global _obs_history
    _obs_history = []


def region_label_from_params(params: "dict | None") -> str:
    """Derive a stable region label from search params for history tagging."""
    if not params:
        return "default"
    code = params.get("region_code")
    if code:
        return str(code)
    lat, lng = params.get("lat"), params.get("lng")
    if lat is not None and lng is not None:
        return f"{float(lat):.3f},{float(lng):.3f}"
    return str(params.get("query_type") or "default")
