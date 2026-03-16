"""
Audit Log viewer page.
"""

import json
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from ui import api_client as api
from ui.theme import page_header, section_divider


def render():
    page_header("Audit Trail", "Search and review all system activity logs")

    # ── Filters ───────────────────────────────────────────
    with st.expander("Filters", expanded=True):
        fc1, fc2, fc3, fc4 = st.columns(4)
        with fc1:
            log_action = st.text_input("Action (regex)", key="audit_action")
        with fc2:
            log_user = st.text_input("User", key="audit_user")
        with fc3:
            log_level = st.selectbox("Level", ["All", "info", "warn", "error"], key="audit_level")
        with fc4:
            log_limit = st.number_input("Max results", min_value=10, max_value=1000, value=100, key="audit_limit")

        dc1, dc2 = st.columns(2)
        with dc1:
            log_since = st.date_input("Since", value=datetime.now() - timedelta(days=7), key="audit_since")
        with dc2:
            log_until = st.date_input("Until", value=datetime.now(), key="audit_until")

    params: dict = {"limit": log_limit}
    if log_action:
        params["action"] = log_action
    if log_user:
        params["user"] = log_user
    if log_level != "All":
        params["level"] = log_level
    if log_since:
        params["since"] = log_since.isoformat()
    if log_until:
        params["until"] = log_until.isoformat()

    data = api.get_audit_logs(**params)
    if not data:
        return

    logs = data.get("logs", [])
    total = data.get("total", 0)

    st.caption(f"Showing {len(logs)} of {total} entries")

    if not logs:
        st.info("No audit logs found matching filters.")
        return

    # ── Table ─────────────────────────────────────────────
    rows = []
    for entry in logs:
        rows.append({
            "Timestamp": _fmt_time(entry.get("timestamp")),
            "Level": entry.get("level", "").upper(),
            "Action": entry.get("action", ""),
            "User": entry.get("user", ""),
            "Job ID": (entry.get("job_id") or "—")[:12],
            "Detail": _trunc(json.dumps(entry.get("detail", {}), default=str), 80),
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, height=500, hide_index=True)

    section_divider()

    # ── Detail viewer ─────────────────────────────────────
    st.markdown("#### Log Detail")

    selected_idx = st.number_input(
        "Select row (0-indexed)", min_value=0, max_value=max(0, len(logs) - 1), value=0, key="audit_detail_idx"
    )

    if 0 <= selected_idx < len(logs):
        entry = logs[selected_idx]
        col_meta, col_detail = st.columns([1, 2])
        with col_meta:
            st.markdown(f"**Action:** `{entry.get('action')}`")
            st.markdown(f"**User:** `{entry.get('user')}`")
            st.markdown(f"**Level:** `{entry.get('level')}`")
            st.markdown(f"**Job ID:** `{entry.get('job_id', '—')}`")
            st.markdown(f"**Time:** `{entry.get('timestamp')}`")
        with col_detail:
            st.json(entry)


# ─── Helpers ──────────────────────────────────────────────────────────


def _fmt_time(iso_str: str | None) -> str:
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        return dt.strftime("%b %d %H:%M:%S")
    except Exception:
        return str(iso_str)[:19]


def _trunc(s: str, max_len: int) -> str:
    return s[:max_len] + "…" if len(s) > max_len else s
