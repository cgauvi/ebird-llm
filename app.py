"""
app.py — Streamlit front-end for the eBird LangChain agent.

Layout
------
┌──────────────────────────────────────────────────────────────────────────┐
│  Sidebar: app info, API status, New Conversation button                  │
├──────────────────────────────────────────────────────────────────────────┤
│  Left column (58%)        │  Right column (42%)                          │
│  Chat history             │  Latest visualization (map or chart)         │
│  Chat input               │  (placeholder until first viz is produced)   │
└──────────────────────────────────────────────────────────────────────────┘
"""

import importlib
import os

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="eBird Birding Assistant",
    page_icon="🐦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------

if "messages" not in st.session_state:
    st.session_state.messages = []  # list[dict(role, content)]

if "viz_snapshot" not in st.session_state:
    # Snapshot of VizBuffer captured after each agent turn so the right panel
    # persists across re-runs triggered by subsequent chat inputs.
    st.session_state.viz_snapshot = {"type": None, "data": None, "title": None}

if "selected_model" not in st.session_state:
    # Initialise from env var (set in .env or by Terraform); fall back to default
    from src.config import DEFAULT_MODEL_ALIAS
    st.session_state.selected_model = os.environ.get("HF_MODEL_ID", DEFAULT_MODEL_ALIAS)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("🐦 eBird Assistant")
    st.caption("Powered by LangChain · HuggingFace · eBird API v2")

    st.divider()

    # Environment status
    st.subheader("Configuration")
    ebird_ok = bool(os.environ.get("EBIRD_API_KEY"))
    st.write(f"eBird API key: {'✅ set' if ebird_ok else '❌ missing'}")

    hf_token_ok = bool(os.environ.get("HUGGINGFACE_API_TOKEN"))
    from src.config import MODELS, DEFAULT_MODEL_ALIAS, resolve_model
    raw_model = os.environ.get("HF_MODEL_ID", DEFAULT_MODEL_ALIAS)
    repo_id, model_cfg = resolve_model(raw_model)
    model_label = f"{raw_model} → `{repo_id}`" if model_cfg else f"`{repo_id}`"
    st.write(f"HuggingFace token: {'✅ set' if hf_token_ok else '❌ missing'}")
    st.write(f"Model: {model_label}")
    if model_cfg and model_cfg.notes:
        st.caption(f"ℹ️ {model_cfg.notes}")

    llm_ok = hf_token_ok

    st.divider()

    if st.button("🔄 New Conversation", use_container_width=True):
        st.session_state.messages = []
        st.session_state.viz_snapshot = {"type": None, "data": None, "title": None}
        # Clear agent memory without re-importing to avoid re-building the LLM
        try:
            import src.agent as _agent_mod
            _agent_mod.reset_agent()
        except Exception:
            pass
        st.rerun()

    st.divider()
    st.markdown(
        """
        **Example queries**
        - *Show recent bird sightings near lat 48.85, lng 2.35*
        - *Map notable birds near lat 51.5, lng -0.12 in the last 14 days*
        - *Historic observations for US-NY on 2024-05-01, then chart them*
        - *List the states in the US*
        - *Find hotspots within 10 km of lat 40.71, lng -74.01*
        """
    )

# ---------------------------------------------------------------------------
# Main layout — two columns
# ---------------------------------------------------------------------------

chat_col, viz_col = st.columns([0.58, 0.42], gap="medium")

# ---------------------------------------------------------------------------
# Right column — visualization panel (rendered first so it stays persistent)
# ---------------------------------------------------------------------------

with viz_col:
    st.subheader("Visualization")

    snap = st.session_state.viz_snapshot
    if snap["type"] == "map":
        st.caption(snap.get("title", "Map"))
        try:
            from streamlit_folium import st_folium
            import folium
            # Reconstruct folium map from stored HTML
            fmap_html = snap["data"]
            # Render via components.html for full interactivity
            st.components.v1.html(fmap_html, height=500, scrolling=False)
        except Exception as exc:
            st.error(f"Could not render map: {exc}")

    elif snap["type"] == "chart":
        st.caption(snap.get("title", "Chart"))
        try:
            import plotly.graph_objects as go
            fig = go.Figure(snap["data"])
            st.plotly_chart(fig, use_container_width=True)
        except Exception as exc:
            st.error(f"Could not render chart: {exc}")

    else:
        st.info(
            "Ask the assistant about bird sightings and a map or chart will appear here."
        )

# ---------------------------------------------------------------------------
# Left column — chat panel
# ---------------------------------------------------------------------------

with chat_col:
    st.subheader("Chat")

    # Render conversation history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Chat input
    user_input = st.chat_input(
        "Ask about birds, regions, sightings, hotspots…",
        disabled=not (ebird_ok and llm_ok),
    )

    if not (ebird_ok and llm_ok):
        st.warning(
            "One or more API keys are missing. "
            "Copy `.env.example` to `.env` and fill in your keys, then restart the app.",
            icon="⚠️",
        )

    if user_input:
        # Append user message
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        # Run agent
        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                try:
                    # Lazy import so startup doesn't build the LLM before keys are set
                    import src.agent as _agent_mod
                    # Clear VizBuffer before the call
                    import src.utils.state as _state
                    _state.clear_viz_buffer()

                    response = _agent_mod.run_agent(user_input)

                    # Snapshot the VizBuffer produced during this agent turn
                    st.session_state.viz_snapshot = {
                        "type": _state.VizBuffer["type"],
                        "data": _state.VizBuffer["data"],
                        "title": _state.VizBuffer["title"],
                    }
                except Exception as exc:
                    response = f"⚠️ An error occurred: {exc}"

            st.markdown(response)

        st.session_state.messages.append({"role": "assistant", "content": response})

        # Force re-run so the viz column updates with the new snapshot
        st.rerun()
