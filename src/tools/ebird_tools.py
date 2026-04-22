"""
ebird_tools.py — LangChain tools that wrap eBird API endpoints.

Each tool:
  • Is decorated with @tool so LangChain can discover and call it.
  • Validates inputs via Pydantic models (tool_input_schema).
  • Returns a JSON string the agent can read and optionally pass to a viz tool.
  • Raises ToolException (surfaced as a user-visible error) on API failures.
"""

import datetime
import difflib
import json
import re
from pathlib import Path
from typing import Optional

import pandas as pd
from langchain.tools import tool
from langchain_core.tools import ToolException

from src.utils.ebird_client import EBirdClient, EBirdError
from src.utils.region_cache import register_codes, validate_region_code
from src.utils.state import set_last_observations, set_last_obs_file, set_obs_dataframe, set_known_species
from src.utils.summarizer import SUMMARIES_DIR


# ---------------------------------------------------------------------------
# Observation result helper — cache and return
# ---------------------------------------------------------------------------


def _return_obs(records: list, note: str | None = None) -> str:
    """Store observation records locally and return a compact summary for the LLM.

    The full JSON is written to a temp file so viz tools can read it by path.
    The LLM receives a compact summary that includes the file path, record count,
    field names, and top species — everything needed to drive the next tool call.
    """
    # Persist full JSON for the session-cache fallback path
    json_str = json.dumps({"_note": note, "observations": records} if note else records)
    set_last_observations(json_str)

    # Store as DataFrame for zero-copy access by viz tools
    df = pd.DataFrame(records) if records else pd.DataFrame()
    set_obs_dataframe(df)
    set_known_species(records)

    # Save observations to a uniquely named JSON file
    SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    obs_file = SUMMARIES_DIR / f"observations_{timestamp}.json"
    obs_file.write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    set_last_obs_file(str(obs_file))

    # Build compact summary — all the LLM needs to decide what to do next
    n = len(records)
    parts: list[str] = []
    if note:
        parts.append(note)
    parts.append(f"Retrieved {n} observations.")

    if not df.empty:
        parts.append(f"Fields: {', '.join(df.columns)}.")
        if "comName" in df.columns:
            counts = pd.to_numeric(
                df["howMany"] if "howMany" in df.columns else pd.Series(dtype=float),
                errors="coerce",
            ).fillna(1)
            top = (
                df.assign(_cnt=counts)
                .groupby("comName")["_cnt"]
                .sum()
                .sort_values(ascending=False)
                .head(5)
            )
            parts.append(
                "Top species: " + ", ".join(f"{sp} ({int(c)})" for sp, c in top.items()) + "."
            )

    parts.append(
        'Call show_observations_table to display the data as a table, '
        'or create_sightings_map or create_historical_chart to visualize.'
    )
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Input validators
# ---------------------------------------------------------------------------

# eBird species codes: 2–10 lowercase alphanumeric characters (e.g. 'norcar', 'y00478').
_SPECIES_CODE_RE = re.compile(r'^[a-z][a-z0-9]{1,9}$')


def _validate_species_code(code: str) -> str | None:
    if not _SPECIES_CODE_RE.match(code):
        return (
            f"'{code}' is not a valid eBird species code. "
            "Species codes are 2–10 lowercase letters/digits (e.g. 'norcar', 'amerob'). "
            "Do not pass a common name, scientific name, or uppercase text as a species code."
        )
    return None


def _validate_lat_lng(lat: float, lng: float) -> str | None:
    if not -90 <= lat <= 90:
        return (
            f"lat={lat} is out of range. Latitude must be between -90 and 90. "
            "Make sure you have not swapped lat and lng."
        )
    if not -180 <= lng <= 180:
        return (
            f"lng={lng} is out of range. Longitude must be between -180 and 180. "
            "Make sure you have not swapped lat and lng."
        )
    return None


def _validate_date(year: int, month: int, day: int) -> str | None:
    try:
        dt = datetime.date(year, month, day)
    except ValueError as exc:
        return f"Invalid date {year}-{month:02d}-{day:02d}: {exc}."
    if dt > datetime.date.today():
        return (
            f"Date {dt} is in the future. "
            "Historic observations are only available for past dates."
        )
    if year < 1800:
        return f"Year {year} is too far in the past. eBird data is not available before 1800."
    return None


def _require_results(records: list, call_description: str) -> None:
    """Raise ToolException with the call details when the API returns nothing."""
    if not records:
        raise ToolException(
            f"The eBird API returned no results for: {call_description}. "
            "Try widening the search (larger radius, more days back, or a broader region)."
        )


# A single shared client instance — created lazily so that the import does not
# fail when the env var is missing at import time.
_client: Optional[EBirdClient] = None


def _get_client() -> EBirdClient:
    global _client
    if _client is None:
        _client = EBirdClient()
    return _client


# ---------------------------------------------------------------------------
# Region auto-correction
# ---------------------------------------------------------------------------


def _autocorrect_subregion(bad_code: str) -> tuple[str, str] | None:
    """Look up valid sub-regions for the parent and return the closest match.

    Args:
        bad_code: The unrecognised region code (already upper-cased).

    Returns:
        ``(corrected_code, region_name)`` for the best fuzzy match, or ``None``
        if the parent lookup fails or returns no results.
    """
    parts = bad_code.split("-")
    if len(parts) == 3:
        parent = "-".join(parts[:2])
        region_type = "subnational2"
    elif len(parts) == 2:
        parent = parts[0]
        region_type = "subnational1"
    else:
        return None

    try:
        records = _get_client().region_list(
            parent_region_code=parent,
            region_type=region_type,
        )
    except EBirdError:
        return None

    if not records:
        return None

    from src.utils.region_cache import register_codes
    register_codes([r["code"] for r in records if "code" in r])

    bad_suffix = parts[-1].lower()
    best_code: str | None = None
    best_name: str | None = None
    best_score = 0.0

    for r in records:
        code = r.get("code", "")
        name = r.get("name", "")
        if not code:
            continue
        code_suffix = code.split("-")[-1].lower()
        score_suffix = difflib.SequenceMatcher(None, bad_suffix, code_suffix).ratio()
        score_name = difflib.SequenceMatcher(None, bad_suffix, name.lower()).ratio()
        score = max(score_suffix, score_name)
        if score > best_score:
            best_score, best_code, best_name = score, code, name

    if best_code:
        return best_code, best_name or best_code

    # Records were fetched but no suffix had any character overlap with bad_suffix
    # (e.g. numeric '01' vs purely alphabetic codes like 'ABI').  Raise a
    # ToolException here — rather than returning None and letting callers raise a
    # less-informative error — so the LLM receives actual valid codes immediately
    # and can retry without an extra get_region_list round-trip.
    parts_outer = bad_code.split("-")
    parent_outer = "-".join(parts_outer[:-1])
    example_codes = [r["code"] for r in records[:8] if r.get("code")]
    tail = f" (and {len(records) - 8} more)" if len(records) > 8 else ""
    raise ToolException(
        f"'{bad_code}' could not be matched to a valid sub-region code for {parent_outer}. "
        f"Valid codes include: {', '.join(example_codes)}{tail}. "
        "Use one of these exact codes."
    )


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
    err = _validate_lat_lng(lat, lng)
    if err:
        raise ToolException(err)
    if species_code:
        err = _validate_species_code(species_code)
        if err:
            raise ToolException(err)
    try:
        records = _get_client().recent_observations_by_location(
            lat=lat,
            lng=lng,
            dist=dist_km,
            back=days_back,
            species_code=species_code,
        )
        _require_results(
            records,
            f"recent observations near lat={lat}, lng={lng}, dist_km={dist_km}, "
            f"days_back={days_back}"
            + (f", species_code={species_code}" if species_code else ""),
        )
        return _return_obs(records)
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
    code = region_code.strip().upper()
    correction_note: str | None = None
    err = validate_region_code(code)
    if err:
        correction = _autocorrect_subregion(code)
        if correction:
            corrected_code, corrected_name = correction
            correction_note = (
                f"'{code}' was not recognised; automatically used "
                f"'{corrected_code}' ({corrected_name}) as the closest match."
            )
            code = corrected_code
        else:
            raise ToolException(err)
    if species_code:
        err = _validate_species_code(species_code)
        if err:
            raise ToolException(err)
    try:
        records = _get_client().recent_observations_by_region(
            region_code=code,
            back=days_back,
            species_code=species_code,
        )
        _require_results(
            records,
            f"recent observations in region={code}, days_back={days_back}"
            + (f", species_code={species_code}" if species_code else ""),
        )
        return _return_obs(records, note=correction_note if correction_note else None)
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
    code = region_code.strip().upper()
    correction_note: str | None = None
    err = validate_region_code(code)
    if err:
        correction = _autocorrect_subregion(code)
        if correction:
            corrected_code, corrected_name = correction
            correction_note = (
                f"'{code}' was not recognised; automatically used "
                f"'{corrected_code}' ({corrected_name}) as the closest match."
            )
            code = corrected_code
        else:
            raise ToolException(err)
    err = _validate_date(year, month, day)
    if err:
        raise ToolException(err)
    try:
        records = _get_client().historic_observations(
            region_code=code,
            year=year,
            month=month,
            day=day,
        )
        _require_results(
            records,
            f"historic observations in region={code} on {year}-{month:02d}-{day:02d}",
        )
        return _return_obs(records, note=correction_note if correction_note else None)
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
    err = _validate_lat_lng(lat, lng)
    if err:
        raise ToolException(err)
    try:
        records = _get_client().nearby_hotspots(lat=lat, lng=lng, dist=dist_km)
        _require_results(records, f"nearby hotspots near lat={lat}, lng={lng}, dist_km={dist_km}")
        return json.dumps(records)
    except EBirdError as exc:
        raise ToolException(str(exc)) from exc


# ---------------------------------------------------------------------------
# Tool 5 — Sub-region listing
# ---------------------------------------------------------------------------


@tool
def get_region_list(
    parent_region_code: str,
    region_type: str | None = None,
) -> str:
    """Return the list of sub-regions inside a parent eBird region.

    Args:
        parent_region_code: eBird code of the parent region.
            Examples: 'world' (all countries), 'US' (US states), 'US-NY' (NY counties).
        region_type: Hierarchy level of the child regions to return.
            One of: 'country', 'subnational1', 'subnational2'.
            Inferred automatically when omitted:
              'world' -> 'country', plain country code -> 'subnational1',
              state/province code (contains '-') -> 'subnational2'.

    Returns:
        JSON array of region records. Key fields: code, name.
    """
    valid_types = {"country", "subnational1", "subnational2"}
    if region_type is not None and region_type not in valid_types:
        raise ToolException(
            f"region_type must be one of {valid_types}, got '{region_type}'"
        )
    parent = parent_region_code.strip().upper()
    try:
        records = _get_client().region_list(
            parent_region_code=parent,
            region_type=region_type,
        )
        # Persist returned codes so future validation can check them.
        # Also record the parent as fully-fetched so the cache validator knows
        # it can safely reject any code not in this list.
        register_codes([r["code"] for r in records if "code" in r], parent=parent)
        output = [
            {"code": r["code"], "name": r["name"]}
            for r in records
            if "code" in r and "name" in r
        ]
        return json.dumps(output, ensure_ascii=False)
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
    err = _validate_lat_lng(lat, lng)
    if err:
        raise ToolException(err)
    try:
        records = _get_client().notable_observations_by_location(
            lat=lat,
            lng=lng,
            dist=dist_km,
            back=days_back,
        )
        _require_results(
            records,
            f"notable observations near lat={lat}, lng={lng}, dist_km={dist_km}, days_back={days_back}",
        )
        return _return_obs(records)
    except EBirdError as exc:
        raise ToolException(str(exc)) from exc


# ---------------------------------------------------------------------------
# Tool 7 — Region info (bounding box + metadata)
# ---------------------------------------------------------------------------


@tool
def get_region_info(region_code: str) -> str:
    """Return metadata and bounding-box coordinates for an eBird region.

    Useful for confirming that a region code is valid, checking its geographic
    extent, or verifying that a lat/lng falls inside a region.

    Args:
        region_code: eBird region code (e.g. 'US-NY', 'CA', 'FR-75').

    Returns:
        JSON object with fields: result (region name), bounds
        (minX, maxX, minY, maxY in decimal degrees).
    """
    code = region_code.strip().upper()
    try:
        data = _get_client().region_info(code)
        return json.dumps(data, ensure_ascii=False)
    except EBirdError as exc:
        raise ToolException(str(exc)) from exc


# ---------------------------------------------------------------------------
# Tool 8 — Top-100 contributors for a region/date
# ---------------------------------------------------------------------------


@tool
def get_top100_contributors(
    region_code: str,
    year: int,
    month: int,
    day: int,
    ranked_by: str = "spp",
    max_results: int = 100,
) -> str:
    """Return the top eBirders in a region for a specific date.

    Args:
        region_code: eBird region code (e.g. 'US-NY', 'CA-ON').
        year: 4-digit year.
        month: Month number 1–12.
        day: Day number 1–31.
        ranked_by: 'spp' to rank by species count (default) or 'cl' for
            checklist count.
        max_results: Number of results to return (1–100, default 100).

    Returns:
        JSON array of contributor records. Key fields: userId, userDisplayName,
        numSpecies (or numCompleteChecklists).
    """
    code = region_code.strip().upper()
    err = validate_region_code(code)
    if err:
        correction = _autocorrect_subregion(code)
        if correction:
            code, _ = correction
        else:
            raise ToolException(err)
    err = _validate_date(year, month, day)
    if err:
        raise ToolException(err)
    if ranked_by not in ("spp", "cl"):
        raise ToolException("ranked_by must be 'spp' (species) or 'cl' (checklists).")
    if not 1 <= max_results <= 100:
        raise ToolException("max_results must be between 1 and 100.")
    try:
        records = _get_client().top100_contributors(
            region_code=code,
            year=year,
            month=month,
            day=day,
            ranked_by=ranked_by,
            max_results=max_results,
        )
        _require_results(
            records,
            f"top-100 contributors for region={code} on {year}-{month:02d}-{day:02d}",
        )
        return json.dumps(records, ensure_ascii=False)
    except EBirdError as exc:
        raise ToolException(str(exc)) from exc


# ---------------------------------------------------------------------------
# Tool 9 — Species list for a region
# ---------------------------------------------------------------------------


@tool
def get_species_list(region_code: str) -> str:
    """Return all bird species ever recorded in an eBird region.

    Args:
        region_code: eBird region code (e.g. 'US-NY', 'CA', 'MX').

    Returns:
        JSON array of eBird species codes (strings), e.g. ["norcar", "amerob"].
        The list can be long (hundreds of entries for large regions).
    """
    code = region_code.strip().upper()
    err = validate_region_code(code)
    if err:
        correction = _autocorrect_subregion(code)
        if correction:
            code, _ = correction
        else:
            raise ToolException(err)
    try:
        species = _get_client().species_list(code)
        _require_results(species, f"species list for region={code}")
        return json.dumps(species, ensure_ascii=False)
    except EBirdError as exc:
        raise ToolException(str(exc)) from exc


# ---------------------------------------------------------------------------
# Tool 10 — Region stats for a specific date
# ---------------------------------------------------------------------------


@tool
def get_region_stats(
    region_code: str,
    year: int,
    month: int,
    day: int,
) -> str:
    """Return checklist and contributor statistics for a region on a specific date.

    Args:
        region_code: eBird region code (e.g. 'US-NY', 'CA-ON').
        year: 4-digit year.
        month: Month number 1–12.
        day: Day number 1–31.

    Returns:
        JSON object with fields: numChecklists, numContributors, numSpecies.
    """
    code = region_code.strip().upper()
    err = validate_region_code(code)
    if err:
        correction = _autocorrect_subregion(code)
        if correction:
            code, _ = correction
        else:
            raise ToolException(err)
    err = _validate_date(year, month, day)
    if err:
        raise ToolException(err)
    try:
        data = _get_client().region_stats(
            region_code=code,
            year=year,
            month=month,
            day=day,
        )
        return json.dumps(data, ensure_ascii=False)
    except EBirdError as exc:
        raise ToolException(str(exc)) from exc


# ---------------------------------------------------------------------------
# Tool 11 — Validate a species name or code
# ---------------------------------------------------------------------------


@tool
def validate_species(
    species_query: str,
    region_code: Optional[str] = None,
) -> str:
    """Validate that a species name or code exists before using it in another tool.

    Checks the species against recent observation data cached in the session, then
    (if a region_code is provided) against the full regional species list.

    Call this BEFORE passing a species name or code to any other tool.

    Args:
        species_query: A common name, scientific name, or eBird species code
            to look up (e.g. 'Northern Cardinal', 'Cardinalis cardinalis', 'norcar').
        region_code: Optional eBird region code (e.g. 'US-NY') to fall back to the
            regional species list when the species is not in recent observations.

    Returns:
        JSON with 'found' (bool), 'species_code', 'comName', 'sciName' when found,
        or 'suggestions' (list of close matches) when not found.
    """
    from src.utils.state import get_known_species

    query = species_query.strip().lower()

    # 1. Check in-memory observations cache (exact match on code, comName, sciName)
    known = get_known_species()
    for r in known:
        if (
            r.get("speciesCode", "").lower() == query
            or r.get("comName", "").lower() == query
            or r.get("sciName", "").lower() == query
        ):
            return json.dumps({
                "found": True,
                "source": "last_observations",
                "species_code": r.get("speciesCode"),
                "comName": r.get("comName"),
                "sciName": r.get("sciName"),
            })

    # Fuzzy match against observations cache
    if known:
        candidates: list[tuple[float, dict]] = []
        for r in known:
            score = max(
                difflib.SequenceMatcher(None, query, r.get("speciesCode", "").lower()).ratio(),
                difflib.SequenceMatcher(None, query, r.get("comName", "").lower()).ratio(),
                difflib.SequenceMatcher(None, query, r.get("sciName", "").lower()).ratio(),
            )
            if score > 0.55:
                candidates.append((score, r))
        if candidates:
            candidates.sort(key=lambda x: -x[0])
            return json.dumps({
                "found": False,
                "source": "last_observations",
                "message": f"'{species_query}' not found in recent observations.",
                "suggestions": [
                    {
                        "species_code": r.get("speciesCode"),
                        "comName": r.get("comName"),
                        "sciName": r.get("sciName"),
                    }
                    for _, r in candidates[:3]
                ],
            })

    # 2. Fall back to regional species list (codes only)
    if region_code:
        code = region_code.strip().upper()
        err = validate_region_code(code)
        if err:
            correction = _autocorrect_subregion(code)
            if correction:
                code, _ = correction
            else:
                raise ToolException(err)
        try:
            species_codes: list[str] = _get_client().species_list(code)
        except EBirdError as exc:
            raise ToolException(str(exc)) from exc

        codes_lower = [s.lower() for s in species_codes]
        if query in codes_lower:
            matched_code = species_codes[codes_lower.index(query)]
            return json.dumps({
                "found": True,
                "source": f"species_list:{code}",
                "species_code": matched_code,
            })

        # Fuzzy match against codes
        close = difflib.get_close_matches(query, codes_lower, n=3, cutoff=0.6)
        return json.dumps({
            "found": False,
            "source": f"species_list:{code}",
            "message": f"'{species_query}' not found in species list for {code}.",
            "suggestions": close,
        })

    # 3. Fall back to eBird taxonomy search (searches by common/scientific name)
    try:
        tax_records = _get_client().taxonomy_search(query)
    except EBirdError:
        tax_records = []

    if tax_records:
        # Exact match first
        for r in tax_records:
            if (
                r.get("speciesCode", "").lower() == query
                or r.get("comName", "").lower() == query
                or r.get("sciName", "").lower() == query
            ):
                return json.dumps({
                    "found": True,
                    "source": "taxonomy",
                    "species_code": r.get("speciesCode"),
                    "comName": r.get("comName"),
                    "sciName": r.get("sciName"),
                })
        # Best fuzzy match from taxonomy results
        best_score = 0.0
        best_record: dict = {}
        for r in tax_records:
            score = max(
                difflib.SequenceMatcher(None, query, r.get("speciesCode", "").lower()).ratio(),
                difflib.SequenceMatcher(None, query, r.get("comName", "").lower()).ratio(),
                difflib.SequenceMatcher(None, query, r.get("sciName", "").lower()).ratio(),
            )
            if score > best_score:
                best_score, best_record = score, r
        if best_score >= 0.4 and best_record:
            return json.dumps({
                "found": True,
                "source": "taxonomy",
                "species_code": best_record.get("speciesCode"),
                "comName": best_record.get("comName"),
                "sciName": best_record.get("sciName"),
            })
        suggestions = [
            {
                "species_code": r.get("speciesCode"),
                "comName": r.get("comName"),
                "sciName": r.get("sciName"),
            }
            for r in tax_records[:3]
        ]
        return json.dumps({
            "found": False,
            "source": "taxonomy",
            "message": f"'{species_query}' not found in taxonomy.",
            "suggestions": suggestions,
        })

    return json.dumps({
        "found": False,
        "message": (
            f"'{species_query}' not found in recent observations. "
            "Provide a region_code to also check the regional species list."
        ),
    })


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
    get_region_info,
    get_top100_contributors,
    get_species_list,
    get_region_stats,
    validate_species,
]
