"""
region_cache.py — Persistent disk cache of known valid eBird region codes.

The cache file lives at ~/.cache/ebird_llm/regions.json and is built
incrementally as get_region_list() results come back from the API.  It is
consulted before every region-based API call to catch hallucinated or
malformed codes early and give the LLM actionable guidance.

Two-tier validation
-------------------
1. Format check (always applied): must match the eBird code pattern —
   uppercase alpha/numeric segments separated by hyphens.
2. Cache check (applied only when the cache is warm): the code must have been
   seen in a previous get_region_list response.  A cold cache skips this
   check so a first run is never blocked.
"""

import json
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

_CACHE_DIR = Path.home() / ".cache" / "ebird_llm"
_CACHE_FILE = _CACHE_DIR / "regions.json"

# All codes accumulated from get_region_list responses (in-memory + on-disk).
_known_codes: set[str] = set()
_cache_loaded: bool = False

# Special codes accepted unconditionally (never returned by region_list).
_ALWAYS_VALID = {"world"}


# ---------------------------------------------------------------------------
# Format regex
# ---------------------------------------------------------------------------
# Valid patterns:
#   'world'          — global pseudo-region
#   'US'             — 2-letter country ISO
#   'US-NY'          — subnational1 (state / province)
#   'US-NY-001'      — subnational2 numeric county  (US style)
#   'CA-QC-ABI'      — subnational2 alpha county    (Canada style)
# All alpha characters MUST be uppercase; lowercase (e.g. 'CA-QC-060r') is invalid.
_REGION_RE = re.compile(
    r"^world$"
    r"|^[A-Z]{2}$"
    r"|^[A-Z]{2}-[A-Z0-9]{1,5}$"
    r"|^[A-Z]{2}-[A-Z0-9]{1,5}-[A-Z0-9]{1,7}$"
)


# ---------------------------------------------------------------------------
# Cache I/O
# ---------------------------------------------------------------------------

def _load() -> None:
    global _cache_loaded
    if _cache_loaded:
        return
    _cache_loaded = True
    if _CACHE_FILE.exists():
        try:
            data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            _known_codes.update(data.get("codes", []))
        except Exception:
            pass  # corrupt file — start fresh


def _save() -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(
            json.dumps({"codes": sorted(_known_codes)}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass  # non-fatal — cache is best-effort


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register_codes(codes: list[str]) -> None:
    """Store a batch of valid region codes returned by a get_region_list call."""
    _load()
    _known_codes.update(codes)
    _save()


def validate_region_code(code: str) -> str | None:
    """Return an error string if *code* looks invalid, or ``None`` if it is OK.

    Args:
        code: The region code to check, already stripped and upper-cased.

    Returns:
        ``None`` if the code passes all checks.
        A human-readable error message (suitable for a ToolException) otherwise.
    """
    _load()

    # --- Tier 1: format ---
    if not _REGION_RE.match(code):
        # Give a specific hint for the common lowercase mistake.
        if code != code.upper():
            return (
                f"'{code}' contains lowercase letters. eBird region codes are "
                f"fully uppercase (e.g. '{code.upper()}'). Please correct the "
                "code and retry."
            )
        return (
            f"'{code}' is not a valid eBird region code format. "
            "Valid examples: 'US' (country), 'US-NY' (state/province), "
            "'US-NY-001' or 'CA-QC-ABI' (county/district). "
            "Use get_region_list to discover valid codes."
        )

    if code in _ALWAYS_VALID:
        return None

    # --- Tier 2: cache (skip when cache is cold) ---
    if _known_codes and code not in _known_codes:
        parts = code.split("-")
        if len(parts) == 1:
            # Bare country codes (e.g. 'US') are not returned by get_region_list
            # and will never be registered in the cache; format check is sufficient.
            return None
        if len(parts) == 3:
            parent = "-".join(parts[:2])
            hint = (
                f"Call get_region_list(region_type='subnational2', "
                f"parent_region_code='{parent}') to list valid codes for that area."
            )
        elif len(parts) == 2:
            parent = parts[0]
            hint = (
                f"Call get_region_list(region_type='subnational1', "
                f"parent_region_code='{parent}') to list valid state/province codes."
            )
        else:
            hint = (
                "Call get_region_list(region_type='country', "
                "parent_region_code='world') to list valid country codes."
            )
        return (
            f"'{code}' was not found among known eBird region codes. "
            f"{hint}"
        )

    return None
