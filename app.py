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
import uuid

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from src.utils.logging_config import setup_logging, LogBuffer, clear_log_buffer, get_log_entries  # noqa: E402
setup_logging()

st.set_page_config(
    page_title="eBird Birding Assistant",
    page_icon="🐦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Maximum number of user turns allowed per conversation before requiring a reset.
MAX_TURNS = 20

# ---------------------------------------------------------------------------
# Log pane helpers — defined at module level so the streaming loop can reach them
# ---------------------------------------------------------------------------

_LOG_LEVEL_COLOURS = {
    "DEBUG":    "#888888",
    "INFO":     "#0277bd",
    "WARNING":  "#e65100",
    "ERROR":    "#c62828",
    "CRITICAL": "#6a1b9a",
    "TOOL_IN":  "#2e7d32",
    "TOOL_OUT": "#558b2f",
    "LLM_OUT":  "#6a1b9a",
}
_LOG_LEVEL_ORDER = {
    "DEBUG": 0, "INFO": 1, "TOOL_IN": 1, "TOOL_OUT": 1,
    "LLM_OUT": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4,
}


def _render_log_into(container, all_entries: list, threshold: int) -> None:
    """Render *all_entries* filtered by *threshold* into a Streamlit container."""
    filtered = [e for e in all_entries if _LOG_LEVEL_ORDER.get(e["level"], 0) >= threshold]
    if filtered:
        rows_html = []
        for e in filtered:
            colour = _LOG_LEVEL_COLOURS.get(e["level"], "#ffffff")
            level_badge = f'<span style="color:{colour};font-weight:bold">[{e["level"]:<8}]</span>'
            ts_span = f'<span style="color:#78909c">[{e["ts"]}]</span>'
            msg = (
                e["message"]
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            rows_html.append(f"{ts_span} {level_badge}<br>{msg}")
        log_html = (
            '<div style="'
            "border:1px solid #e0e0e0;border-radius:6px;padding:10px 12px;"
            "font-family:monospace;font-size:11px;line-height:1.7;"
            "max-height:500px;overflow-y:auto;white-space:pre-wrap;"
            '">' + "<br>".join(rows_html) + "</div>"
        )
        container.markdown(log_html, unsafe_allow_html=True)
    else:
        container.caption("No entries. Run a query to see output.")


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
                    # Track session start
                    try:
                        from src.utils.usage_tracker import increment_session
                        increment_session(result["email"])
                    except Exception:
                        pass
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

with st.sidebar:
    st.title("🐦 eBird Assistant")
    st.caption("Powered by LangChain · HuggingFace · eBird API v2")

    # --- Authenticated user info & sign-out ---
    if st.session_state.authenticated:
        st.caption(f"Signed in as **{st.session_state.user_email}**")
        if st.button("🚪 Sign Out", use_container_width=True, key="btn_sign_out"):
            st.session_state.authenticated = False
            st.session_state.user_email = None
            st.session_state.session_id = None
            st.session_state.messages = []
            st.rerun()

        # --- Monthly usage display ---
        try:
            from src.utils.usage_tracker import get_usage, MAX_SESSIONS_PER_MONTH, MAX_PROMPTS_PER_MONTH
            _usage = get_usage(st.session_state.user_email)
            st.caption(
                f"This month: {_usage['session_count']}/{MAX_SESSIONS_PER_MONTH} sessions · "
                f"{_usage['prompt_count']}/{MAX_PROMPTS_PER_MONTH} prompts"
            )
        except Exception:
            pass

    st.divider()

    ebird_ok = bool(os.environ.get("EBIRD_API_KEY"))
    hf_token_ok = bool(os.environ.get("HUGGINGFACE_API_TOKEN"))
    llm_ok = hf_token_ok

    st.divider()

    if st.button("🔄 New Conversation", use_container_width=True, key="btn_new_conversation"):
        st.session_state.messages = []
        st.session_state.viz_snapshot = {"type": None, "data": None, "title": None, "table": None}
        clear_log_buffer()
        st.session_state.log_entries = []
        # Track new session
        if st.session_state.authenticated:
            st.session_state.session_id = str(uuid.uuid4())
            try:
                from src.utils.usage_tracker import increment_session
                result = increment_session(st.session_state.user_email)
                if not result["allowed"]:
                    st.warning(
                        f"You've reached the **{result['limit']} session/month** limit. "
                        "Please try again next month."
                    )
            except Exception:
                pass
        # Clear agent memory without re-importing to avoid re-building the LLM
        try:
            import src.agent as _agent_mod
            _agent_mod.reset_agent()
        except Exception:
            pass
        st.rerun()

    st.divider()
    st.checkbox("🪵 Show log pane", key="show_logs")

    if st.session_state.get("show_logs", False):
        _LEVEL_OPTIONS = ["DEBUG", "INFO", "WARNING", "ERROR"]

        selected_level = st.selectbox(
            "Min level",
            options=_LEVEL_OPTIONS,
            index=1,
            key="log_level_filter",
        )
        if st.button("🗑️ Clear logs", use_container_width=True, key="btn_clear_log"):
            clear_log_buffer()
            st.session_state.log_entries = []
            st.rerun()

        _log_threshold = _LOG_LEVEL_ORDER.get(selected_level, 0)
        log_area = st.empty()
        _render_log_into(log_area, st.session_state.log_entries, _log_threshold)

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
    st.caption(f"Loaded: {snap['type'] or 'none'}")

    if snap["type"] == "map":
        if snap["data"] is not None:
            from streamlit_folium import st_folium
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
            st.error("Map data is empty.")

    elif snap["type"] == "chart":
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

    if not (ebird_ok and llm_ok):
        st.warning(
            "One or more API keys are missing. "
            "Copy `.env.example` to `.env` and fill in your keys, then restart the app.",
            icon="⚠️",
        )

    turn_count = sum(1 for m in st.session_state.messages if m["role"] == "user")
    limit_reached = turn_count >= MAX_TURNS
    if limit_reached:
        st.warning(
            f"You've reached the **{MAX_TURNS}-message limit** for this conversation. "
            "Click **🔄 New Conversation** in the sidebar to start a fresh session.",
            icon="🛑",
        )

    # Chat input at the top
    user_input = st.chat_input(
        "Ask about birds, regions, sightings, hotspots…",
        disabled=not (ebird_ok and llm_ok) or limit_reached,
    )

    # Scrollable chat history below the input
    history_container = st.container(height=600, border=False)
    with history_container:
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        # Auto-scroll to the latest message on every rerun.
        # The iframe created by components.html sits inside the scrollable container;
        # walking up the DOM to the first overflowing ancestor and scrolling it works
        # regardless of Streamlit's internal class names.
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

    if user_input:
        import src.utils.state as _state
        import src.agent as _agent_mod

        # --- Prompt-level rate limiting ---
        _prompt_allowed = True
        if st.session_state.authenticated:
            try:
                from src.utils.usage_tracker import increment_prompt
                _prompt_result = increment_prompt(st.session_state.user_email)
                if not _prompt_result["allowed"]:
                    _prompt_allowed = False
                    st.warning(
                        f"You've reached the **{_prompt_result['limit']} prompt/month** limit. "
                        "Please try again next month.",
                        icon="🛑",
                    )
            except Exception:
                pass  # fail open

        if not _prompt_allowed:
            st.stop()

        _state.clear_viz_buffer()
        # Drop stale table immediately so it doesn't persist during intermediate
        # re-renders while the agent is working on the new map.
        st.session_state.viz_snapshot["table"] = None
        response = ""

        # Stream the agent, showing live progress via st.status
        with st.status("Working…", expanded=True) as status:
            try:
                for event in _agent_mod.stream_agent(user_input, history=st.session_state.messages):
                    if event["type"] == "tool_start":
                        st.write(event["label"])
                    elif event["type"] == "tool_end":
                        # Snapshot VizBuffer immediately after each tool completes
                        # so we capture it even if the final text event is missing.
                        # Only update if the viz data actually changed (new object identity)
                        # to avoid re-rendering stale visuals.
                        if (
                            _state.VizBuffer["type"] is not None
                            and _state.VizBuffer["data"] is not st.session_state.viz_snapshot["data"]
                        ):
                            st.session_state.viz_snapshot = {
                                "type": _state.VizBuffer["type"],
                                "data": _state.VizBuffer["data"],
                                "title": _state.VizBuffer["title"],
                                "table": _state.VizBuffer.get("table"),
                            }
                        output = event.get("output", "")
                        if output:
                            with st.expander(f"↳ {event['name']} result", expanded=False):
                                st.text(output)
                        st.write("✓ Done")
                    elif event["type"] == "final":
                        response = event["content"]
                    # Live-drain log buffer so the sidebar pane updates incrementally
                    _new_logs = list(LogBuffer)
                    if _new_logs:
                        st.session_state.log_entries.extend(_new_logs)
                        clear_log_buffer()
                        if log_area is not None:
                            _render_log_into(log_area, st.session_state.log_entries, _log_threshold)
                status.update(label="Done", state="complete", expanded=False)
            except Exception as exc:
                response = f"⚠️ An error occurred: {exc}"
                status.update(label="Error", state="error", expanded=True)

        # Final safety-net capture after full stream
        if (
            _state.VizBuffer["type"] is not None
            and _state.VizBuffer["data"] is not st.session_state.viz_snapshot["data"]
        ):
            st.session_state.viz_snapshot = {
                "type": _state.VizBuffer["type"],
                "data": _state.VizBuffer["data"],
                "title": _state.VizBuffer["title"],
                "table": _state.VizBuffer.get("table"),
            }

        st.session_state.messages.append({"role": "user", "content": user_input})
        st.session_state.messages.append({"role": "assistant", "content": response})

        # Flush new log entries from the in-memory buffer into session_state so
        # they survive the upcoming rerun (LogBuffer is module-level and may not
        # persist across Streamlit worker restarts).
        st.session_state.log_entries.extend(list(LogBuffer))
        clear_log_buffer()

        # Persist log entries for the completed turn to DynamoDB for later inspection.
        if st.session_state.log_entries:
            try:
                from src.utils.usage_tracker import flush_session_logs
                flush_session_logs(
                    user_id=st.session_state.get("user_email") or "anonymous",
                    session_id=st.session_state.get("session_id") or "",
                    entries=st.session_state.log_entries,
                )
            except Exception:
                pass  # never block the UI on logging failures

        # Rerun to refresh both chat history and viz panel
        st.rerun()
