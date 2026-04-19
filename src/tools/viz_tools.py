"""
viz_tools.py — LangChain tools that build visualizations from eBird data.

Both tools write their output into src.utils.state.VizBuffer so that app.py can
render the result in the right-hand panel after the agent finishes its turn.
They return a short human-readable confirmation string to the agent.
"""
# %%
import json
from pathlib import Path
from statistics import mean
from typing import Literal

import folium
from folium.plugins import MarkerCluster
import pandas as pd
import plotly.express as px
from langchain.tools import tool
from langchain_core.tools import ToolException

from src.utils.state import VizBuffer, get_last_observations, get_last_obs_file, get_obs_dataframe


# ---------------------------------------------------------------------------
# Helper — load observations from a file path or session cache
# ---------------------------------------------------------------------------


def _load_obs(observations_file: str) -> list[dict]:
    """Return observation records from *observations_file* or the session cache.

    Priority:
    1. If *observations_file* is provided, read and parse that JSON file.
    2. Fall back to the in-process DataFrame cache (fastest, zero-copy).
    3. Fall back to the raw JSON string cache kept by ``set_last_observations``.
    """
    if observations_file and observations_file.strip():
        path = Path(observations_file.strip())
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ToolException(
                    f"Failed to parse observations file {path}: {exc}"
                ) from exc
            if isinstance(data, dict) and "observations" in data:
                data = data["observations"]
            if not isinstance(data, list):
                raise ToolException("Expected a JSON array of observations in the file.")
            return data
        # File not found — fall through to session cache below

    # Fall back to session DataFrame cache
    df = get_obs_dataframe()
    if df is not None and not df.empty:
        return df.to_dict(orient="records")

    # Fall back to last known observations file written this session
    last_file = get_last_obs_file()
    if last_file:
        path = Path(last_file)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "observations" in data:
                    data = data["observations"]
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass

    # Fall back to raw JSON string cache
    cached = get_last_observations()
    if cached:
        try:
            data = json.loads(cached)
            if isinstance(data, dict) and "observations" in data:
                data = data["observations"]
            if isinstance(data, list) and all(isinstance(r, dict) for r in data):
                return data
        except json.JSONDecodeError:
            pass

    raise ToolException(
        "No observation data available. Run an eBird data tool first, "
        "then pass the returned file path as observations_file."
    )


# ---------------------------------------------------------------------------
# Tool 7 — Interactive sightings map (folium)
# ---------------------------------------------------------------------------


@tool
def create_sightings_map(observations_file: str = "") -> str:
    """Build an interactive map that plots bird sighting locations.

    Each sighting is shown as a circle marker.  Clicking a marker reveals the
    species name, location name, count, and date.

    Call this tool immediately after retrieving observations when the user asks
    to *show*, *map*, or *visualise* sightings geographically.

    Args:
        observations_file: Path to the JSON file returned by an eBird data tool
            (included in the tool's output as "JSON file: /path/to/file.json").
            Leave empty to use the session cache automatically.

    Returns:
        A short confirmation string, e.g. "Map created with 42 sightings."
        The map itself is rendered in the Streamlit right panel automatically.
    """
    records = _load_obs(observations_file)

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

    # Cluster overlapping / nearby markers so multiple observations at the
    # same location don't pile up as a single invisible point.
    cluster = MarkerCluster(name="Sightings").add_to(fmap)

    # Folium icon colours (subset that renders reliably)
    ICON_COLOURS = [
        "red", "blue", "green", "purple", "orange",
        "darkred", "darkblue", "darkgreen", "cadetblue", "darkpurple",
        "pink", "lightblue", "lightgreen", "gray", "beige",
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
            species_colour[species] = ICON_COLOURS[colour_idx % len(ICON_COLOURS)]
            colour_idx += 1

        lat = float(rec["lat"])
        lng = float(rec["lng"])
        popup_html = (
            f"<b>{species}</b><br>"
            f"<i>{sci}</i><br>"
            f"Count: {count}<br>"
            f"Location: {loc}<br>"
            f"Date: {obs_dt}<br>"
            f"<small>📍 {lat:.5f}°N, {abs(lng):.5f}°{'W' if lng < 0 else 'E'}</small>"
        )

        folium.Marker(
            location=[lat, lng],
            popup=folium.Popup(popup_html, max_width=280),
            tooltip=f"{species} (n={count})",
            icon=folium.Icon(color=species_colour[species]),
        ).add_to(cluster)

    # ------------------------------------------------------------------
    # Table: every observation that has a marker on the map, so the
    # dataframe and map always reflect the same set of records.
    # ------------------------------------------------------------------
    df_geo = pd.DataFrame(geo_records)
    if "howMany" in df_geo.columns:
        df_geo["howMany"] = pd.to_numeric(df_geo["howMany"], errors="coerce").fillna(1).astype(int)
    else:
        df_geo["howMany"] = 1

    display_cols = {"comName": "Species", "howMany": "Count",
                    "locName": "Location", "obsDt": "Date"}
    available = {k: v for k, v in display_cols.items() if k in df_geo.columns}
    df_top = (
        df_geo[list(available.keys())]
        .rename(columns=available)
        .sort_values("Count", ascending=False)
        .reset_index(drop=True)
    )

    # Store Map object (not HTML) so app.py can render it with st_folium,
    # which correctly sizes and places the map in the Streamlit panel.
    VizBuffer["type"] = "map"
    VizBuffer["data"] = fmap          # folium.Map object
    VizBuffer["title"] = "Bird Sightings Map"
    VizBuffer["table"] = df_top.to_dict(orient="records")

    return f"Map created with {len(geo_records)} sightings."


# ---------------------------------------------------------------------------
# Tool 8 — Historical observations chart (plotly)
# ---------------------------------------------------------------------------


@tool
def create_historical_chart(
    observations_file: str = "",
    chart_type: Literal["bar", "line", "scatter", "heatmap", "facet_bar", "box"] = "bar",
    top_n_species: int = 15,
) -> str:
    """Build a chart showing the number of observations per species.

    Use this tool when the user asks to *chart*, *plot*, or *visualise*
    observations over time or by species count.

    Args:
        observations_file: Path to the JSON file returned by an eBird data tool
            (included in the tool's output as "JSON file: /path/to/file.json").
            Leave empty to use the session cache automatically.
        chart_type: Chart style to render.
            - 'bar'       (default) — horizontal bar chart ranked by total count.
            - 'line'      — time-series line chart per species (requires 'obsDt').
            - 'scatter'   — scatter plot of count over time with OLS regression
                            lines per species (requires 'obsDt').
            - 'heatmap'   — species × date heatmap showing observation intensity
                            (requires 'obsDt').
            - 'facet_bar' — bar charts faceted by location (up to 6 locations,
                            requires 'locName').
            - 'box'       — box plot showing the count distribution per species.
        top_n_species: Show only the top N most-observed species (default 15).

    Returns:
        A short confirmation string, e.g. "Chart created with 120 records."
        The chart is rendered in the Streamlit right panel automatically.
    """
    records = _load_obs(observations_file)
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

    elif chart_type == "line":
        if "obsDt" not in df.columns:
            raise ToolException(
                "Observation records are missing the 'obsDt' field required "
                "for a time-series chart. Try chart_type='bar' instead."
            )
        df["date"] = pd.to_datetime(df["obsDt"], format="mixed").dt.date
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

    elif chart_type == "scatter":
        if "obsDt" not in df.columns:
            raise ToolException(
                "Observation records are missing the 'obsDt' field required "
                "for a scatter/regression chart. Try chart_type='bar' instead."
            )
        df["date"] = pd.to_datetime(df["obsDt"], format="mixed").dt.date
        top_species = (
            df.groupby("comName")["howMany"]
            .sum()
            .nlargest(top_n_species)
            .index.tolist()
        )
        scatter_df = df[df["comName"].isin(top_species)].copy()
        scatter_df["date"] = pd.to_datetime(scatter_df["date"])
        fig = px.scatter(
            scatter_df,
            x="date",
            y="howMany",
            color="comName",
            trendline="ols",
            title=f"Observations with OLS Regression (top {top_n_species} species)",
            labels={"date": "Date", "howMany": "Count", "comName": "Species"},
        )
        fig.update_layout(margin={"t": 50, "b": 60})

    elif chart_type == "heatmap":
        if "obsDt" not in df.columns:
            raise ToolException(
                "Observation records are missing the 'obsDt' field required "
                "for a heatmap. Try chart_type='bar' instead."
            )
        df["date"] = pd.to_datetime(df["obsDt"], format="mixed").dt.date.astype(str)
        top_species = (
            df.groupby("comName")["howMany"]
            .sum()
            .nlargest(top_n_species)
            .index.tolist()
        )
        heat_df = df[df["comName"].isin(top_species)]
        pivot = (
            heat_df.groupby(["comName", "date"])["howMany"]
            .sum()
            .unstack(fill_value=0)
        )
        import plotly.graph_objects as go
        fig = go.Figure(data=go.Heatmap(
            z=pivot.values,
            x=pivot.columns.tolist(),
            y=pivot.index.tolist(),
            colorscale="YlOrRd",
            hoverongaps=False,
        ))
        fig.update_layout(
            title=f"Observation Heatmap (top {top_n_species} species)",
            xaxis_title="Date",
            yaxis_title="Species",
            margin={"t": 50, "b": 80},
        )

    elif chart_type == "facet_bar":
        if "locName" not in df.columns:
            raise ToolException(
                "Observation records are missing 'locName' required for a "
                "facet chart. Try chart_type='bar' instead."
            )
        top_species = (
            df.groupby("comName")["howMany"]
            .sum()
            .nlargest(top_n_species)
            .index.tolist()
        )
        facet_df = df[df["comName"].isin(top_species)]
        top_locs = (
            facet_df.groupby("locName")["howMany"]
            .sum()
            .nlargest(6)
            .index.tolist()
        )
        facet_df = facet_df[facet_df["locName"].isin(top_locs)]
        summary = facet_df.groupby(["locName", "comName"], as_index=False)["howMany"].sum()
        fig = px.bar(
            summary,
            x="comName",
            y="howMany",
            facet_col="locName",
            facet_col_wrap=2,
            title=f"Species Counts by Location (top {top_n_species} species, top 6 locations)",
            labels={"comName": "Species", "howMany": "Count", "locName": "Location"},
            color="comName",
            color_discrete_sequence=px.colors.qualitative.Set2,
        )
        fig.update_layout(
            showlegend=False,
            margin={"t": 60, "b": 120},
            height=600,
        )
        fig.for_each_xaxis(lambda ax: ax.update(tickangle=-40))
        fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))

    elif chart_type == "box":
        top_species = (
            df.groupby("comName")["howMany"]
            .sum()
            .nlargest(top_n_species)
            .index.tolist()
        )
        box_df = df[df["comName"].isin(top_species)]
        fig = px.box(
            box_df,
            x="comName",
            y="howMany",
            title=f"Count Distribution per Species (top {top_n_species})",
            labels={"comName": "Species", "howMany": "Count"},
            color="comName",
            color_discrete_sequence=px.colors.qualitative.Set2,
        )
        fig.update_layout(
            xaxis_tickangle=-40,
            showlegend=False,
            margin={"t": 50, "b": 120},
        )

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

# %%
