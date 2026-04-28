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

from src.utils.state import (
    VizBuffer,
    get_last_observations,
    get_last_obs_file,
    get_obs_dataframe,
    get_obs_history,
)


# ---------------------------------------------------------------------------
# Helpers — parse observations JSON or fall back to session cache
# ---------------------------------------------------------------------------


def parse_observations_json(observations_json: str) -> list[dict]:
    """Parse and validate an observations JSON string.

    Handles common LLM output artefacts:
    - Surrounding whitespace
    - Backslash-escaped quotes (\\\" instead of ")
    - JSON string wrapped in an extra outer pair of quotes
    """
    text = observations_json.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # LLM artefact: every " escaped as \"
        try:
            parsed = json.loads(text.replace('\\"', '"'))
        except json.JSONDecodeError as exc:
            raise ToolException(
                f"Observations JSON is not valid JSON: {exc}"
            ) from exc

    # LLM artefact: whole JSON wrapped in an extra pair of outer quotes
    if isinstance(parsed, str):
        inner = parsed
        try:
            parsed = json.loads(inner)
        except json.JSONDecodeError:
            try:
                parsed = json.loads(inner.replace('\\"', '"'))
            except json.JSONDecodeError as exc:
                raise ToolException(
                    f"Observations JSON is not valid JSON: {exc}"
                ) from exc

    if not isinstance(parsed, list):
        raise ToolException("Expected a JSON array of observations.")
    return parsed


def _load_from_cache() -> list[dict]:
    """Return observations from the in-process session cache."""
    df = get_obs_dataframe()
    if df is not None and not df.empty:
        return df.to_dict(orient="records")

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
        "then pass the returned JSON as observations_json."
    )


# ---------------------------------------------------------------------------
# Tool 7 — Interactive sightings map (folium)
# ---------------------------------------------------------------------------


@tool
def create_sightings_map(observations_json: str = "") -> str:
    """Build an interactive map that plots bird sighting locations.

    Each sighting is shown as a circle marker.  Clicking a marker reveals the
    species name, location name, count, and date.

    Call this tool immediately after retrieving observations when the user asks
    to *show*, *map*, or *visualise* sightings geographically.

    Args:
        observations_json: JSON array of observation records returned by an
            eBird data tool.  Leave empty to use the session cache automatically.

    Returns:
        A short confirmation string, e.g. "Map created with 42 sightings."
        The map itself is rendered in the Streamlit right panel automatically.
    """
    records = parse_observations_json(observations_json) if observations_json.strip() else _load_from_cache()

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
        .head(10)
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
    observations_json: str = "",
    chart_type: Literal["bar", "line", "scatter", "heatmap", "facet_bar", "box"] = "bar",
    top_n_species: int = 15,
    compare_regions: bool = False,
) -> str:
    """Build a chart showing the number of observations per species.

    Use this tool when the user asks to *chart*, *plot*, or *visualise*
    observations over time or by species count.

    Args:
        observations_json: JSON array of observation records returned by an
            eBird data tool.  Leave empty to use the session cache automatically.
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
        compare_regions: When True (only effective for 'line' and 'bar'), pull
            observations from every region fetched in this session and overlay
            them on a single figure. Species are distinguished by colour and
            regions by line-dash ('line') or pattern ('bar'), each with its own
            legend. Use this when the user asks to compare regions or sites.

    Returns:
        A short confirmation string, e.g. "Chart created with 120 records."
        The chart is rendered in the Streamlit right panel automatically.
    """
    multi_region = False
    if compare_regions and chart_type in ("line", "bar") and not observations_json.strip():
        history = get_obs_history()
        region_labels = [e["region"] for e in history if e.get("records")]
        if len(region_labels) >= 2:
            tagged: list[dict] = []
            for entry in history:
                for rec in entry["records"]:
                    rec = dict(rec)
                    rec["_region"] = entry["region"]
                    tagged.append(rec)
            records = tagged
            multi_region = True
        else:
            records = parse_observations_json(observations_json) if observations_json.strip() else _load_from_cache()
    else:
        records = parse_observations_json(observations_json) if observations_json.strip() else _load_from_cache()
    if not records:
        raise ToolException("The observations list is empty — nothing to chart.")

    df = pd.DataFrame(records)

    # Normalise count column — eBird returns "howMany" which may be None for 'X' reports
    if "howMany" not in df.columns:
        df["howMany"] = 1
    df["howMany"] = pd.to_numeric(df["howMany"], errors="coerce").fillna(1)

    if "comName" not in df.columns:
        raise ToolException("Observation records are missing the 'comName' field.")

    n_unique = df["comName"].nunique()
    sole_species = df["comName"].iloc[0] if n_unique == 1 else None

    def _species_qualifier(base_multi: str, base_single: str | None = None) -> str:
        """Return a title string adjusted for how many species are in the data."""
        if sole_species:
            return base_single or f"{sole_species} — {base_multi}"
        if n_unique <= top_n_species:
            return base_multi.replace(f" (top {top_n_species} species)", "").replace(
                f"Top {top_n_species} Species by ", "Species by "
            ).replace(f"top {top_n_species} species, ", "")
        return base_multi

    if chart_type == "bar":
        if multi_region and "obsDt" in df.columns:
            df["date"] = pd.to_datetime(df["obsDt"], format="mixed").dt.normalize()
            top_species = (
                df.groupby("comName")["howMany"].sum()
                .nlargest(top_n_species).index.tolist()
            )
            bar_df = df[df["comName"].isin(top_species)]
            summary = bar_df.groupby(
                ["date", "comName", "_region"], as_index=False
            )["howMany"].sum()
            n_regions = summary["_region"].nunique()
            fig = px.bar(
                summary,
                x="date",
                y="howMany",
                color="comName",
                pattern_shape="_region",
                barmode="group",
                title=(
                    f"Observations Over Time — Comparing {n_regions} Regions "
                    f"(top {top_n_species} species)"
                ),
                labels={
                    "date": "Date",
                    "howMany": "Count",
                    "comName": "Species",
                    "_region": "Region",
                },
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
            fig.update_layout(
                margin={"t": 50, "b": 80},
                legend={"title": "Species / Region"},
            )
        else:
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
                title=_species_qualifier(
                    f"Top {top_n_species} Species by Observation Count",
                    f"{sole_species} — Observation Count",
                ),
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
        df["date"] = pd.to_datetime(df["obsDt"], format="mixed").dt.normalize()
        group_cols = ["date", "comName"] + (["_region"] if multi_region else [])
        time_summary = (
            df.groupby(group_cols, as_index=False)["howMany"].sum()
        )
        top_species = (
            df.groupby("comName")["howMany"]
            .sum()
            .nlargest(top_n_species)
            .index.tolist()
        )
        time_summary = time_summary[time_summary["comName"].isin(top_species)]
        line_kwargs = dict(
            x="date",
            y="howMany",
            color="comName",
            labels={
                "date": "Date",
                "howMany": "Count",
                "comName": "Species",
                "_region": "Region",
            },
            markers=True,
        )
        if multi_region:
            n_regions = time_summary["_region"].nunique()
            line_kwargs["line_dash"] = "_region"
            line_kwargs["title"] = (
                f"Observations Over Time — Comparing {n_regions} Regions "
                f"(top {top_n_species} species)"
            )
        else:
            line_kwargs["title"] = _species_qualifier(
                f"Observations Over Time (top {top_n_species} species)",
                f"{sole_species} — Observations Over Time",
            )
        fig = px.line(time_summary, **line_kwargs)
        fig.update_layout(
            margin={"t": 50, "b": 60},
            legend={"title": "Species / Region" if multi_region else "Species"},
        )

    elif chart_type == "scatter":
        if "obsDt" not in df.columns:
            raise ToolException(
                "Observation records are missing the 'obsDt' field required "
                "for a scatter/regression chart. Try chart_type='bar' instead."
            )
        df["date"] = pd.to_datetime(df["obsDt"], format="mixed").dt.normalize()
        top_species = (
            df.groupby("comName")["howMany"]
            .sum()
            .nlargest(top_n_species)
            .index.tolist()
        )
        scatter_df = df[df["comName"].isin(top_species)].copy()
        fig = px.scatter(
            scatter_df,
            x="date",
            y="howMany",
            color="comName",
            trendline="ols",
            title=_species_qualifier(
                f"Observations with OLS Regression (top {top_n_species} species)",
                f"{sole_species} — Observations with OLS Regression",
            ),
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
            title=_species_qualifier(
                f"Observation Heatmap (top {top_n_species} species)",
                f"{sole_species} — Observation Heatmap",
            ),
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
            title=_species_qualifier(
                f"Species Counts by Location (top {top_n_species} species, top 6 locations)",
                f"{sole_species} — Counts by Location",
            ),
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
            title=_species_qualifier(
                f"Count Distribution per Species (top {top_n_species})",
                f"{sole_species} — Count Distribution",
            ),
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
# Tool 9 — Observations data table
# ---------------------------------------------------------------------------

# Human-friendly column labels shown in the viz panel
_OBS_COLUMN_LABELS: dict[str, str] = {
    "comName":          "Species",
    "sciName":          "Scientific Name",
    "howMany":          "Count",
    "obsDt":            "Date",
    "locName":          "Location",
    "lat":              "Lat",
    "lng":              "Lng",
    "obsValid":         "Valid",
    "obsReviewed":      "Reviewed",
    "locationPrivate":  "Private",
    "speciesCode":      "Code",
    "locId":            "Loc ID",
    "subId":            "Checklist ID",
}

# Preferred column order (columns not listed appear after these)
_OBS_COLUMN_ORDER = [
    "comName", "sciName", "howMany", "obsDt", "locName",
    "lat", "lng", "obsValid", "obsReviewed", "locationPrivate",
    "speciesCode", "locId", "subId",
]


@tool
def show_observations_table(observations_json: str = "") -> str:
    """Display the most recent observation data as an interactive table in the viz panel.

    Call this tool whenever the user asks to *see*, *show*, *display*, *print*,
    or *view* the raw observation data, a table, or a dataframe.  It renders all
    retrieved records as a sortable, searchable dataframe in the right-hand panel.

    Args:
        observations_json: JSON array of observation records returned by an
            eBird data tool.  Leave empty to use the session cache automatically.

    Returns:
        A short confirmation string, e.g. "Table displayed with 33 observations."
        The table is rendered in the Streamlit right panel automatically.
    """
    records = parse_observations_json(observations_json) if observations_json.strip() else _load_from_cache()
    if not records:
        raise ToolException("No observation data available — run an eBird data tool first.")

    df = pd.DataFrame(records)

    # Normalise count column
    if "howMany" in df.columns:
        df["howMany"] = pd.to_numeric(df["howMany"], errors="coerce").fillna(1).astype(int)

    # Re-order columns: preferred order first, then any extras
    ordered = [c for c in _OBS_COLUMN_ORDER if c in df.columns]
    extras  = [c for c in df.columns if c not in ordered]
    df = df[ordered + extras]

    # Rename to human-friendly labels
    df = df.rename(columns=_OBS_COLUMN_LABELS)

    VizBuffer["type"]  = "dataframe"
    VizBuffer["data"]  = df.to_dict(orient="records")
    VizBuffer["title"] = f"Observations ({len(records)} records)"
    VizBuffer["table"] = None  # not used for this type

    return f"Table displayed with {len(records)} observations."


# ---------------------------------------------------------------------------
# Public list used by agent.py
# ---------------------------------------------------------------------------

VIZ_TOOLS = [
    create_sightings_map,
    create_historical_chart,
    show_observations_table,
]

# %%
