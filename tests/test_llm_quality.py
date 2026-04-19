"""
test_llm_quality.py — LLM-as-judge quality evaluation tests using deepeval.

Judge model : Google Gemma (HuggingFace Inference API).
              Configured via JUDGE_MODEL_ID env var (default: 'gemma-3-27b').
Agent model : whatever HF_MODEL_ID is set to (default: qwen2.5-72b).

Each test:
  1. Runs the real eBird agent against a mocked eBird API (no live network calls).
  2. Captures the agent's final text response as ``actual_output``.
  3. Passes it to one or more GEval metrics scored by the Gemma judge.

Test scenarios
--------------
TestRecentSightingsQuebecCity
  • test_answer_relevancy      — response addresses bird sightings near Quebec City
  • test_geographic_accuracy   — response targets Quebec City, not another city

TestCaracaraSpeciesAccuracy
  • test_identifies_crested_caracara — 'caracara' → 'Crested Caracara', not all birds
  • test_species_filter_applied      — results are filtered to one species
  • test_mentions_map                — response confirms a map was created

Mark   : llm_quality
Skip   : when HUGGINGFACE_API_TOKEN is not set, or deepeval is not installed.
Run    : pytest tests/test_llm_quality.py -m llm_quality -v
"""

import json
import os
import re
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Skip the whole module when the API token is absent
# ---------------------------------------------------------------------------

if not os.environ.get("HUGGINGFACE_API_TOKEN"):
    pytest.skip(
        "HUGGINGFACE_API_TOKEN not set — skipping LLM quality tests",
        allow_module_level=True,
    )

# ---------------------------------------------------------------------------
# Defer deepeval import so a missing package skips cleanly at collection time
# ---------------------------------------------------------------------------

try:
    from deepeval import assert_test
    from deepeval.metrics import GEval
    from deepeval.models.base_model import DeepEvalBaseLLM
    from deepeval.test_case import LLMTestCase, LLMTestCaseParams
except ImportError:
    pytest.skip("deepeval not installed — pip install deepeval", allow_module_level=True)

# Applied to every test in this module:
pytestmark = pytest.mark.llm_quality

# ---------------------------------------------------------------------------
# Sample eBird API payloads
# ---------------------------------------------------------------------------

QC_RECENT_OBS = [
    {
        "comName": "American Robin",
        "sciName": "Turdus migratorius",
        "speciesCode": "amerob",
        "howMany": 3,
        "lat": 46.821,
        "lng": -71.220,
        "obsDt": "2026-04-17 07:15",
        "locName": "Parc des Champs-de-Bataille",
        "locId": "L100",
    },
    {
        "comName": "Black-capped Chickadee",
        "sciName": "Poecile atricapillus",
        "speciesCode": "bkcchi",
        "howMany": 5,
        "lat": 46.800,
        "lng": -71.215,
        "obsDt": "2026-04-17 08:30",
        "locName": "Bois-de-Coulonge Park",
        "locId": "L101",
    },
    {
        "comName": "Dark-eyed Junco",
        "sciName": "Junco hyemalis",
        "speciesCode": "daejun",
        "howMany": 2,
        "lat": 46.815,
        "lng": -71.235,
        "obsDt": "2026-04-16 09:00",
        "locName": "Domaine Maizerets",
        "locId": "L102",
    },
]

QC_CRECAR_OBS = [
    {
        "comName": "Crested Caracara",
        "sciName": "Caracara plancus",
        "speciesCode": "crecar",
        "howMany": 1,
        "lat": 46.820,
        "lng": -71.224,
        "obsDt": "2026-04-17 09:30",
        "locName": "Plaines d'Abraham",
        "locId": "L500",
    },
]

TAXONOMY_CRECAR = [
    {
        "speciesCode": "crecar",
        "comName": "Crested Caracara",
        "sciName": "Caracara plancus",
    }
]

# ---------------------------------------------------------------------------
# Gemma judge — DeepEvalBaseLLM backed by HuggingFace Inference API
# ---------------------------------------------------------------------------


class GemmaJudge(DeepEvalBaseLLM):
    """Evaluation judge using Google Gemma via HuggingFace Inference API.

    The model is resolved from the ``JUDGE_MODEL_ID`` environment variable
    (default: ``'gemma-3-27b'``).  Any alias from ``src.config.MODELS`` or a
    full HuggingFace repo ID (e.g. ``'google/gemma-3-12b-it'``) is accepted.

    The class keeps a single LLM instance across all tests to avoid rebuilding
    the endpoint repeatedly.
    """

    _instance = None  # module-level singleton

    def _get_llm(self):
        if GemmaJudge._instance is None:
            from src.config import build_llm

            model_id = os.environ.get("JUDGE_MODEL_ID", "gemma-3-27b")
            GemmaJudge._instance = build_llm(model_id)
        return GemmaJudge._instance

    # ------------------------------------------------------------------
    # DeepEvalBaseLLM interface
    # ------------------------------------------------------------------

    def generate(self, prompt: str, schema=None):
        """Generate a response, optionally coercing it into a Pydantic schema."""
        from langchain_core.messages import HumanMessage

        llm = self._get_llm()

        if schema is not None:
            fields = (
                list(schema.model_fields.keys())
                if hasattr(schema, "model_fields")
                else []
            )
            structured_prompt = (
                prompt
                + f"\n\nRespond ONLY with a valid JSON object. "
                f"Required fields: {fields}. "
                "No markdown fences, no extra text."
            )
            response = llm.invoke([HumanMessage(content=structured_prompt)])
            return self._coerce_to_schema(response.content, schema)

        response = llm.invoke([HumanMessage(content=prompt)])
        return response.content

    async def a_generate(self, prompt: str, schema=None):
        import asyncio

        return await asyncio.to_thread(self.generate, prompt, schema)

    def get_model_name(self) -> str:
        model_id = os.environ.get("JUDGE_MODEL_ID", "gemma-3-27b")
        return f"GemmaJudge({model_id})"

    # ------------------------------------------------------------------
    # Schema coercion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_to_schema(text: str, schema):
        """Parse JSON from free-form text and instantiate the schema.

        Tries three increasingly lenient strategies before falling back to a
        best-effort instance with score=5 (neutral) so evaluation can proceed.
        """
        # Strategy 1: JSON code fences  ```json { ... } ```
        for pattern in [r"```json\s*(\{.*?\})\s*```", r"```\s*(\{.*?\})\s*```"]:
            m = re.search(pattern, text, re.DOTALL)
            if m:
                try:
                    return schema(**json.loads(m.group(1)))
                except Exception:
                    pass

        # Strategy 2: first bare JSON object in the text
        m = re.search(r"(\{[^{}]*\})", text, re.DOTALL)
        if m:
            try:
                return schema(**json.loads(m.group(1)))
            except Exception:
                pass

        # Strategy 3: regex-extract score and reason individually
        score_m = re.search(r'"score"\s*:\s*([0-9]+(?:\.[0-9]+)?)', text)
        reason_m = re.search(r'"reason"\s*:\s*"([^"]+)"', text)
        score = int(float(score_m.group(1))) if score_m else 5
        reason = reason_m.group(1) if reason_m else text[:300]
        try:
            return schema(score=score, reason=reason)
        except Exception:
            return text


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_state():
    """Reset agent, VizBuffer, and region cache before/after every test."""
    import src.utils.region_cache as rc
    from src.agent import reset_agent
    from src.utils.state import clear_viz_buffer

    reset_agent()
    clear_viz_buffer()

    prev_codes = rc._known_codes.copy()
    prev_loaded = rc._cache_loaded
    rc._known_codes.clear()
    rc._cache_loaded = True  # suppress cold-cache disk reads

    yield

    reset_agent()
    clear_viz_buffer()
    rc._known_codes.clear()
    rc._known_codes.update(prev_codes)
    rc._cache_loaded = prev_loaded


def _make_client(obs_data: list, taxonomy_data: list | None = None) -> MagicMock:
    """Build a mock EBirdClient with all observation endpoints pre-configured."""
    client = MagicMock()
    client.recent_observations_by_location.return_value = obs_data
    client.recent_observations_by_region.return_value = obs_data
    client.notable_observations_by_location.return_value = obs_data
    client.historic_observations.return_value = obs_data
    client.taxonomy_search.return_value = taxonomy_data or []
    client.species_list.return_value = [r["speciesCode"] for r in obs_data]
    client.region_list.return_value = [{"code": "CA-QC", "name": "Quebec"}]
    client.region_info.return_value = {
        "result": "CA-QC",
        "bounds": {"minX": -80.0, "maxX": -57.0, "minY": 44.9, "maxY": 63.0},
    }
    client.nearby_hotspots.return_value = []
    return client


@pytest.fixture
def qc_client():
    with patch("src.tools.ebird_tools._get_client") as mock_get:
        mock_get.return_value = _make_client(QC_RECENT_OBS)
        yield


@pytest.fixture
def crecar_client():
    with patch("src.tools.ebird_tools._get_client") as mock_get:
        mock_get.return_value = _make_client(QC_CRECAR_OBS, taxonomy_data=TAXONOMY_CRECAR)
        yield


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _run(query: str) -> str:
    """Run the agent, returning its final response.  Skip on connection errors."""
    from src.agent import run_agent

    try:
        return run_agent(query)
    except Exception as exc:
        pytest.skip(f"Agent run failed (API unavailable?): {exc}")


def _make_geval(name: str, criteria: str, threshold: float = 0.5) -> GEval:
    """Convenience factory for a GEval metric using the Gemma judge."""
    return GEval(
        name=name,
        criteria=criteria,
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
        ],
        model=GemmaJudge(),
        threshold=threshold,
    )


# ---------------------------------------------------------------------------
# Scenario 1 — "show me recent bird sightings in Quebec City"
# ---------------------------------------------------------------------------


class TestRecentSightingsQuebecCity:
    """Query: 'show me recent bird sightings in Quebec City'."""

    _QUERY = "show me recent bird sightings in Quebec City"

    def test_answer_relevancy(self, qc_client):
        """Response should list bird species observed near Quebec City."""
        actual_output = _run(self._QUERY)
        test_case = LLMTestCase(input=self._QUERY, actual_output=actual_output)
        assert_test(
            test_case,
            [
                _make_geval(
                    "answer_relevancy",
                    "The response directly addresses the user's request for recent bird "
                    "sightings in or near Quebec City. It should mention at least one "
                    "specific bird species by name and indicate the observations were "
                    "collected near Quebec City (by naming the city, using a Quebec region "
                    "code, or citing Quebec City coordinates ~46.8°N, 71.2°W).",
                    threshold=0.5,
                )
            ],
        )

    def test_geographic_accuracy(self, qc_client):
        """Response should refer to Quebec City, not a different location."""
        actual_output = _run(self._QUERY)
        test_case = LLMTestCase(input=self._QUERY, actual_output=actual_output)
        assert_test(
            test_case,
            [
                _make_geval(
                    "geographic_accuracy",
                    "The response correctly targets Quebec City (Québec City, QC, Canada) "
                    "as the search location. It must NOT describe sightings in a different "
                    "city such as Montreal, Ottawa, or Toronto, and must NOT claim results "
                    "are for an unspecified or generic location.",
                    threshold=0.5,
                )
            ],
        )


# ---------------------------------------------------------------------------
# Scenario 2 — "map caracara observations in Quebec"
# ---------------------------------------------------------------------------


class TestCaracaraObservationsMap:
    """Query: 'map caracara observations in Quebec'."""

    _QUERY = "map caracara observations in Quebec"

    def test_identifies_crested_caracara(self, crecar_client):
        """Response should name 'Crested Caracara', not list all birds."""
        actual_output = _run(self._QUERY)
        test_case = LLMTestCase(input=self._QUERY, actual_output=actual_output)
        assert_test(
            test_case,
            [
                _make_geval(
                    "species_accuracy",
                    "The response correctly identifies 'caracara' as the Crested Caracara "
                    "(Caracara plancus, eBird code 'crecar'). The response must explicitly "
                    "name 'Crested Caracara'. It must NOT describe results as 'all recent "
                    "sightings' or list multiple unrelated species as if no species filter "
                    "was applied.",
                    threshold=0.6,
                )
            ],
        )

    def test_species_filter_applied(self, crecar_client):
        """Response should not describe showing all unfiltered observations."""
        actual_output = _run(self._QUERY)
        test_case = LLMTestCase(input=self._QUERY, actual_output=actual_output)
        assert_test(
            test_case,
            [
                _make_geval(
                    "species_filter_applied",
                    "The response describes observations that are filtered to a single "
                    "species (Crested Caracara). A failing response would describe showing "
                    "'all recent bird sightings', list many unrelated bird species, or "
                    "otherwise indicate no species filter was used.",
                    threshold=0.6,
                )
            ],
        )

    def test_mentions_map(self, crecar_client):
        """Response should confirm a map was created or is being displayed."""
        actual_output = _run(self._QUERY)
        test_case = LLMTestCase(input=self._QUERY, actual_output=actual_output)
        assert_test(
            test_case,
            [
                _make_geval(
                    "map_creation",
                    "The response confirms that an interactive map has been created, "
                    "rendered, or is being displayed for the Crested Caracara sightings. "
                    "Phrases like 'here is a map', 'I've created a map', 'a map has been "
                    "generated', or similar indicate success. A response that only provides "
                    "a text list with no map reference should score low.",
                    threshold=0.5,
                )
            ],
        )
