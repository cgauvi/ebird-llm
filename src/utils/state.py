"""
state.py — Shared in-process side-channel between LangChain viz tools and the
Streamlit UI.  Tools write here; app.py reads and renders the payload.

Structure of VizBuffer:
    {
        "type": "map" | "chart" | None,
        "data": <folium.Map HTML str>  |  <plotly fig dict>  |  None,
        "title": str | None,
    }
"""

VizBuffer: dict = {
    "type": None,
    "data": None,
    "title": None,
}


def clear_viz_buffer() -> None:
    """Reset VizBuffer so stale visuals are not re-rendered on the next turn."""
    VizBuffer["type"] = None
    VizBuffer["data"] = None
    VizBuffer["title"] = None
