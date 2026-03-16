"""
Settings page — configuration, system info, API docs link.
"""

import os

import streamlit as st

from ui import api_client as api
from ui.theme import page_header, section_divider


def render():
    page_header("Settings", "System configuration and connection details")

    # ── User identity ─────────────────────────────────────
    st.markdown("#### User Identity")
    st.session_state["username"] = st.text_input(
        "Your username (used for audit logs)",
        value=st.session_state.get("username", os.environ.get("USER", os.environ.get("USERNAME", "user"))),
        key="settings_username",
    )

    section_divider()

    # ── API Connection ────────────────────────────────────
    st.markdown("#### API Connection")

    h = api.health()

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**Endpoint:** `{api.API_URL}`")
        if h:
            st.success(f"Status: **{h.get('status', 'unknown').upper()}** | v{h.get('version', '?')}")
        else:
            st.error("Cannot reach API server")

    with col2:
        if h:
            st.markdown(f"**MongoDB:** `{h.get('mongo', 'unknown')}`")
            st.markdown(f"**Platform:** `{h.get('platform', 'unknown')}`")

    if h:
        st.markdown(f"[📖 Open API Documentation]({api.API_URL}/docs)")

    section_divider()

    # ── Registered job types ──────────────────────────────
    st.markdown("#### Registered Pipelines")

    if h:
        types = h.get("registered_job_types", [])
        for jt in types:
            category = "Land Records" if "uc1" in jt else "Photo Verification" if "uc2" in jt else "System"
            icon = "📄" if "uc1" in jt else "🌳" if "uc2" in jt else "⚙️"
            st.markdown(f"- {icon}  **`{jt}`** — _{category}_")
    else:
        st.caption("Connect to API to see registered pipelines.")

    section_divider()

    # ── Environment ───────────────────────────────────────
    st.markdown("#### Environment")

    env_vars = {
        "API_URL": os.environ.get("API_URL", "(default: http://localhost:8000)"),
        "API_TIMEOUT": os.environ.get("API_TIMEOUT", "(default: 30)"),
        "MONGO_URI": os.environ.get("MONGO_URI", "(default: mongodb://localhost:27017)"),
        "MONGO_DB": os.environ.get("MONGO_DB", "(default: f4f_poc)"),
        "CXAI_API_KEY": "***" if os.environ.get("CXAI_API_KEY") else "(not set)",
    }

    for k, v in env_vars.items():
        st.markdown(f"- `{k}` = `{v}`")
