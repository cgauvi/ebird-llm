"""
viz_tools.py — LangChain tools that build visualizations from eBird data.

Both tools write their output into src.utils.state.VizBuffer so that app.py can
render the result in the right-hand panel after the agent finishes its turn.
They return a short human-readable confirmation string to the agent.
"""

import json
from statistics import mean
from typing import Literal

import folium
import pandas as pd
import plotly.express as px
from langchain.tools import tool
from langchain_core.tools import ToolException

from src.utils.state import VizBuffer


# ---------------------------------------------------------------------------
# Helper — parse observations JSON safely
# ---------------------------------------------------------------------------


def _parse_obs(observations_json: str) -> list[dict]:
    try:
        data = json.loads(observations_json)
    except json.JSONDecodeError as exc:
        raise ToolException(
            "observations_json is not valid JSON. "
            "Pass the raw output from an eBird observation tool."
        ) from exc
    if not isinstance(data, list):
        raise ToolException("Expected a JSON array of observations.")
    return data


# ---------------------------------------------------------------------------
# Tool 7 — Interactive sightings map (folium)
# ---------------------------------------------------------------------------


@tool
def create_sightings_map(observations_json: str) -> str:
    """Build an interactive map that plots bird sighting locations.

    Each sighting is shown as a circle marker.  Clicking a marker reveals the
    species name, location name, count, and date.

    Call this tool immediately after retrieving observations when the user asks
    to *show*, *map*, or *visualise* sightings geographically.

    Args:
        observations_json: The raw JSON string returned by any eBird
            observation tool (recent, historic, notable, etc.).

    Returns:
        A short confirmation string, e.g. "Map created with 42 sightings."
        The map itself is rendered in the Streamlit right panel automatically.
    """
    records = _parse_obs(observations_json)

    # Filter records that have coordinates
    geo_records = [
        r for r in records if r.get("lat") is not None and r.get("lng") is not None
    ]
    if not geo_records:
        raise ToolException(
            "None of the observations include coordinate data — cannot build a map."
        )

    center_lat = mean(float(r["lat"]) for r in geo_records)
    center_lng = mean(float(r["lng"]) for r in geo_records)

    fmap = folium.Map(
        location=[center_lat, center_lng],
        zoom_start=10,
        tiles="CartoDB positron",
    )

    # Colour cycle for variety
    colours = [
        "#e74c3c", "#3498db", "#2ecc71", "#f39c12",
        "#9b59b6", "#1abc9c", "#e67e22", "#34495e",
    ]

    species_colour: dict[str, str] = {}
    colour_idx = 0

    for rec in geo_records:
        species = rec.get("comName", "Unknown species")
        sci = rec.get("sciName", "")
        count = rec.get("howMany", "X")
        loc = rec.get("locName", "")
        obs_dt = rec.get("obsDt", "")

        if species not in species_colour:
            species_colour[species] = colours[colour_idx % len(colours)]
            colour_idx += 1

        tooltip_html = (
            f"<b>{species}</b><br>"
            f"<i>{sci}</i><br>"
            f"Count: {count}<br>"
            f"Location: {loc}<br>"
            f"Date: {obs_dt}"
        )

        folium.CircleMarker(
            location=[float(rec["lat"]), float(rec["lng"])],
            radius=7,
            color=species_colour[species],
            fill=True,
            fill_opacity=0.75,
            tooltip=folium.Tooltip(tooltip_html),
        ).add_to(fmap)

    # Store HTML string in shared buffer
    VizBuffer["type"] = "map"
    VizBuffer["data"] = fmap._repr_html_()
    VizBuffer["title"] = "Bird Sightings Map"

    return f"Map created with {len(geo_records)} sightings."


# ---------------------------------------------------------------------------
# Tool 8 — Historical observations chart (plotly)
# ---------------------------------------------------------------------------


@tool
def create_historical_chart(
    observations_json: str,
    chart_type: Literal["bar", "line"] = "bar",
    top_n_species: int = 15,
) -> str:
    """Build a chart showing the number of observations per species.

    Use this tool when the user asks to *chart*, *plot*, or *visualise*
    observations over time or by species count.

    Args:
        observations_json: The raw JSON string returned by any eBird
            observation tool (historic, recent, notable, etc.).
        chart_type: 'bar' (default) for a bar chart ranked by count,
            or 'line' for a time-series line chart by date.
        top_n_species: Show only the top N most-observed species (default 15).
            Ignored for the 'line' chart type.

    Returns:
        A short confirmation string, e.g. "Chart created with 120 records."
        The chart is rendered in the Streamlit right panel automatically.
    """
    records = _parse_obs(observations_json)
    if not records:
        raise ToolException("The observations list is empty — nothing to chart.")

    df = pd.DataFrame(records)

    # Normalise count column — eBird returns "howMany" which may be None for 'X' reports
    if "howMany" not in df.columns:
        df["howMany"] = 1
    df["howMany"] = pd.to_numeric(df["howMany"], errors="coerce").fillna(1)

    if "comName" not in df.columns:
        raise ToolException("Observation records are missing the 'comName' field.")

    if chart_type == "bar":
        summary = (
            df.groupby("comName", as_index=False)["howMany"]
            .sum()
            .sort_values("howMany", ascending=False)
            .head(top_n_species)
        )
        fig = px.bar(
            summary,
            x="comName",
            y="howMany",
            title=f"Top {top_n_species} Species by Observation Count",
            labels={"comName": "Species", "howMany": "Count"},
            color="comName",
            color_discrete_sequence=px.colors.qualitative.Set2,
        )
        fig.update_layout(
            xaxis_tickangle=-40,
            showlegend=False,
            margin={"t": 50, "b": 120},
        )

    else:  # line
        if "obsDt" not in df.columns:
            raise ToolException(
                "Observation records are missing the 'obsDt' field required "
                "for a time-series chart. Try chart_type='bar' instead."
            )
        df["date"] = pd.to_datetime(df["obsDt"]).dt.date
        time_summary = (
            df.groupby(["date", "comName"], as_index=False)["howMany"].sum()
        )
        top_species = (
            df.groupby("comName")["howMany"]
            .sum()
            .nlargest(top_n_species)
            .index.tolist()
        )
        time_summary = time_summary[time_summary["comName"].isin(top_species)]
        fig = px.line(
            time_summary,
            x="date",
            y="howMany",
            color="comName",
            title=f"Observations Over Time (top {top_n_species} species)",
            labels={"date": "Date", "howMany": "Count", "comName": "Species"},
            markers=True,
        )
        fig.update_layout(margin={"t": 50, "b": 60})

    VizBuffer["type"] = "chart"
    VizBuffer["data"] = fig.to_dict()
    VizBuffer["title"] = fig.layout.title.text or "Observations Chart"

    return f"Chart created with {len(records)} records."


# ---------------------------------------------------------------------------
# Public list used by agent.py
# ---------------------------------------------------------------------------

VIZ_TOOLS = [
    create_sightings_map,
    create_historical_chart,
]
