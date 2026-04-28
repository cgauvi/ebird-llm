# app.py — Streamlit front-end with immediate generic “Processing...”
# plus live flywheel-style tool status updates using st.status()

import os
import uuid
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from src.utils.logging_config import (
    setup_logging,
    LogBuffer,
    clear_log_buffer,
)

setup_logging()

IS_DEV = os.getenv("APP_ENV", "dev").lower() == "dev"

st.set_page_config(
    page_title="eBird Birding Assistant",
    page_icon="🐦",
    layout="wide",
    initial_sidebar_state="expanded",
)

MAX_TURNS = 20

# -----------------------------------------------------------------------------
# Sidebar
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Log pane helpers
# -----------------------------------------------------------------------------

_LOG_LEVEL_COLOURS = {
    "DEBUG": "#888888",
    "INFO": "#0277bd",
    "WARNING": "#e65100",
    "ERROR": "#c62828",
    "CRITICAL": "#6a1b9a",
}

_LOG_LEVEL_ORDER = {
    "DEBUG": 0,
    "INFO": 1,
    "WARNING": 2,
    "ERROR": 3,
    "CRITICAL": 4,
}


def _render_log_into(container, all_entries, threshold):
    filtered = [
        e for e in all_entries
        if _LOG_LEVEL_ORDER.get(e.get("level", "INFO"), 0) >= threshold
    ]

    if not filtered:
        container.caption("No entries. Run a query to see output.")
        return

    rows_html = []
    for e in filtered:
        colour = _LOG_LEVEL_COLOURS.get(e.get("level", "INFO"), "#ffffff")
        level = e.get("level", "INFO")
        ts = e.get("ts", "")
        msg = str(e.get("message", ""))
        msg = msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        rows_html.append(
            f'<span style="color:#78909c">[{ts}]</span> '
            f'<span style="color:{colour};font-weight:bold">[{level}]</span><br>{msg}'
        )

    html = (
        '<div style="border:1px solid #e0e0e0;border-radius:6px;'
        'padding:10px 12px;font-family:monospace;font-size:11px;'
        'line-height:1.7;max-height:500px;overflow-y:auto;white-space:pre-wrap;">'
        + "<br>".join(rows_html)
        + "</div>"
    )

    container.markdown(html, unsafe_allow_html=True)



log_area = None      # set inside sidebar when show_logs is active
_log_threshold = 1   # default: INFO

# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
    st.session_state.user_email = None
    st.session_state.session_id = None

if "messages" not in st.session_state:
    st.session_state.messages = []  # list[dict(role, content)]

if "show_logs" not in st.session_state:
    st.session_state.show_logs = False

if "log_entries" not in st.session_state:
    st.session_state.log_entries = []  # list[dict] — persists across reruns

if "viz_snapshot" not in st.session_state:
    # Snapshot of VizBuffer captured after each agent turn so the right panel
    # persists across re-runs triggered by subsequent chat inputs.
    st.session_state.viz_snapshot = {"type": None, "data": None, "title": None, "table": None}
    from src.config import DEFAULT_MODEL_ALIAS
    os.environ.setdefault("HF_MODEL_ID", DEFAULT_MODEL_ALIAS)

# ---------------------------------------------------------------------------
# Authentication gate
# ---------------------------------------------------------------------------

from src.utils.auth import is_configured as auth_configured  # noqa: E402

if auth_configured() and not st.session_state.authenticated:
    from src.utils import auth  # noqa: E402

    st.title("🐦 eBird Birding Assistant")
    login_tab, signup_tab, confirm_tab = st.tabs(["Sign In", "Sign Up", "Verify Email"])

    with login_tab:
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign In", use_container_width=True)
            if submitted and email and password:
                result = auth.sign_in(email, password)
                if result["success"]:
                    st.session_state.authenticated = True
                    st.session_state.user_email = result["email"]
                    st.session_state.session_id = str(uuid.uuid4())
                    st.rerun()
                else:
                    st.error(result["error"])

    with signup_tab:
        with st.form("signup_form"):
            new_email = st.text_input("Email", key="signup_email")
            new_password = st.text_input("Password", type="password", key="signup_pw")
            st.caption("Min 8 chars, uppercase, lowercase, number, and symbol required.")
            signed_up = st.form_submit_button("Create Account", use_container_width=True)
            if signed_up and new_email and new_password:
                result = auth.sign_up(new_email, new_password)
                if result["success"]:
                    st.success("Account created! Check your email for a verification code.")
                else:
                    st.error(result["error"])

    with confirm_tab:
        with st.form("confirm_form"):
            conf_email = st.text_input("Email", key="conf_email")
            conf_code = st.text_input("Verification Code")
            confirmed = st.form_submit_button("Verify", use_container_width=True)
            if confirmed and conf_email and conf_code:
                result = auth.confirm_sign_up(conf_email, conf_code)
                if result["success"]:
                    st.success("Email verified! You can now sign in.")
                else:
                    st.error(result["error"])
        if st.button("Resend code", key="btn_resend"):
            if conf_email:
                auth.resend_confirmation_code(conf_email)
                st.info("Verification code re-sent.")

    st.stop()

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def _git_version() -> str:
    """Return the current git tag or short commit hash, e.g. 'v1.2.3' or 'a1b2c3d'."""
    baked = os.environ.get("BUILD_VERSION", "").strip()
    if baked:
        return baked
    import subprocess
    try:
        return subprocess.check_output(
            ["git", "describe", "--tags", "--always"],
            stderr=subprocess.DEVNULL,
            cwd=os.path.dirname(__file__) or ".",
        ).decode().strip()
    except Exception:
        return "unknown"


with st.sidebar:
    st.title("🐦 eBird Assistant")
    st.caption(f"Powered by LangChain · HuggingFace · eBird API v2 · `{_git_version()}`")

    st.divider()

    if st.button("🔄 New Conversation", use_container_width=True):
        st.session_state.messages = []
        st.session_state.viz_snapshot = {
            "type": None,
            "data": None,
            "title": None,
            "table": None,
        }
        clear_log_buffer()
        st.rerun()

    st.divider()

    if not IS_DEV and st.session_state.get("user_email"):
        try:
            from src.utils.usage_tracker import get_usage, MAX_LLM_CALLS_PER_MONTH
            _used = get_usage(st.session_state.user_email)["llm_call_count"]
            _remaining = max(0, MAX_LLM_CALLS_PER_MONTH - _used)
            st.metric(
                "Requests remaining this month",
                f"{_remaining} / {MAX_LLM_CALLS_PER_MONTH}",
            )
            st.progress(min(_used / MAX_LLM_CALLS_PER_MONTH, 1.0))
        except Exception:
            pass
        st.divider()

    if IS_DEV:
        show_logs = st.checkbox("🪵 Show log pane", value=False)

        if show_logs:
            level_options = ["DEBUG", "INFO", "WARNING", "ERROR"]
            selected_level = st.selectbox(
                "Min level",
                options=level_options,
                index=1,
            )

            if st.button("🗑️ Clear logs", use_container_width=True):
                clear_log_buffer()
                st.session_state.log_entries = []
                st.rerun()

            threshold = _LOG_LEVEL_ORDER.get(selected_level, 0)
            log_display = st.empty()
            _render_log_into(
                log_display,
                st.session_state.log_entries,
                threshold,
            )

        st.divider()

    st.markdown(
        """
        **Example queries**
        - Show recent bird sightings near lat 48.85, lng 2.35
        - Map notable birds near lat 51.5, lng -0.12 in the last 14 days
        - Historic observations for US-NY on 2024-05-01
        - Find hotspots within 10 km of lat 40.71, lng -74.01
        """
    )

# -----------------------------------------------------------------------------
# Session state
# -----------------------------------------------------------------------------

if "messages" not in st.session_state:
    st.session_state.messages = []

if "log_entries" not in st.session_state:
    st.session_state.log_entries = []

if "viz_snapshot" not in st.session_state:
    st.session_state.viz_snapshot = {
        "type": None,
        "data": None,
        "title": None,
        "table": None,
    }

# -----------------------------------------------------------------------------
# Layout
# -----------------------------------------------------------------------------

chat_col, viz_col = st.columns([0.58, 0.42], gap="medium")

# ---------------------------------------------------------------------------
# Right column — visualization panel (rendered first so it stays persistent)
# ---------------------------------------------------------------------------

with viz_col:
    st.subheader("Visualization")

    snap = st.session_state.viz_snapshot
    _viz_state_label = st.empty()
    _viz_state_label.caption(f"Loaded: {snap['type'] or 'none'}")
    _viz_content = st.empty()

    def _render_viz_snap(container, snap):
        """Render the current viz snapshot into *container*."""
        if snap["type"] == "map":
            if snap["data"] is not None:
                from streamlit_folium import st_folium
                with container.container():
                    st_folium(
                        snap["data"],
                        height=480,
                        use_container_width=True,
                        returned_objects=[],  # prevent map interactions from triggering reruns
                    )
                    if snap.get("table"):
                        import pandas as pd
                        st.caption(f"{len(snap['table'])} sightings (same as map)")
                        st.dataframe(
                            pd.DataFrame(snap["table"]),
                            use_container_width=True,
                            hide_index=True,
                        )
            else:
                container.error("Map data is empty.")

        elif snap["type"] == "chart":
            try:
                import plotly.graph_objects as go
                fig = go.Figure(snap["data"])
                container.plotly_chart(fig, use_container_width=True)
            except Exception as exc:
                container.error(f"Could not render chart: {exc}")

        elif snap["type"] == "dataframe":
            if snap.get("data"):
                import pandas as pd
                df = pd.DataFrame(snap["data"])
                with container.container():
                    if snap.get("title"):
                        st.caption(snap["title"])
                    st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                container.error("Dataframe data is empty.")

        else:
            container.info(
                "Ask the assistant about bird sightings and a map or chart will appear here."
            )

    _render_viz_snap(_viz_content, snap)

# -----------------------------------------------------------------------------
# Left column — chat panel
# -----------------------------------------------------------------------------

with chat_col:
    st.subheader("Chat")

    history_container = st.container(height=600, border=False)

    with history_container:
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        # Auto-scroll to bottom on every rerun
        st.components.v1.html(
            """
            <script>
            (function () {
                let el = window.frameElement;
                for (let i = 0; i < 15 && el; i++) {
                    el = el.parentElement;
                    if (el && el.scrollHeight > el.clientHeight) {
                        el.scrollTop = el.scrollHeight;
                        break;
                    }
                }
            })();
            </script>
            """,
            height=0,
        )

    user_input = st.chat_input(
        "Ask about birds, regions, sightings, hotspots…"
    )

    if user_input:
        import src.utils.state as _state
        import src.agent as _agent_mod

        # -------------------------------------------------------------
        # Reset visualization state immediately
        # -------------------------------------------------------------

        _state.clear_viz_buffer()
        # Bump the turn counter so leftover obs caches from prior turns are
        # treated as stale by the viz tools' fallback loader.
        _state.start_new_turn()

        st.session_state.viz_snapshot = {
            "type": None,
            "data": None,
            "title": None,
            "table": None,
        }
        
        viz_state_label = st.empty()
        viz_content=st.empty()
        viz_state_label.caption("Loaded: none")
        viz_content.info(
            "Ask the assistant about bird sightings and a map or chart will appear here."
        )

        response = ""

        # -------------------------------------------------------------
        # Status widget moved to Visualization pane
        # (generic processing + live tool labels)
        # -------------------------------------------------------------

        # Reuse the existing visualization placeholder so status appears
        # inside the viz pane and is later replaced by the actual visualization.
        status_container = viz_content

        try:
            with status_container.container():
                with st.status(
                    "🔄 Processing your request...",
                    expanded=False,
                ) as status:

                    # Immediate generic state right after Enter is pressed
                    status.update(
                        label="🔄 Processing your request...",
                        state="running",
                    )

                    for event in _agent_mod.stream_agent(
                        user_input,
                        history=st.session_state.messages,
                    ):

                        # ---------------------------------------------
                        # Tool started → update flywheel label
                        # ---------------------------------------------
                        if event["type"] == "tool_start":
                            status.update(
                                label=f"⚙️ {event['label']}",
                                state="running",
                            )

                        # ---------------------------------------------
                        # Tool finished → snapshot visualization
                        # ---------------------------------------------
                        elif event["type"] == "tool_end":
                            if (
                                _state.VizBuffer["type"] is not None
                                and _state.VizBuffer["data"]
                                is not st.session_state.viz_snapshot["data"]
                            ):
                                st.session_state.viz_snapshot = {
                                    "type": _state.VizBuffer["type"],
                                    "data": _state.VizBuffer["data"],
                                    "title": _state.VizBuffer["title"],
                                    "table": _state.VizBuffer.get("table"),
                                }

                        # ---------------------------------------------
                        # Final LLM response
                        # ---------------------------------------------
                        elif event["type"] == "final":
                            response = event["content"]

                        # ---------------------------------------------
                        # Persist logs during stream
                        # ---------------------------------------------
                        new_logs = list(LogBuffer)
                        if new_logs:
                            st.session_state.log_entries.extend(new_logs)
                            clear_log_buffer()

                    # ---------------------------------------------
                    # Final complete state
                    # ---------------------------------------------
                    status.update(
                        label="✅ Complete",
                        state="complete",
                    )

        except Exception as exc:
            response = f"⚠️ An error occurred: {exc}"

        # -------------------------------------------------------------
        # Final safety snapshot
        # -------------------------------------------------------------

        if (
            _state.VizBuffer["type"] is not None
            and _state.VizBuffer["data"]
            is not st.session_state.viz_snapshot["data"]
        ):
            st.session_state.viz_snapshot = {
                "type": _state.VizBuffer["type"],
                "data": _state.VizBuffer["data"],
                "title": _state.VizBuffer["title"],
                "table": _state.VizBuffer.get("table"),
            }

        # -------------------------------------------------------------
        # Save messages
        # -------------------------------------------------------------

        st.session_state.messages.append(
            {"role": "user", "content": user_input}
        )

        st.session_state.messages.append(
            {"role": "assistant", "content": response}
        )

        clear_log_buffer()
        st.rerun()
