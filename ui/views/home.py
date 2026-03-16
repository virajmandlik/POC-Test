"""
Home / Dashboard — friendly overview with short metric labels.
"""

from datetime import datetime

import pandas as pd
import streamlit as st

from ui import api_client as api
from ui.theme import page_header, section_divider


def render():
    page_header("Dashboard", "System overview and recent activity")

    h = api.health()

    # ── Metric cards (short labels to avoid truncation) ───
    jd = api.list_jobs(limit=1)
    c = jd.get("counts", {}) if jd else {}

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total", c.get("total", 0))
    m2.metric("Pending", c.get("pending", 0))
    m3.metric("Running", c.get("running", 0))
    m4.metric("Done", c.get("completed", 0))
    m5.metric("Failed", c.get("failed", 0))

    section_divider()

    # ── Two columns ───────────────────────────────────────
    left, right = st.columns([3, 2], gap="large")

    with left:
        st.markdown("##### Recent Jobs")
        jd = api.list_jobs(limit=8)
        jobs = jd.get("jobs", []) if jd else []
        if jobs:
            rows = []
            for j in jobs:
                status = j.get("status", "")
                rows.append({
                    "Status": status.upper(),
                    "Type": j.get("job_type", ""),
                    "User": j.get("user", ""),
                    "When": _relative_time(j.get("created_at")),
                })
            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True,
                hide_index=True,
                height=320,
            )
        else:
            st.info("No jobs yet — go to **Land Records** or **Photo Verification** to start.")

    with right:
        st.markdown("##### System")
        if h:
            c1, c2 = st.columns(2)
            c1.metric("API", f"v{h.get('version', '?')}")
            c2.metric("Mongo", "✓" if h.get("mongo") == "connected" else "✗")
            st.caption(f"Platform: `{h.get('platform', '?')}`")
        else:
            st.error("API offline")

        st.markdown("")
        st.markdown("##### Pipelines")
        if h:
            for jt in h.get("registered_job_types", []):
                icon = "📄" if "uc1" in jt else "🌳" if "uc2" in jt else "⚙️"
                st.caption(f"{icon}  `{jt}`")

    section_divider()

    # ── Activity feed ─────────────────────────────────────
    st.markdown("##### Recent Activity")
    ad = api.get_audit_logs(limit=6)
    logs = ad.get("logs", []) if ad else []
    if logs:
        for entry in logs:
            ts = _relative_time(entry.get("timestamp"))
            action = entry.get("action", "")
            user = entry.get("user", "")
            lvl = entry.get("level", "info")
            icon = {"info": "ℹ️", "warn": "⚠️", "error": "🔴"}.get(lvl, "ℹ️")
            st.markdown(f"{icon}  **{action}** — *{user}* · {ts}")
    else:
        st.caption("No recent activity.")


def _relative_time(iso_str: str | None) -> str:
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        delta = datetime.now().astimezone() - dt if dt.tzinfo else datetime.now() - dt
        secs = delta.total_seconds()
        if secs < 60:
            return "just now"
        if secs < 3600:
            return f"{int(secs // 60)}m ago"
        if secs < 86400:
            return f"{int(secs // 3600)}h ago"
        return f"{int(secs // 86400)}d ago"
    except Exception:
        return str(iso_str)[:16]
