"""
F4F Unified UI — Main Application

Single Streamlit app with left-sidebar navigation.
All operations go through the FastAPI backend.

Run:   streamlit run ui/app.py --server.port 8501
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from ui import api_client as api
from ui.theme import inject_css, sidebar_brand
from ui.views import home, uc1, uc2, jobs, audit, settings

# ─── Page Registry ────────────────────────────────────────────────────

PAGES = [
    ("📊  Dashboard",           home),
    ("📄  Land Records",        uc1),
    ("🌳  Photo Verification",  uc2),
    ("⚡  Jobs",                jobs),
    ("📋  Audit Logs",          audit),
    ("⚙️  Settings",            settings),
]


def main():
    st.set_page_config(
        page_title="Farmers for Forests",
        page_icon="🌳",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    inject_css()

    # ── Sidebar ───────────────────────────────────────────
    with st.sidebar:
        h = api.health()
        sidebar_brand(api_ok=h is not None and h.get("status") == "ok")

        # Navigation radio
        labels = [label for label, _ in PAGES]
        choice = st.radio(
            "Navigation",
            labels,
            index=0,
            key="nav",
            label_visibility="collapsed",
        )

        st.markdown("---")

        # Username
        if "username" not in st.session_state:
            st.session_state["username"] = os.environ.get(
                "USER", os.environ.get("USERNAME", "user")
            )
        st.session_state["username"] = st.text_input(
            "👤 Username", value=st.session_state["username"], key="sidebar_user"
        )

        # Quick stats
        st.markdown("---")
        jd = api.list_jobs(limit=1)
        c = jd.get("counts", {}) if jd else {}
        running = c.get("running", 0)
        failed = c.get("failed", 0)
        if running:
            st.markdown(f"🔵 **{running}** running")
        if failed:
            st.markdown(f"🔴 **{failed}** failed")
        if not running and not failed:
            st.caption("No active jobs")

    # ── Render page ───────────────────────────────────────
    idx = labels.index(choice)
    _, module = PAGES[idx]
    module.render()


if __name__ == "__main__":
    main()
